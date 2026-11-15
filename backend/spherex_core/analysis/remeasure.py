"""Forced photometry of any position in a built sequence, on its cached native cutouts.

Runs the same measurements as the pipeline (ePSF fit, aperture fallback, ring photometry for detectors in
which the source saturates) at a new position, with optional proper motion. The cutouts come from the disk
cache the build filled, so no image data is downloaded again; the per-exposure work runs in the shared
process pool. Synchronous by design: a few seconds for hundreds of exposures.
"""

from __future__ import annotations

import asyncio
import time

import numpy as np

from .. import cache
from ..bands import NATIVE_PIXSCALE
from ..config import settings
from ..flags import ARTIFACTS, MASK_DEFAULT, PHOT_BAD, RING_BAD, RING_CORE, SATURATION
from ..io import Cutout, EPSFTable, Level2Reader, RangeClient, WaveMap
from ..photometry import ab_mag, measure, radial_profile
from ..pipeline.assemble import apply_rings
from ..pipeline.sequence import cpu_pool
from ..processing import background
from ..targets import Target
from .common import SequenceContext, cutout_half, sequence_frames


def measure_exposure(cut: Cutout, band: str, mjd_hint: float, target: Target, wave: WaveMap,
                     psf_os: np.ndarray | None, oversampling: int) -> dict:
    """Photometry and radial profile of `target` at the exposure time (module level: pickled for the pool).
    Mirrors the photometry part of pipeline.frame.process_frame."""
    h = cut.header
    masked = (cut.flags & MASK_DEFAULT) != 0
    bkg, _ = background(np.where(masked, np.nan, cut.image.astype(np.float32)))
    mjd = float(h.get("MJD-AVG", mjd_hint))
    ra_t, dec_t = target.position_at(mjd)
    xt, yt = cut.wcs.all_world2pix(ra_t, dec_t, 0, quiet=True)
    xt, yt = float(xt), float(yt)
    wl, dwl = wave(xt, yt)
    omega = float(h.get("OMEGA_MEDIAN", NATIVE_PIXSCALE ** 2))
    fwhm = float(h.get("PSF_FWHM", 1.5 * NATIVE_PIXSCALE))
    phot = measure(cut.image, cut.variance, cut.flags, xt - cut.x0, yt - cut.y0, fwhm, omega, psf_os,
                   oversampling, MASK_DEFAULT, SATURATION, ARTIFACTS, PHOT_BAD)
    ring = radial_profile(cut.image, cut.variance, cut.flags, xt - cut.x0, yt - cut.y0, RING_BAD, RING_CORE,
                          bkg if np.isfinite(bkg) else 0.0)
    return {"band": band, "mjd": mjd, "wl": float(wl), "dwl": float(dwl), "phot": phot, "ring": ring,
            "omega": omega}


def _record(index: int, epoch: int, r: dict) -> dict:
    """Same fields as meta.json photometry records (pipeline.assemble)."""
    ph = r["phot"]
    relative = bool(ph.get("relative", False))
    return {"index": index, "epoch": epoch, "band": r["band"], "mjd": r["mjd"], "wl": r["wl"], "dwl": r["dwl"],
            "flux_mjy": ph["flux_mjy"], "err_mjy": ph["err_mjy"],
            "ab_mag": None if relative else ab_mag(ph["flux_mjy"]), "flagged": ph["flagged"],
            "ap_flags": ph["ap_flags"], "masked_frac": ph["masked_frac"], "method": ph["method"],
            "saturated": ph["saturated"], "npix": ph["npix"], "chi2": ph["chi2"], "psf_weight": ph["psf_weight"],
            "flux_ap_mjy": ph["flux_ap_mjy"], "err_ap_mjy": ph["err_ap_mjy"], "relative": relative,
            "ring_r_in": ph.get("ring_r_in"), "ring_r_out": ph.get("ring_r_out")}


_TABLES: dict[str, object] = {}  # per worker process: wave maps and ePSF tables by cache path


def _table(path: str, kind: str):
    if path not in _TABLES:
        with np.load(path) as z:
            _TABLES[path] = (WaveMap(z["x"], z["y"], z["values"]) if kind == "wave" else
                             EPSFTable(z["xcenter"], z["ycenter"], z["psf"], int(z["oversampling"]), z["cwave"],
                                       z["neff"]))
    return _TABLES[path]


def measure_cached(cut_path: str, wave_path: str, epsf_path: str, band: str, mjd_hint: float,
                   target: Target) -> dict | None:
    """measure_exposure on a cached cutout, done entirely in a pool worker: loading the npz, parsing the header
    and building the WCS cost ~25 ms per exposure, which serialised on the event loop made a re-measure of
    hundreds of exposures take 10 s. Returns None when the cache entry is unusable (the caller fetches it)."""
    from astropy.io import fits

    from ..io import slim_header

    try:
        with np.load(cut_path, allow_pickle=False) as z:
            if int(z["x0"]) < 0:
                return {"off_frame": True}
            cut = Cutout("", slim_header(fits.Header.fromstring(str(z["header"]))), int(z["x0"]), int(z["y0"]),
                         z["image"], z["variance"], z["flags"])
    except Exception:  # noqa: BLE001 - missing or damaged cache entry
        return None
    wave, epsf = _table(wave_path, "wave"), _table(epsf_path, "epsf")
    ra_t, dec_t = target.position_at(mjd_hint)
    xt, yt = cut.wcs.all_world2pix(ra_t, dec_t, 0, quiet=True)
    psf_os, _ = epsf.nearest(float(xt), float(yt))  # zone of the new position, not the cutout centre
    return measure_exposure(cut, band, mjd_hint, target, wave, psf_os, epsf.oversampling)


async def measure_position(ctx: SequenceContext, target: Target) -> dict:
    """Photometry records for `target` in every exposure of the sequence, plus stats.

    Exposures whose cutout is in the disk cache (all of them, for a sequence built here) are measured wholly in
    the process pool; only missing ones go through the reader (S3 range reads)."""
    t0 = time.perf_counter()
    exposures = ctx.meta["exposures"]
    frames = await asyncio.to_thread(sequence_frames, ctx)
    half = cutout_half(ctx.grid)
    loop = asyncio.get_running_loop()
    pool = cpu_pool()
    layout = cache.layout()
    per: list[dict | None] = [None] * len(exposures)
    async with RangeClient(settings.max_connections) as client:
        reader = Level2Reader(client, layout.root)
        sem = asyncio.Semaphore(settings.max_frames_in_flight)
        # One wave map and ePSF table per detector, cached on disk for the workers (loaded once per process).
        first = {}
        for f in frames:
            if f is not None:
                first.setdefault(f.detector, f)
        await asyncio.gather(*(reader.wave_map(f.s3_url, d) for d, f in first.items()),
                             *(reader.epsf_table(f.s3_url, d) for d, f in first.items()))

        async def one(i: int) -> None:
            f = frames[i]
            if f is None:
                return
            cpath = layout.cutout(f.s3_url, ctx.grid.ra, ctx.grid.dec, half)
            if cpath.exists():
                r = await loop.run_in_executor(pool, measure_cached, str(cpath), str(layout.wave(f.detector)),
                                               str(layout.epsf(f.detector)), exposures[i]["band"],
                                               exposures[i]["mjd"], target)
                if r is not None:
                    per[i] = None if r.get("off_frame") else r
                    return
            async with sem:
                cut = await reader.cutout(f.s3_url, ctx.grid.ra, ctx.grid.dec, half)
                if cut is None:
                    return
                wave = await reader.wave_map(f.s3_url, f.detector)
                epsf = await reader.epsf_table(f.s3_url, f.detector)
                ra_t, dec_t = target.position_at(exposures[i]["mjd"])
                xt, yt = cut.wcs.all_world2pix(ra_t, dec_t, 0, quiet=True)
                psf_os, _ = epsf.nearest(float(xt), float(yt))  # zone of the new position, not the cutout centre
                per[i] = await loop.run_in_executor(pool, measure_exposure, cut, exposures[i]["band"],
                                                    exposures[i]["mjd"], target, wave, psf_os, epsf.oversampling)

        await asyncio.gather(*(one(i) for i in range(len(exposures))))
        downloaded = client.bytes_read
    ok = [r for r in per if r is not None]
    for i, r in enumerate(per):
        if r is not None:
            r["index"], r["epoch"] = i, exposures[i]["epoch"]
    rings = apply_rings(ok)
    records = [_record(r["index"], r["epoch"], r) for r in ok if r["phot"] is not None]
    records.sort(key=lambda p: p["index"])
    return {"sequence": ctx.seq_id, "target": target.to_dict(), "photometry": records, "rings": rings,
            "stats": {"exposures": len(exposures), "measured": len(records),
                      "missing_files": sum(f is None for f in frames), "bytes_downloaded": downloaded,
                      "seconds": time.perf_counter() - t0}}
