"""Read and process a build's exposures in batches, each batch in a process-pool worker (ADR-0039).

A batch is a few exposures. The worker gives it its own event loop and does everything for them: IRSA cutouts or
S3 range reads, header parsing, FLAGS decoding and process_frame. A build therefore uses every core: before, a
single event loop parsed headers and decoded FLAGS for all exposures, and on the dev server a cold 6' build spent
15 s of its 18 s on that one core. Batches are submitted in the order given, so the caller decides what is
finished first (pipeline.sequence puts the epoch the viewer opens on in front, for previews).

The same coroutine runs inside the API process when there is no pool to use (`read_batch` with
`inline=True`), which is also how the offline tests drive it.
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field

from ..archive import Frame
from ..io import IrsaCutouts, Level2Reader, RangeClient
from ..processing import Grid
from ..targets import Target
from .frame import process_frame

# Wavelength maps and ePSF tables a worker process has loaded, per cache root and detector: every batch of every
# build in that process reuses them instead of reading the npz files again.
_TABLES: dict[tuple[str, str, int], object] = {}


@dataclass
class FetchSpec:
    """Everything a worker needs besides the exposures (picklable)."""

    cache_root: str | None
    grid: Grid
    target: Target
    comoving: bool  # every exposure on its own grid centred on the object at that time (solar-system objects)
    half: int
    keep_half: int
    variance_radius: int
    connections: int  # S3 connections of this batch
    irsa_slots: int = 0  # IRSA cutout requests this batch may have in flight (0: S3 only)
    irsa_timeout: float = 15.0
    in_flight: int = 0  # exposures of the batch read at once (0: all)


@dataclass
class Outcome:
    index: int  # position in the build's frame list
    result: dict | None  # process_frame output; None when off the frame or failed
    timing: dict
    error: str | None = None
    flag_defs: str = ""  # the file's FLAGS bit definitions (flags.flag_defs_to_text)


@dataclass
class BatchResult:
    outcomes: list[Outcome]
    net: dict = field(default_factory=dict)
    started: float = 0.0  # time.time() when the worker started the batch


def frame_grid(grid: Grid, target: Target, frame: Frame) -> Grid:
    """The co-moving grid of one exposure: same size and scale, centred on the object at that time."""
    ra, dec = target.position_at(frame.mjd)
    return Grid(float(ra), float(dec), grid.n, grid.pixscale)


def _seed_tables(reader: Level2Reader, root: str | None) -> None:
    for (r, kind, det), table in _TABLES.items():
        if r == str(root):
            (reader.wave_maps if kind == "wave" else reader.epsf_tables)[det] = table


def _keep_tables(reader: Level2Reader, root: str | None) -> None:
    for det, table in reader.wave_maps.items():
        _TABLES[(str(root), "wave", det)] = table
    for det, table in reader.epsf_tables.items():
        _TABLES[(str(root), "epsf", det)] = table


async def read_batch(spec: FetchSpec, items: list[tuple[int, Frame]], client: RangeClient | None = None,
                     irsa: IrsaCutouts | None = None, gate=None) -> BatchResult:
    """Read and process the exposures of one batch; a failing exposure is reported in its Outcome. Without
    clients, the batch opens (and closes) its own, with IRSA slots taken from `gate` when given (shared by the
    batches of a build)."""
    if client is None or irsa is None:
        async with RangeClient(spec.connections) as c, \
                IrsaCutouts(spec.irsa_slots, spec.irsa_timeout, gate=gate) as i:
            return await read_batch(spec, items, c, i)
    before = (client.requests, client.bytes_read, client.retries_done, irsa.requests, irsa.served, irsa.busy,
              irsa.failed, irsa.bytes_read, irsa.seconds)
    reader = Level2Reader(client, spec.cache_root, irsa=irsa)
    _seed_tables(reader, spec.cache_root)
    sem = asyncio.Semaphore(spec.in_flight or max(1, len(items)))
    target = spec.target

    async def one(i: int, f: Frame) -> Outcome:
        tq = time.perf_counter()
        t: dict = {"file": f.file_name, "band": f.band}
        async with sem:
            ta = time.perf_counter()
            t["slot"] = ta - tq
            try:
                g = frame_grid(spec.grid, target, f) if spec.comoving else spec.grid
                cut = await reader.cutout(
                    f.s3_url, g.ra, g.dec, spec.half, keep_half=spec.keep_half,
                    variance_radius=spec.variance_radius, irsa_url=f.irsa_url,
                    # where process_frame measures the target: at MJD-AVG, falling back to the archive's mjd
                    variance_at=lambda h, f=f: target.position_at(float(h.get("MJD-AVG", f.mjd))))
                tb = time.perf_counter()
                t["read"] = tb - ta
                if cut is None:
                    t["off_frame"] = True
                    return Outcome(i, None, t)
                wave = await reader.wave_map(f.s3_url, f.detector)
                epsf = await reader.epsf_table(f.s3_url, f.detector)
                t["tables"] = time.perf_counter() - tb
                ny, nx = cut.image.shape  # the cutout is centred on the target's mid-epoch position
                psf_os, _ = epsf.nearest(cut.x0 + (nx - 1) / 2.0, cut.y0 + (ny - 1) / 2.0)
                r = process_frame(cut, f, g, target, wave, psf_os, epsf.oversampling)
                t["process"] = r.pop("t_process", None)
                return Outcome(i, r, t, flag_defs=cut.flag_defs)
            except Exception as e:  # noqa: BLE001 - one bad file must not kill the sequence
                t["error"] = repr(e)[:200]
                return Outcome(i, None, t, f"{f.file_name}: {e!r}"[:300])
            finally:
                t["work"] = time.perf_counter() - ta
                t["total"] = time.perf_counter() - tq

    outcomes = list(await asyncio.gather(*(one(i, f) for i, f in items)))
    _keep_tables(reader, spec.cache_root)
    req, nb, rt, ir, isv, ib, ifl, ibt, isec = before
    served = irsa.served - isv
    net = {"requests": client.requests - req, "bytes": client.bytes_read - nb + irsa.bytes_read - ibt,
           "cache_hits": reader.cache_hits, "retries": client.retries_done - rt,
           "irsa": {"requests": irsa.requests - ir, "served": served, "busy": irsa.busy - ib,
                    "failed": irsa.failed - ifl, "bytes": irsa.bytes_read - ibt,
                    "s_mean": (irsa.seconds - isec) / served if served else None}}
    return BatchResult(outcomes, net)


# ---------------------------------------------------------------- worker processes
_GATE = None  # IRSA request slots shared by all workers of the pool (set by the pool initializer)


def set_irsa_gate(gate) -> None:
    global _GATE
    _GATE = gate


class _WorkerLoop:
    """A worker process's own event loop, in a thread, with HTTP clients that stay open from batch to batch: the
    TLS connections to S3 are reused instead of being opened again for every batch."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, name="fetch-loop", daemon=True).start()
        self.client: RangeClient | None = None
        self.irsa: IrsaCutouts | None = None

    async def _clients(self, spec: FetchSpec) -> tuple[RangeClient, IrsaCutouts]:
        if self.client is None:
            self.client = await RangeClient(max(spec.connections, 32)).__aenter__()
        if self.irsa is None or self.irsa.timeout != spec.irsa_timeout:
            # opened with room for many connections; the slots actually allowed are set per build below
            self.irsa = await IrsaCutouts(64, spec.irsa_timeout, gate=_GATE).__aenter__()
        self.irsa.max_in_flight = spec.irsa_slots  # 0 when this build reads S3 only
        return self.client, self.irsa

    def run(self, spec: FetchSpec, items: list[tuple[int, Frame]]) -> BatchResult:
        async def go() -> BatchResult:
            client, irsa = await self._clients(spec)
            return await read_batch(spec, items, client, irsa)

        return asyncio.run_coroutine_threadsafe(go(), self.loop).result()


_WORKER: _WorkerLoop | None = None


def warm_worker() -> None:
    """Pool warm-up task: start this worker's event loop (the imports already happened when it was spawned)."""
    global _WORKER
    if _WORKER is None:
        _WORKER = _WorkerLoop()
    time.sleep(0.05)  # hold the worker a moment, so the other warm-up tasks go to (and spawn) other workers


def run_batch(spec: FetchSpec, items: list[tuple[int, Frame]]) -> BatchResult:
    """Entry point in a worker process (module level, so the pool can pickle it by reference)."""
    global _WORKER
    started = time.time()
    if _WORKER is None:
        _WORKER = _WorkerLoop()
    res = _WORKER.run(spec, items)
    res.started = started
    return res


def merge_net(total: dict, part: dict) -> None:
    """Add one batch's network counters to the build's."""
    for k in ("requests", "bytes", "cache_hits", "retries"):
        total[k] = total.get(k, 0) + part.get(k, 0)
    irsa = total.setdefault("irsa", {"requests": 0, "served": 0, "busy": 0, "failed": 0, "bytes": 0, "seconds": 0.0})
    pi = part.get("irsa") or {}
    for k in ("requests", "served", "busy", "failed", "bytes"):
        irsa[k] += pi.get(k, 0) or 0
    irsa["seconds"] += (pi.get("s_mean") or 0.0) * (pi.get("served") or 0)


def batches(items: list, workers: int, size: int = 0) -> list[list]:
    """Cut items (already in priority order) into batches: one wave over the workers, 2 to 16 exposures each,
    so that no batch waits for a free worker while others have finished."""
    if not items:
        return []
    if size <= 0:
        size = min(16, max(2, -(-len(items) // max(1, workers))))
    return [items[k:k + size] for k in range(0, len(items), size)]
