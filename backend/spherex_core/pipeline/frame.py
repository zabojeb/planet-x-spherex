"""CPU work for one exposure, run in a worker process: mask, background, align to the grid, photometry."""

from __future__ import annotations

import time

import numpy as np

from ..archive import Frame
from ..bands import NATIVE_PIXSCALE
from ..flags import ARTIFACTS, MASK_DEFAULT, PHOT_BAD, RING_BAD, RING_CORE, SATURATION, count_bits
from ..io import Cutout, WaveMap
from ..photometry import measure, radial_profile
from ..processing import Grid, background, pixel_map, resample, resample_flags
from ..targets import Target


def process_frame(cut: Cutout, frame: Frame, grid: Grid, target: Target, wave: WaveMap,
                  psf_os: np.ndarray | None = None, oversampling: int = 5) -> dict:
    """Aligned image and flags on the grid plus photometry at the target's position at exposure time.

    psf_os: the detector-zone ePSF for this cutout (EPSFTable.nearest), or None to skip the PSF fit.
    Must stay a module-level function: it is pickled by reference for the process pool.
    `t_process` in the result is the wall time spent here, in seconds (for the build's timing log).
    """
    t0 = time.perf_counter()
    h = cut.header
    masked = (cut.flags & MASK_DEFAULT) != 0
    raw = cut.image.astype(np.float32)
    bkg, rms = background(np.where(masked, np.nan, raw))
    img = np.where(masked, np.nan, raw) - bkg
    xs, ys = pixel_map(cut.wcs, cut.x0, cut.y0, grid)
    aligned = resample(img, xs, ys)
    aligned_raw = resample(raw - bkg, xs, ys)  # unmasked; only used to fill pixels masked in every frame
    aligned_flags = resample_flags(cut.flags, xs, ys)

    mjd = float(h.get("MJD-AVG", frame.mjd))
    ra_t, dec_t = target.position_at(mjd)
    xt, yt = cut.wcs.all_world2pix(ra_t, dec_t, 0, quiet=True)
    xt, yt = float(xt), float(yt)
    wl, dwl = wave(xt, yt)
    omega = float(h.get("OMEGA_MEDIAN", NATIVE_PIXSCALE ** 2))
    fwhm = float(h.get("PSF_FWHM", 1.5 * NATIVE_PIXSCALE))
    phot = measure(cut.image, cut.variance, cut.flags, xt - cut.x0, yt - cut.y0, fwhm, omega, psf_os,
                   oversampling, MASK_DEFAULT, SATURATION, ARTIFACTS, PHOT_BAD)
    # Radial profile for ring photometry; used only if this detector turns out to saturate (assemble.py).
    ring = radial_profile(cut.image, cut.variance, cut.flags, xt - cut.x0, yt - cut.y0, RING_BAD, RING_CORE,
                          bkg if np.isfinite(bkg) else 0.0)
    return {
        "t_process": time.perf_counter() - t0,
        "band": frame.band, "mjd": mjd, "wl": float(wl), "dwl": float(dwl), "bkg": bkg, "rms": rms,
        "weight": 1.0 / rms ** 2 if np.isfinite(rms) and rms > 0 else 0.0, "psf_fwhm": fwhm,
        "aligned": aligned, "aligned_raw": aligned_raw, "flags": aligned_flags, "phot": phot,
        "ring": ring, "omega": omega,
        "flag_counts": count_bits(aligned_flags),
        "obs_id": frame.obs_id, "collection": frame.collection, "file": frame.file_name,
    }
