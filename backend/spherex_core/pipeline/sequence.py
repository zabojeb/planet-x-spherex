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

from .. import cache
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
    """Run the whole pipeline and write the result to out_dir. Returns the metadata dict."""
    report = progress or (lambda stage, done, total: None)
    t0 = time.perf_counter()
    target = p.target

    report("search", 0, 0)
    ra_q, dec_q = target.position_at(year_to_mjd(SEARCH_EPOCH))
    frames = await asyncio.to_thread(query_frames, float(ra_q), float(dec_q), p.collections)
    t_search = time.perf_counter() - t0
    if not frames:
        raise LookupError("No SPHEREx images cover this position yet.")

    mid = float(np.median([f.mjd for f in frames]))
    ra_c, dec_c = target.position_at(mid)
    grid = make_grid(float(ra_c), float(dec_c), p.fov_arcmin)
    half = int(math.ceil(grid.n * grid.pixscale / NATIVE_PIXSCALE * 0.5 * 1.42)) + 3  # covers any rotation

    loop = asyncio.get_running_loop()
    per_frame: list[dict | None] = [None] * len(frames)
    errors: list[str] = []
    done = 0
    report("fetch", 0, len(frames))
    pool = cpu_pool()
    async with RangeClient(settings.max_connections) as client:
        reader = Level2Reader(client, cache.layout().root)
        sem = asyncio.Semaphore(settings.max_frames_in_flight)

        async def one(i: int, f: Frame) -> None:
            nonlocal done
            async with sem:
                try:
                    cut = await reader.cutout(f.s3_url, grid.ra, grid.dec, half)
                    if cut is not None:
                        wave = await reader.wave_map(f.s3_url, f.detector)
                        epsf = await reader.epsf_table(f.s3_url, f.detector)
                        ny, nx = cut.image.shape  # the cutout is centred on the target's mid-epoch position
                        psf_os, _ = epsf.nearest(cut.x0 + (nx - 1) / 2.0, cut.y0 + (ny - 1) / 2.0)
                        per_frame[i] = await loop.run_in_executor(pool, process_frame, cut, f, grid, target, wave,
                                                                  psf_os, epsf.oversampling)
                except Exception as e:  # noqa: BLE001 - one bad file must not kill the sequence
                    errors.append(f"{f.file_name}: {e!r}"[:300])
                    log.warning("frame failed: %s %r", f.file_name, e)
                finally:
                    done += 1
                    report("fetch", done, len(frames))

        await asyncio.gather(*(one(i, f) for i, f in enumerate(frames)))
        net = {"requests": client.requests, "bytes": client.bytes_read, "cache_hits": reader.cache_hits}
        flag_defs = dict(reader.flag_defs)
    t_fetch = time.perf_counter() - t0 - t_search

    report("stack", 0, 0)
    meta, arrays = await asyncio.to_thread(assemble, p, target, grid, frames, per_frame, errors, flag_defs)
    meta["stats"].update(net)
    meta["stats"]["seconds"] = {"search": t_search, "fetch_and_align": t_fetch,
                                "total": time.perf_counter() - t0}
    await asyncio.to_thread(results.write_result, out_dir, meta, arrays)
    return meta
