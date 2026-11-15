"""Photometry on one archival frame (2MASS, AllWISE, NEOWISE), pure functions of a pixel window.

For every position: a standard-aperture sum with a sigma-clipped annulus background (the surveys' own
apertures, aperture-corrected with the PSF's enclosed energy; bad pixels inside take the mean of their radius),
the radius of the saturated core touching the source, and a radial profile in 1-px annuli with a far
background, from which a ring outside the core is measured for stars that saturate (ADR-0020's method applied
to WISE). Values are in DN; calibration to mJy happens in archival_lc with the frame's MAGZP.

Saturated pixels: NEOWISE L1b marks the ramp sample that saturated (mask bits 10-18) and sets bad pixels to NaN
(those are bad, not a core, when a bad-pixel bit explains them); AllWISE Atlas and 2MASS images have no mask,
so their cores are recognised by NaN or by the measured flux exceeding the band's saturation limit.
"""

from __future__ import annotations

import numpy as np
from scipy.ndimage import label as label_regions

CLIP_SIGMA = 3.0
MAX_MASKED_FRACTION = 0.1  # of the aperture's PSF weight
CORE_TOUCH_PX = 1.5
MIN_ANNULUS_PIXELS = 20


def _clipped(values: np.ndarray) -> tuple[float, float]:
    """Median and robust sigma after 3-sigma clipping (5 iterations)."""
    v = values[np.isfinite(values)]
    if v.size == 0:
        return float("nan"), float("nan")
    for _ in range(5):
        med = np.median(v)
        sig = 1.4826 * np.median(np.abs(v - med))
        keep = np.abs(v - med) <= CLIP_SIGMA * max(sig, 1e-12)
        if keep.all() or keep.sum() < 10:
            break
        v = v[keep]
    med = float(np.median(v))
    return med, float(1.4826 * np.median(np.abs(v - med)))


def _overlap_weights(dx: np.ndarray, dy: np.ndarray, r: float, sub: int = 5) -> np.ndarray:
    """Fraction of each pixel inside a circle of radius r (sub x sub sampling, exact enough for r >= 2 px)."""
    off = (np.arange(sub) + 0.5) / sub - 0.5
    ox, oy = np.meshgrid(off, off)
    d2 = (dx[..., None, None] + ox) ** 2 + (dy[..., None, None] + oy) ** 2
    return (d2 <= r * r).mean(axis=(-1, -2))


def measure_position(data: np.ndarray, bad: np.ndarray, core: np.ndarray, x: float, y: float, r_ap: float,
                     bkg_in: float, bkg_out: float, profile_px: int, ring_bkg: tuple[float, float],
                     frame_sigma: float) -> dict | None:
    """Aperture flux (DN), core radius and radial profile at (x, y) in window pixel coordinates.

    bad: pixels not to use (masked or saturated or NaN); core: pixels that belong to a saturated core
    (saturation bits or NaN). Returns None when the aperture does not fit in the window.
    """
    ny, nx = data.shape
    outer = max(bkg_out, ring_bkg[1])
    if not (r_ap + 1 <= x <= nx - 2 - r_ap and r_ap + 1 <= y <= ny - 2 - r_ap):
        return None
    x0, x1 = max(int(np.floor(x - outer)), 0), min(int(np.ceil(x + outer)) + 1, nx)
    y0, y1 = max(int(np.floor(y - outer)), 0), min(int(np.ceil(y + outer)) + 1, ny)
    gy, gx = np.mgrid[y0:y1, x0:x1]
    dx, dy = gx - x, gy - y
    r = np.hypot(dx, dy)
    d = data[y0:y1, x0:x1].astype(np.float64)
    b = bad[y0:y1, x0:x1]
    c = core[y0:y1, x0:x1]

    ann = (r >= bkg_in) & (r < bkg_out) & ~b
    if ann.sum() >= MIN_ANNULUS_PIXELS:
        bkg, sig = _clipped(d[ann])
        n_ann = int(ann.sum())
    else:
        bkg, sig, n_ann = 0.0, frame_sigma, 0
    if not np.isfinite(sig) or sig <= 0:
        sig = frame_sigma

    near = r <= r_ap + 1.0
    w = np.zeros_like(r)
    w[near] = _overlap_weights(dx[near], dy[near], r_ap)
    good = ~b
    # bad pixels inside the aperture take the mean of the good pixels at the same radius (1-px annuli), so a
    # dead or noisy pixel on the PSF does not bias the flux; an annulus without good pixels contributes nothing
    # and the frame is flagged by its masked share
    sub_ap = np.where(good, d - bkg, 0.0)
    kr = np.floor(r).astype(int)
    use_ap = near & good
    nk = int(kr[near].max()) + 1
    cnt = np.bincount(kr[use_ap], minlength=nk)
    mean_k = np.bincount(kr[use_ap], weights=sub_ap[use_ap], minlength=nk) / np.maximum(cnt, 1)
    fill = near & b & (kr < nk)
    sub_ap[fill] = np.where(cnt[kr[fill]] > 0, mean_k[kr[fill]], 0.0)
    flux = float((w * sub_ap).sum())
    n_eff = float((w * good).sum())
    err = float(sig * np.sqrt(n_eff + (n_eff ** 2 / n_ann if n_ann else 0.0)))
    # masked share weighted like a centrally concentrated PSF (Gaussian of sigma r_ap / 2)
    psfw = w * np.exp(-0.5 * (r / max(r_ap / 2.0, 0.5)) ** 2)
    masked_frac = float((psfw * b).sum() / psfw.sum()) if psfw.sum() > 0 else 1.0

    labels, _ = label_regions(c & (r < profile_px), structure=np.ones((3, 3), dtype=bool))
    touching = np.unique(labels[(r <= CORE_TOUCH_PX) & (labels > 0)])
    core_r = float(r[np.isin(labels, touching)].max() + 0.5) if touching.size else 0.0

    in_far = (r >= ring_bkg[0]) & (r < ring_bkg[1]) & ~b
    far_bkg, far_sig = _clipped(d[in_far]) if in_far.sum() >= MIN_ANNULUS_PIXELS else (bkg, sig)
    if not np.isfinite(far_sig) or far_sig <= 0:
        far_sig = sig
    # faint (sub-threshold) sources make the mean sky brighter than the median: their expected flux per pixel
    far_excess = 0.0
    if in_far.sum() >= MIN_ANNULUS_PIXELS:
        v = d[in_far]
        v = v[np.isfinite(v) & (np.abs(v - far_bkg) <= 5.0 * far_sig)]
        if v.size:
            far_excess = float(v.mean() - far_bkg)
    k = np.floor(r).astype(int)
    inside = r < profile_px
    use = inside & good
    sub = d - far_bkg
    return {
        "flux_dn": flux, "err_dn": err, "bkg": float(bkg), "bkg_sigma": float(sig),
        "masked_frac": masked_frac, "core_r": core_r,
        "sat_in_aperture": bool((c & (r <= r_ap)).any()),
        "profile": {
            "ntotal": np.bincount(k[inside], minlength=profile_px)[:profile_px].astype(np.int32),
            "nvalid": np.bincount(k[use], minlength=profile_px)[:profile_px].astype(np.int32),
            "sums": np.bincount(k[use], weights=sub[use], minlength=profile_px)[:profile_px],
            "sigma": float(far_sig),
            "n_far": int(in_far.sum()),
            "far_excess": far_excess,
        },
    }


def ring_dn(profile: dict, r_in: int, r_out: int, min_valid: float = 0.6, min_coverage: float = 0.9
            ) -> tuple[float, float] | None:
    """Background-subtracted DN in the ring r_in <= r < r_out of a radial profile, masked pixels filled with
    their annulus mean; (flux, error) or None when the ring leaves the window or is mostly masked."""
    ks = np.arange(r_in, r_out)
    if ks.size == 0 or ks[-1] >= len(profile["ntotal"]):
        return None
    ntot = profile["ntotal"][ks].astype(float)
    nval = profile["nvalid"][ks].astype(float)
    if (ntot.sum() < min_coverage * np.pi * (2 * ks + 1).sum() or np.any(nval == 0)
            or nval.sum() < min_valid * ntot.sum()):
        return None
    fill = ntot / nval
    s = float((profile["sums"][ks] * fill).sum())
    n = float(ntot.sum())
    n_far = max(profile.get("n_far", 0), 1)
    # pixel noise of the filled annuli plus the error of the far background, scaled to the ring's area
    err = float(profile["sigma"] * np.sqrt((ntot ** 2 / nval).sum() + n * n / n_far))
    return s, err


def choose_ring(core_radii: list[float], width: int, profile_px: int, percentile: float = 95.0
                ) -> tuple[int, int] | None:
    """One ring for every frame of a (target, band): from 1 px outside the given percentile of the saturated-core
    radii, `width` px wide; None if it would not fit in the profile."""
    if not core_radii:
        return None
    r_in = int(np.ceil(np.percentile(core_radii, percentile))) + 1
    r_out = r_in + width
    if r_out > profile_px - 1:
        return None
    return r_in, r_out
