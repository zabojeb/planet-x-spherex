"""Build a time sequence for a sky position.

search (IRSA TAP) -> cutouts straight from S3 -> mask, background, align on a common grid (worker
processes) -> group into epochs -> co-add per epoch and detector -> forced photometry for spectra and
light curves -> write the result (pipeline.results).
"""

from __future__ import annotations

import asyncio
import logging
import math
import multiprocessing as mp
import time
import warnings
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Callable

import numpy as np

from .. import cache, logs
from ..archive import Frame, query_frames
from ..bands import NATIVE_PIXSCALE
from ..config import settings
from ..io import Level2Reader, RangeClient
from ..processing import make_grid
from ..timeutil import year_to_mjd
from . import results
from .assemble import assemble
from .frame import process_frame
from .params import SequenceParams

log = logging.getLogger(__name__)
Progress = Callable[[str, int, int], None]
SEARCH_EPOCH = 2026.0  # where to place a moving target for the footprint search (images are 3.5 deg wide)
_POOL: ProcessPoolExecutor | None = None


def _worker_init() -> None:
    warnings.filterwarnings("ignore")


def cpu_pool() -> ProcessPoolExecutor:
    """Process pool shared by all builds: per-frame work is Python-heavy (WCS, photometry), so threads
    would serialize on the GIL."""
    global _POOL
    if _POOL is None:
        _POOL = ProcessPoolExecutor(max_workers=settings.cpu_workers, mp_context=mp.get_context("spawn"),
                                    initializer=_worker_init)
    return _POOL


async def build_sequence(p: SequenceParams, out_dir: Path, progress: Progress | None = None) -> dict:
    """Run the whole pipeline and write the result to out_dir. Returns the metadata dict.

    Logs one INFO line per stage with its duration, a progress line every 10 % of the exposures, and a
    breakdown of where the exposure time went (see logs.py); meta.stats.seconds and .timings keep the same.
    """
    report = progress or (lambda stage, done, total: None)
    t0 = time.perf_counter()
    target = p.target
    logs.event(log, logging.INFO, "build start", target=target.name, ra=round(target.ra, 5), dec=round(target.dec, 5),
               fov_arcmin=p.fov_arcmin)

    report("search", 0, 0)
    ra_q, dec_q = target.position_at(year_to_mjd(SEARCH_EPOCH))
    with logs.timed(log, "search", collections=len(p.collections or settings.collections)) as info:
        frames = await asyncio.to_thread(query_frames, float(ra_q), float(dec_q), p.collections)
        info["frames"] = len(frames)
    t_search = time.perf_counter() - t0
    if not frames:
        raise LookupError("No SPHEREx images cover this position yet.")

    mid = float(np.median([f.mjd for f in frames]))
    ra_c, dec_c = target.position_at(mid)
    grid = make_grid(float(ra_c), float(dec_c), p.fov_arcmin)
    half = int(math.ceil(grid.n * grid.pixscale / NATIVE_PIXSCALE * 0.5 * 1.42)) + 3  # covers any rotation

    loop = asyncio.get_running_loop()
    per_frame: list[dict | None] = [None] * len(frames)
    timing: list[dict] = []  # per exposure: slot, read, tables, pool_wait, process, total (seconds)
    errors: list[str] = []
    done = 0
    n_frames = len(frames)
    next_log = {"pct": 10, "at": time.perf_counter() + 15}
    report("fetch", 0, n_frames)
    t_fetch0 = time.perf_counter()
    with logs.timed(log, "pool ready", level=logging.DEBUG, workers=settings.cpu_workers):
        pool = cpu_pool()

    def log_progress() -> None:
        pct = 100 * done // n_frames
        now = time.perf_counter()
        if pct >= next_log["pct"] or now >= next_log["at"]:
            logs.event(log, logging.INFO, "fetch progress", done=done, total=n_frames, pct=pct,
                       s=now - t_fetch0, errors=len(errors) or None)
            next_log["pct"] = (pct // 10 + 1) * 10
            next_log["at"] = now + 15

    async with RangeClient(settings.max_connections) as client:
        reader = Level2Reader(client, cache.layout().root)
        sem = asyncio.Semaphore(settings.max_frames_in_flight)

        async def one(i: int, f: Frame) -> None:
            nonlocal done
            tq = time.perf_counter()
            t: dict = {"file": f.file_name, "band": f.band}
            async with sem:
                ta = time.perf_counter()
                t["slot"] = ta - tq  # waiting for one of max_frames_in_flight
                try:
                    cut = await reader.cutout(f.s3_url, grid.ra, grid.dec, half)
                    tb = time.perf_counter()
                    t["read"] = tb - ta
                    if cut is not None:
                        wave = await reader.wave_map(f.s3_url, f.detector)
                        epsf = await reader.epsf_table(f.s3_url, f.detector)
                        tc = time.perf_counter()
                        t["tables"] = tc - tb
                        ny, nx = cut.image.shape  # the cutout is centred on the target's mid-epoch position
                        psf_os, _ = epsf.nearest(cut.x0 + (nx - 1) / 2.0, cut.y0 + (ny - 1) / 2.0)
                        r = await loop.run_in_executor(pool, process_frame, cut, f, grid, target, wave,
                                                       psf_os, epsf.oversampling)
                        t["process"] = r.pop("t_process", None)
                        if t["process"] is not None:  # the rest is waiting for a free worker and pickling
                            t["pool_wait"] = max(0.0, time.perf_counter() - tc - t["process"])
                        per_frame[i] = r
                    else:
                        t["off_frame"] = True
                except Exception as e:  # noqa: BLE001 - one bad file must not kill the sequence
                    errors.append(f"{f.file_name}: {e!r}"[:300])
                    t["error"] = repr(e)[:200]
                    log.warning("frame failed: %s %r", f.file_name, e)
                finally:
                    t["total"] = time.perf_counter() - tq
                    t["work"] = time.perf_counter() - ta
                    timing.append(t)
                    lvl = logging.WARNING if t["work"] > settings.slow_frame_s else logging.DEBUG
                    logs.event(log, lvl, "frame", **{k: (round(v, 3) if isinstance(v, float) else v) for k, v in t.items()})
                    done += 1
                    report("fetch", done, n_frames)
                    log_progress()

        await asyncio.gather(*(one(i, f) for i, f in enumerate(frames)))
        net = {"requests": client.requests, "bytes": client.bytes_read, "cache_hits": reader.cache_hits,
               "retries": client.retries_done}
        flag_defs = dict(reader.flag_defs)
    t_fetch = time.perf_counter() - t0 - t_search

    breakdown = {k: logs.summary([t[k] for t in timing if k in t])
                 for k in ("slot", "read", "tables", "pool_wait", "process", "work")}
    slowest = sorted(timing, key=lambda t: t["work"], reverse=True)[:3]
    logs.event(log, logging.INFO, "fetch done", s=t_fetch, frames=n_frames, used=sum(r is not None for r in per_frame),
               errors=len(errors) or None, requests=net["requests"], mb=round(net["bytes"] / 1e6, 1),
               cache_hits=net["cache_hits"], retries=net["retries"] or None)
    for k, v in breakdown.items():
        if v:
            logs.event(log, logging.INFO, f"fetch time {k}", **{q: v[q] for q in ("p50", "p90", "max", "sum")})
    logs.event(log, logging.INFO, "fetch slowest",
               frames="; ".join(f"{t['file']} {t['work']:.1f}s (read {t.get('read', 0):.1f}, "
                                f"process {t.get('process') or 0:.1f})" for t in slowest))

    report("stack", 0, 0)
    with logs.timed(log, "assemble") as info:
        meta, arrays = await asyncio.to_thread(assemble, p, target, grid, frames, per_frame, errors, flag_defs)
        info["epochs"] = len(meta["epochs"])
        info["grid"] = grid.n
    t_assemble = time.perf_counter() - t0 - t_search - t_fetch
    meta["stats"].update(net)
    meta["stats"]["timings"] = breakdown
    meta["stats"]["seconds"] = {"search": t_search, "fetch_and_align": t_fetch, "assemble": t_assemble,
                                "total": time.perf_counter() - t0}
    with logs.timed(log, "write") as info:
        await asyncio.to_thread(results.write_result, out_dir, meta, arrays)
        info["mb"] = round(sum(a.nbytes for a in arrays.values()) / 1e6, 1)
    logs.event(log, logging.INFO, "build done", s=time.perf_counter() - t0, search=t_search, fetch=t_fetch,
               assemble=t_assemble)
    return meta
