"""Build a time sequence for a sky position.

search (IRSA TAP) -> cutouts straight from S3 (cached per file, ADR-0037) -> mask, background, align on a common grid (worker
processes) -> group into epochs -> co-add per epoch and detector -> forced photometry for spectra and
light curves -> write the result (pipeline.results).

Co-moving mode (solar-system objects, spherex_core.moving): frames come from IRSA MOST instead of the
footprint search, and every exposure is resampled on its own grid centred on the object at that time.
"""

from __future__ import annotations

import asyncio
import logging
import math
import multiprocessing as mp
import os
import shutil
import threading
import time
import warnings
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from typing import Callable

import numpy as np

from .. import cache, logs
from ..archive import Frame, query_frames
from ..bands import NATIVE_PIXSCALE
from ..config import settings
from ..flags import merge_flag_defs
from ..io import Level2Reader, RangeClient
from ..moving import frames_for_object, patch_comoving_meta
from ..photometry import VARIANCE_RADIUS
from ..processing import Grid, group_epochs, make_grid
from ..targets import Target
from ..timeutil import year_to_mjd
from . import results
from .assemble import assemble
from .fetch import (BatchResult, FetchSpec, Outcome, batches, merge_net, read_batch, run_batch, set_irsa_gate,
                    warm_worker)
from .params import SequenceParams

log = logging.getLogger(__name__)
Progress = Callable[[str, int, int], None]
SEARCH_EPOCH = 2026.0  # where to place a moving target for the footprint search (images are 3.5 deg wide)
_POOL: ProcessPoolExecutor | None = None
# BLAS/OpenMP libraries start one thread per host core in every pool worker (64+ on the dev box), which
# exhausted the container's thread limit and broke the pool. Each worker is one process doing one frame.
_SINGLE_THREAD_ENV = ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")


def cutout_half(grid: Grid) -> int:
    """Half-size in native pixels of a detector cutout that covers the grid at any rotation."""
    return int(math.ceil(grid.n * grid.pixscale / NATIVE_PIXSCALE * 0.5 * 1.42)) + 3


def _worker_init(irsa_gate=None) -> None:
    for name in _SINGLE_THREAD_ENV:
        os.environ[name] = "1"
    warnings.filterwarnings("ignore")
    set_irsa_gate(irsa_gate)


def cpu_pool() -> ProcessPoolExecutor:
    """Process pool shared by all builds: per-frame work is Python-heavy (WCS, photometry), so threads
    would serialize on the GIL. A pool that broke (a worker died) is replaced instead of failing every
    later build."""
    global _POOL
    if _POOL is not None and getattr(_POOL, "_broken", False):
        log.warning("process pool was broken; starting a new one")
        reset_pool()
    if _POOL is None:
        # spawned workers read these at import time (before the initializer runs), so set them here
        for name in _SINGLE_THREAD_ENV:
            os.environ.setdefault(name, "1")
        ctx = mp.get_context("spawn")
        # IRSA's cutout service turns most requests away above ~8 at once, so its slots are shared by all workers.
        gate = ctx.BoundedSemaphore(max(1, settings.irsa_max_in_flight))
        _POOL = ProcessPoolExecutor(max_workers=settings.cpu_workers, mp_context=ctx, initializer=_worker_init,
                                    initargs=(gate,))
    return _POOL


def warm_pool() -> None:
    """Spawn every worker of the pool now, in the background, so the first build after a start does not wait for
    them (~1.5 s for 29 workers importing astropy on the dev server). Called by the API and the build worker."""
    if not settings.fetch_workers:
        return
    pool = cpu_pool()
    for _ in range(settings.cpu_workers):
        pool.submit(warm_worker)


def _write_frames(result_dir: Path, frames) -> None:
    """Every Frame of the build next to its result (frames.json, read by analysis.common.sequence_frames), so
    analyses of a finished sequence never search IRSA again."""
    import json
    from dataclasses import asdict

    tmp = result_dir / "frames.json.part"
    tmp.write_text(json.dumps({f.file_name: asdict(f) for f in frames}))
    tmp.replace(result_dir / "frames.json")


def reset_pool() -> None:
    """Drop the shared pool (without waiting for it); the next cpu_pool() call starts a fresh one."""
    global _POOL
    old, _POOL = _POOL, None
    if old is not None:
        old.shutdown(wait=False, cancel_futures=True)


async def run_in_pool(fn, *args):
    """Run fn(*args) in the shared pool; if the pool broke, replace it and retry once."""
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(cpu_pool(), fn, *args)
    except BrokenProcessPool:
        reset_pool()
        return await loop.run_in_executor(cpu_pool(), fn, *args)


PREVIEW_MIN_FRAMES = 40  # smaller builds are quick enough without previews
PREVIEW_STEPS = (1 / 3, 2 / 3)  # later previews once this share of the exposures is in finished epochs


class _Plan:
    """Which exposures to read first and when a preview is worth writing.

    Epochs are planned from the archive times (the build regroups them from MJD-AVG later; they agree but for
    rounding): the first full epoch, where the viewer opens, goes first, then every other epoch in time order.
    """

    def __init__(self, frames: list[Frame], p: SequenceParams):
        mjd = np.array([f.mjd for f in frames], dtype=float)
        ep, partial = group_epochs(mjd, p.gap_days, p.max_epoch_days)
        self.epoch = ep
        full = [e for e in range(len(partial)) if not partial[e]]
        self.opening = full[0] if full else 0
        rank = {e: (0 if e == self.opening else 1, e) for e in range(len(partial))}
        self.order = sorted(range(len(frames)), key=lambda i: (rank[int(ep[i])], frames[i].band, mjd[i]))
        self.left = {e: int((ep == e).sum()) for e in range(len(partial))}
        self.n = len(frames)
        self.n_opening = self.left.get(self.opening, 0)  # the first n_opening of `order` are the opening epoch
        self.key = p.key()
        self.enabled = self.n >= PREVIEW_MIN_FRAMES and len(partial) > 1
        self.steps = list(PREVIEW_STEPS)
        self.previews = 0
        self.preview_ids: list[str] = []

    def ready(self, finished: list[int]) -> list[int] | None:
        """Note finished exposures; the exposures of the finished epochs when a preview is due, else None."""
        for i in finished:
            self.left[int(self.epoch[i])] -= 1
        if not self.enabled:
            return None
        complete = [e for e, n in self.left.items() if n == 0]
        if self.opening not in complete:
            return None
        idx = [i for i in range(self.n) if int(self.epoch[i]) in complete]
        if len(idx) >= self.n:  # everything is in: the full result follows at once
            return None
        if self.previews == 0 or (self.steps and len(idx) >= self.steps[0] * self.n):
            return idx
        return None

    def shown(self, pid: str, count: int) -> None:
        self.previews += 1
        self.preview_ids.append(pid)
        while self.steps and count >= self.steps[0] * self.n:
            self.steps.pop(0)


async def _write_preview(p: SequenceParams, target: Target, grid: Grid, frames: list[Frame], per_frame: list,
                         errors: list[str], defs_text: list[str], idx: list[int], plan: _Plan,
                         most_result, superseded: Callable[[], bool] = lambda: False) -> str | None:
    """Assemble the finished epochs into a result of their own; returns its id (None if it failed, or if the full
    result got under way meanwhile)."""
    pid = f"{plan.key}p{plan.previews + 1}"
    sub_frames = [frames[i] for i in idx]
    sub = [per_frame[i] for i in idx]
    if not any(r is not None for r in sub):
        return None
    try:
        with logs.timed(log, "preview", frames=len(idx), total=plan.n) as info:
            meta, arrays = await asyncio.to_thread(assemble, p, target, grid, sub_frames, sub, list(errors),
                                                   merge_flag_defs(defs_text[i] for i in sorted(idx)))
            if most_result is not None:
                patch_comoving_meta(meta, target, most_result)
            meta["id"] = pid
            meta["preview"] = {"of": plan.key, "n": plan.previews + 1, "frames": len(idx), "total": plan.n}
            if superseded():
                info["skipped"] = "full result under way"
                return None
            await asyncio.to_thread(results.write_result, cache.layout().sequence(pid), meta, arrays)
            info["id"] = pid
            info["epochs"] = len(meta["epochs"])
    except Exception as e:  # noqa: BLE001 - a preview is a courtesy; the build goes on without it
        log.warning("preview failed: %r", e)
        return None
    plan.shown(pid, len(idx))
    return pid


async def _ensure_tables(frames: list[Frame]) -> None:
    """Wavelength map and ePSF table of every detector in the build, on disk before the batches start."""
    first: dict[int, Frame] = {}
    for f in frames:
        first.setdefault(f.detector, f)
    root = cache.layout().root
    missing = [f for d, f in first.items() if not (cache.layout().wave(d).exists() and cache.layout().epsf(d).exists())]
    if not missing:
        return
    async with RangeClient(8) as client:
        reader = Level2Reader(client, root)
        await asyncio.gather(*(asyncio.gather(reader.wave_map(f.s3_url, f.detector),
                                              reader.epsf_table(f.s3_url, f.detector)) for f in missing))


HEAD_RELEASE = 0.125  # the other batches start when this share of the opening epoch's batches is left


async def _run_batches(specs: list[FetchSpec], parts: list[list], workers: int, first: int = 0):
    """Yield (BatchResult, submit time) as batches finish: in pool workers, or here when workers == 0. A pool that
    breaks is replaced once and its unfinished batches are submitted again. Cancelling drops queued batches.

    The first `first` batches (the opening epoch) run alone and the others start when nearly all of those are
    done: a cold build is limited by bandwidth, so the opening epoch, and with it the first preview, arrives in
    about the time its own bytes take instead of near the end of the build (ADR-0039)."""
    loop = asyncio.get_running_loop()
    todo = list(range(len(parts)))
    if not workers:
        sem = asyncio.Semaphore(max(1, settings.max_frames_in_flight // max([1, *map(len, parts)])))
        gate = threading.BoundedSemaphore(max(1, settings.irsa_max_in_flight))  # IRSA slots of the whole build

        async def inline(b: int) -> BatchResult:
            async with sem:
                started = time.time()
                res = await read_batch(specs[b], parts[b], gate=gate)
                res.started = started
                return res

    for attempt in range(2):
        futs: dict[asyncio.Future, int] = {}
        submitted: dict[int, float] = {}

        def submit(batch_ids: list[int]) -> set[asyncio.Future]:
            new: set[asyncio.Future] = set()
            for b in batch_ids:
                submitted[b] = time.time()
                fut = loop.run_in_executor(cpu_pool(), run_batch, specs[b], parts[b]) if workers \
                    else asyncio.ensure_future(inline(b))
                futs[fut] = b
                new.add(fut)
            return new

        head = [b for b in todo if b < first]
        rest = [b for b in todo if b >= first]
        release = int(len(head) * HEAD_RELEASE)
        pending = submit(head)
        if len(head) <= release:
            pending |= submit(rest)
            rest = []
        left = len(head)
        broken: list[int] = []
        try:
            while pending:
                finished, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for fut in sorted(finished, key=futs.__getitem__):  # batches finished together: in priority order
                    b = futs[fut]
                    left -= b < first
                    try:
                        res = fut.result()
                    except BrokenProcessPool:
                        broken.append(b)
                        continue
                    except Exception as e:  # noqa: BLE001 - the whole batch failed (e.g. no network)
                        res = BatchResult([Outcome(i, None, {"file": f.file_name, "band": f.band, "work": 0.0},
                                                   f"{f.file_name}: {e!r}"[:300]) for i, f in parts[b]])
                    yield res, submitted[b]
                if rest and left <= release and not broken:
                    pending |= submit(rest)
                    rest = []
        finally:
            for fut in futs:
                fut.cancel()
        if not broken and not rest:
            return
        log.warning("process pool broke with %d batches unfinished; starting a new pool", len(broken) + len(rest))
        reset_pool()
        todo = sorted(broken + rest)
    for b in todo:
        f0 = parts[b]
        yield BatchResult([Outcome(i, None, {"file": f.file_name, "band": f.band, "work": 0.0},
                                   f"{f.file_name}: process pool broke") for i, f in f0]), time.time()


async def build_sequence(p: SequenceParams, out_dir: Path, progress: Progress | None = None,
                         max_frames: int = 0, on_preview: Callable[[str], object] | None = None) -> dict:
    """Run the whole pipeline and write the result to out_dir. Returns the metadata dict.

    While exposures are read, previews are written as complete results of their own, with the id
    `<key>p<n>`: the epoch the viewer opens on first, then more finished epochs. Each is announced with
    progress("preview:<id>", done, total) and on_preview(id) (which may publish it), and all are removed once the
    full result is written.

    max_frames > 0 refuses (ValueError) positions with more exposures than that (deep fields), so one
    request cannot occupy a worker for many minutes. Cancelling the task aborts the S3 reads and drops
    queued process-pool work.

    Logs one INFO line per stage with its duration, a progress line every 10 % of the exposures (or 15 s), and a
    breakdown of where the exposure time went (logs.py, by Max0072); meta.stats.seconds and .timings keep the same.
    """
    report = progress or (lambda stage, done, total: None)
    if p.static_sky and not p.is_moving_object:
        raise ValueError("static_sky needs a solar-system object (object_name or naif_id)")
    t0 = time.perf_counter()
    target = p.target
    most_result = None  # set in co-moving mode (solar-system object)
    logs.event(log, logging.INFO, "build start", target=target.name or p.object_name, ra=round(target.ra, 5),
               dec=round(target.dec, 5), fov_arcmin=p.fov_arcmin, moving=p.object_name or None)

    report("search", 0, 0)
    with logs.timed(log, "search", collections=len(p.collections or settings.collections)) as info:
        if p.is_moving_object:
            target, frames, most_result = await asyncio.to_thread(
                frames_for_object, p.object_name, p.object_type, p.obs_begin, p.obs_end, p.naif_id, p.collections)
        else:
            ra_q, dec_q = target.position_at(year_to_mjd(SEARCH_EPOCH))
            frames = await asyncio.to_thread(query_frames, float(ra_q), float(dec_q), p.collections)
        info["found"] = len(frames)
        frames = p.select(frames)
        info["frames"] = len(frames)
    t_search = time.perf_counter() - t0
    if not frames:
        raise LookupError("No SPHEREx images cover this position yet.")
    if max_frames and len(frames) > max_frames:
        raise ValueError(f"{len(frames)} exposures cover this position; the limit is {max_frames}. "
                         "Ask the operators to prebuild it.")

    mid = float(np.median([f.mjd for f in frames]))
    ra_c, dec_c = target.position_at(mid)
    grid = make_grid(float(ra_c), float(dec_c), p.fov_arcmin)
    half = cutout_half(grid)
    # The cache keeps columns for the widest field (rows arrive full-width anyway), so widening the field
    # later reads only new rows; VARIANCE is read only where photometry looks (ADR-0037).
    keep_half = max(half, cutout_half(make_grid(grid.ra, grid.dec, settings.cutout_cache_fov, grid.pixscale)))

    per_frame: list[dict | None] = [None] * len(frames)
    timing: list[dict] = []  # per exposure: slot, read, tables, pool_wait, process, work, total (seconds)
    errors: list[str] = []
    n_frames = len(frames)
    comoving = most_result is not None
    # The epoch the viewer opens on goes first, then the others in time order; previews show finished epochs
    # while the rest is still being read (ADR-0039).
    plan = _Plan(frames, p)
    items = [(i, frames[i]) for i in plan.order]
    report("fetch", 0, n_frames)
    t_fetch0 = time.perf_counter()
    workers = settings.cpu_workers if settings.fetch_workers else 0
    use_irsa = settings.cutout_source == "irsa" and settings.irsa_max_in_flight > 0
    # The opening epoch in small batches, so that every worker reads part of it and the preview comes after about
    # one exposure's read time; then the rest in one wave of larger batches.
    head = plan.n_opening if plan.enabled else 0
    w = max(1, workers)
    first_parts = batches(items[:head], w, size=max(2, -(-head // w))) if head else []
    parts = first_parts + batches(items[head:], w)

    # Exposures in flight: max_frames_in_flight for the whole build, shared out over the workers. Starting them a
    # few at a time per batch (not all at once) lets every exposure that starts later find an IRSA slot that an
    # earlier one freed, so IRSA serves as many as its few slots allow and S3 reads the rest in parallel.
    in_flight = max(2, -(-settings.max_frames_in_flight // workers)) if workers else settings.max_frames_in_flight

    def spec(b: int, batch: list) -> FetchSpec:
        # IRSA slots are shared by every batch of the build (a semaphore across the pool's processes, or one
        # in this process), so at most irsa_max_in_flight cutout requests are open at once.
        return FetchSpec(str(cache.layout().root), grid, target, comoving, half, keep_half, VARIANCE_RADIUS,
                         connections=min(64, 3 * min(len(batch), in_flight) + 2),
                         irsa_slots=settings.irsa_max_in_flight if use_irsa else 0,
                         irsa_timeout=settings.irsa_timeout_s,
                         # the opening epoch runs alone (see _run_batches): all of a batch at once
                         in_flight=max(in_flight, len(batch)) if b < len(first_parts) else in_flight)

    await _ensure_tables(frames)  # once per detector (from S3 the first time ever), so batches read them from disk
    net: dict = {}
    defs_text = [""] * n_frames  # FLAGS bit definitions of each file, merged in frame order (they differ by version)
    done = 0
    next_log = {"pct": 10, "at": time.perf_counter() + 15}
    # A preview is written in the background, one at a time, so reading goes on meanwhile (assembling a large field
    # takes seconds); one that is not written by the time the full result is assembled is dropped.
    previewing: asyncio.Task | None = None
    final_started = False

    async def show_preview(idx: list[int]) -> None:
        pid = await _write_preview(p, target, grid, frames, per_frame, errors, defs_text, idx, plan, most_result,
                                   superseded=lambda: final_started)
        if pid and not final_started:
            if on_preview is not None:
                r = on_preview(pid)
                if asyncio.iscoroutine(r):
                    await r
            report(f"preview:{pid}", done, n_frames)

    batches_iter = _run_batches([spec(b, batch) for b, batch in enumerate(parts)], parts, workers,
                                first=len(first_parts))
    try:
        async for res, submitted in batches_iter:
            wait = max(0.0, res.started - submitted) if res.started else None
            for o in res.outcomes:
                t = o.timing
                if wait is not None:
                    t["pool_wait"] = wait
                timing.append(t)
                per_frame[o.index] = o.result
                defs_text[o.index] = o.flag_defs
                if o.error:
                    errors.append(o.error)
                    log.warning("frame failed: %s", o.error)
                lvl = logging.WARNING if t.get("work", 0) > settings.slow_frame_s else logging.DEBUG
                logs.event(log, lvl, "frame", **{k: (round(v, 3) if isinstance(v, float) else v) for k, v in t.items()})
            merge_net(net, res.net)
            done += len(res.outcomes)
            report("fetch", done, n_frames)
            pct, now = 100 * done // n_frames, time.perf_counter()
            if pct >= next_log["pct"] or now >= next_log["at"]:
                logs.event(log, logging.INFO, "fetch progress", done=done, total=n_frames, pct=pct,
                           s=now - t_fetch0, errors=len(errors) or None)
                next_log["pct"], next_log["at"] = (pct // 10 + 1) * 10, now + 15
            ready = plan.ready([o.index for o in res.outcomes])
            if ready is not None and (previewing is None or previewing.done()):
                previewing = asyncio.create_task(show_preview(ready))
    except BaseException:
        if previewing is not None:
            previewing.cancel()
        raise
    final_started = True
    irsa_net = net.pop("irsa", {})
    secs = irsa_net.pop("seconds", 0.0)
    irsa_net["s_mean"] = round(secs / irsa_net["served"], 3) if irsa_net.get("served") else None
    net = {"requests": net.get("requests", 0), "bytes": net.get("bytes", 0), "cache_hits": net.get("cache_hits", 0),
           "retries": net.get("retries", 0), "irsa": irsa_net}
    t_fetch = time.perf_counter() - t0 - t_search
    breakdown = {k: logs.summary([t[k] for t in timing if t.get(k) is not None])
                 for k in ("slot", "read", "tables", "pool_wait", "process", "work")}
    logs.event(log, logging.INFO, "fetch done", s=t_fetch, frames=n_frames,
               used=sum(r is not None for r in per_frame), errors=len(errors) or None, requests=net["requests"],
               mb=round(net["bytes"] / 1e6, 1), cache_hits=net["cache_hits"], retries=net["retries"] or None,
               batches=len(parts), workers=workers or None, previews=plan.previews or None,
               irsa_served=irsa_net.get("served") or None, irsa_busy=irsa_net.get("busy") or None,
               irsa_failed=irsa_net.get("failed") or None)
    for k, v in breakdown.items():
        if v:
            logs.event(log, logging.INFO, f"fetch time {k}", **{q: v[q] for q in ("p50", "p90", "max", "sum")})
    slowest = sorted(timing, key=lambda t: t.get("work", 0), reverse=True)[:3]
    if slowest:
        logs.event(log, logging.INFO, "fetch slowest",
                   frames="; ".join(f"{t['file']} {t.get('work', 0):.1f}s (read {t.get('read') or 0:.1f}, "
                                    f"process {t.get('process') or 0:.1f})" for t in slowest))

    report("stack", 0, 0)
    with logs.timed(log, "assemble") as info:
        meta, arrays = await asyncio.to_thread(assemble, p, target, grid, frames, per_frame, errors,
                                               merge_flag_defs(defs_text))
        info["epochs"] = len(meta["epochs"])
        info["grid"] = grid.n
    t_assemble = time.perf_counter() - t0 - t_search - t_fetch
    if most_result is not None:
        patch_comoving_meta(meta, target, most_result)
        if p.static_sky:  # optional: clean co-adds with the static sky subtracted (spherex_core.solar, ADR-0060)
            from ..solar.static_sky import add_static_sky

            aligned = {r["file"]: r["aligned"] for r in per_frame if r is not None}
            await add_static_sky(meta, arrays, aligned, target, grid, half, p.collections, report)
    meta["stats"].update(net)
    meta["stats"]["timings"] = breakdown
    meta["stats"]["seconds"] = {"search": t_search, "fetch_and_align": t_fetch, "assemble": t_assemble,
                                "total": time.perf_counter() - t0}
    with logs.timed(log, "write") as info:
        await asyncio.to_thread(results.write_result, out_dir, meta, arrays)
        await asyncio.to_thread(_write_frames, out_dir, frames)
        info["mb"] = round(sum(a.nbytes for a in arrays.values()) / 1e6, 1)
    if previewing is not None:  # a preview still being written skips writing; wait so none is left behind
        await asyncio.gather(previewing, return_exceptions=True)
    for pid in plan.preview_ids:  # the previews were only for watching this build
        shutil.rmtree(cache.layout().sequence(pid), ignore_errors=True)
    logs.event(log, logging.INFO, "build done", s=time.perf_counter() - t0, search=t_search, fetch=t_fetch,
               assemble=t_assemble)
    return meta
