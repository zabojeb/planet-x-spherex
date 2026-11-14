"""Photometry at a (moving) target position on native SPHEREx pixels.

Primary: a PSF fit with the detector's effective PSF (ePSF) that skips saturated, nonlinear and masked
pixels, so bright stars are measured from their unsaturated pixels. Secondary: an aperture sum with a
Gaussian aperture correction, kept for comparison and as a fallback when the fit has too little to work on.
Both report mJy with a systematic floor added in quadrature (the VARIANCE extension alone gave reduced
chi^2 ~ 3.8 on a constant star).
"""

from __future__ import annotations

import numpy as np
from astropy.stats import SigmaClip
from photutils.aperture import ApertureStats, CircularAnnulus, CircularAperture, aperture_photometry
from scipy.ndimage import map_coordinates

from .flags import MAX_MASKED_FRACTION

ARCSEC2_TO_SR = (np.pi / 180.0 / 3600.0) ** 2
GAUSS_FWHM_TO_SIGMA = 1.0 / 2.354820045
SYSTEMATIC_FLOOR = 0.03  # fractional error added in quadrature to every flux
PSF_FIT_RADIUS = 5.0  # native pixels; pixels beyond the ePSF support only constrain the background
# The SPHEREx ePSF is compact: ~67 % of the flux in the central pixel and ~97 % within 3x3 (D1), so a star
# whose 3x3 core saturates leaves only a few per cent of the PSF in usable pixels. Such fits are still made
# (they track brightness changes of saturated variables) but flagged.
MIN_PSF_WEIGHT = 0.02  # fit only if the usable pixels hold at least this fraction of the PSF
FLAG_PSF_WEIGHT = 0.2  # ...and flag the result if they hold less than this
MIN_FIT_PIXELS = 5
SATURATED_CORE_RADIUS = 2.0  # a saturated pixel this close to the source marks the measurement "saturated"


def mjy_per_unit(omega_arcsec2: float) -> float:
    """(MJy/sr) summed over pixels of solid angle omega -> mJy."""
    return omega_arcsec2 * ARCSEC2_TO_SR * 1e9


def with_floor(flux: float, err: float, floor: float = SYSTEMATIC_FLOOR) -> float:
    return float(np.hypot(err, floor * abs(flux)))


def epsf_values(psf_os: np.ndarray, oversampling: int, dx, dy) -> np.ndarray:
    """Pixel-integrated PSF per unit flux at native-pixel offsets (dx, dy) from the source.

    photutils ePSF convention: the oversampled array sums to 1 and a sample taken every `oversampling`
    points is the flux fraction of a native pixel divided by oversampling**2. Cubic-spline interpolation,
    zero outside the array (the ePSF covers about +-3.3 native pixels).
    """
    cy, cx = (np.asarray(psf_os.shape, dtype=float) - 1.0) / 2.0
    yy = cy + np.asarray(dy, dtype=float) * oversampling
    xx = cx + np.asarray(dx, dtype=float) * oversampling
    vals = map_coordinates(np.asarray(psf_os, dtype=np.float64), [yy, xx], order=3, mode="constant", cval=0.0)
    return vals * oversampling ** 2


def psf_model(psf_os: np.ndarray, oversampling: int, dx, dy, x_frac: float, y_frac: float) -> np.ndarray:
    """ePSF values at offsets (dx, dy), normalised so that the full stamp at this sub-pixel phase sums to 1."""
    half = int(np.ceil(max(psf_os.shape) / oversampling / 2.0)) + 1
    k = np.arange(-half, half + 1, dtype=float)
    ox, oy = np.meshgrid(k - x_frac, k - y_frac)
    norm = epsf_values(psf_os, oversampling, ox, oy).sum()
    return epsf_values(psf_os, oversampling, dx, dy) / norm


def psf_photometry(image: np.ndarray, variance: np.ndarray, flags: np.ndarray, x: float, y: float,
                   psf_os: np.ndarray, oversampling: int, omega_arcsec2: float, mask_bits: int, sat_bits: int,
                   artifact_bits: int, radius: float = PSF_FIT_RADIUS) -> dict | None:
    """Weighted linear least-squares fit of flux + constant background with the ePSF fixed at (x, y).

    Saturated/nonlinear and masked pixels are left out, so a star whose core saturates is measured from
    its unsaturated pixels. Returns None when the target is off the cutout, fewer than MIN_FIT_PIXELS remain,
    or the remaining pixels hold less than MIN_PSF_WEIGHT of the PSF (then the caller falls back to the
    aperture). Flux and error are in mJy without the systematic floor.
    """
    ny, nx = image.shape
    if not (0.0 <= x <= nx - 1.0 and 0.0 <= y <= ny - 1.0):
        return None
    x0, x1 = max(int(np.floor(x - radius)), 0), min(int(np.ceil(x + radius)) + 1, nx)
    y0, y1 = max(int(np.floor(y - radius)), 0), min(int(np.ceil(y + radius)) + 1, ny)
    gy, gx = np.mgrid[y0:y1, x0:x1]
    dx, dy = gx - x, gy - y
    r2 = dx * dx + dy * dy
    inside = r2 <= radius * radius
    model = psf_model(psf_os, oversampling, dx, dy, x - np.round(x), y - np.round(y))

    data = image[y0:y1, x0:x1].astype(np.float64)
    var = variance[y0:y1, x0:x1].astype(np.float64)
    fl = flags[y0:y1, x0:x1].astype(np.int64)
    sat = (fl & sat_bits) != 0
    masked = (((fl & mask_bits) != 0) | ~np.isfinite(data) | ~np.isfinite(var) | (var <= 0)) & ~sat
    use = inside & ~sat & ~masked
    npix = int(use.sum())
    weight_total = float(model[inside].sum())
    psf_weight = float(model[use].sum()) / weight_total if weight_total > 0 else 0.0
    if npix < MIN_FIT_PIXELS or psf_weight < MIN_PSF_WEIGHT:
        return None

    a = np.stack([model[use], np.ones(npix)], axis=1)
    w = 1.0 / var[use]
    ata = (a * w[:, None]).T @ a
    atb = (a * w[:, None]).T @ data[use]
    try:
        cov = np.linalg.inv(ata)
    except np.linalg.LinAlgError:
        return None
    p = cov @ atb
    if not (np.all(np.isfinite(p)) and cov[0, 0] > 0):
        return None
    resid = data[use] - a @ p
    chi2 = float((w * resid * resid).sum() / max(npix - 2, 1))
    masked_frac = float(model[inside & masked].sum()) / weight_total if weight_total > 0 else 1.0
    used_flags = int(np.bitwise_or.reduce(fl[use])) if npix else 0
    scale = mjy_per_unit(omega_arcsec2)
    return {
        "flux_mjy": float(p[0]) * scale,
        "err_mjy": float(np.sqrt(cov[0, 0])) * scale,
        "bkg": float(p[1]),
        "npix": npix,
        "chi2": chi2,
        "psf_weight": psf_weight,
        "masked_frac": masked_frac,
        "saturated": bool((sat & (r2 <= SATURATED_CORE_RADIUS ** 2)).any()),
        "used_flags": used_flags,
        "flagged": bool(masked_frac > MAX_MASKED_FRACTION or psf_weight < FLAG_PSF_WEIGHT
                        or used_flags & artifact_bits),
    }


def forced_photometry(image: np.ndarray, variance: np.ndarray, flags: np.ndarray, x: float, y: float,
                      fwhm_arcsec: float, omega_arcsec2: float, mask_bits: int, bad_bits: int,
                      max_masked_frac: float = MAX_MASKED_FRACTION) -> dict | None:
    """Flux in mJy inside r = 1 FWHM with a local annulus background and a Gaussian aperture correction.

    Returns None when the aperture does not fit inside the cutout.
    """
    ny, nx = image.shape
    pix = float(np.sqrt(omega_arcsec2))
    fwhm_px = max(float(fwhm_arcsec) / pix, 0.8)
    r = max(1.5, fwhm_px)
    r_in = max(4.0, 2.5 * fwhm_px)
    r_out = r_in + 3.0
    if not (r + 1 <= x <= nx - 2 - r and r + 1 <= y <= ny - 2 - r):
        return None

    masked = ((flags & mask_bits) != 0) | ~np.isfinite(image)
    aper = CircularAperture((x, y), r)
    ann = CircularAnnulus((x, y), r_in, r_out)
    st = ApertureStats(image, ann, mask=masked, sigma_clip=SigmaClip(sigma=3.0, maxiters=5))
    bkg = float(st.median) if np.isfinite(st.median) else 0.0
    data = np.where(masked, 0.0, image - bkg)
    err = np.sqrt(np.clip(np.nan_to_num(variance, nan=0.0), 0.0, None))
    t = aperture_photometry(data, aper, error=err, mask=masked, method="exact")
    s, e = float(t["aperture_sum"][0]), float(t["aperture_sum_err"][0])

    am = aper.to_mask(method="exact")
    weights = am.data
    fc = am.cutout(flags, fill_value=0)
    ap_flags = int(np.bitwise_or.reduce(fc[weights > 0].astype(np.int64))) if fc is not None else 0
    mc = am.cutout(masked.astype(float), fill_value=1.0)
    masked_frac = float((mc * weights).sum() / weights.sum()) if mc is not None else 1.0

    sigma = fwhm_px * GAUSS_FWHM_TO_SIGMA
    apcorr = 1.0 / (1.0 - np.exp(-(r * r) / (2.0 * sigma * sigma)))
    scale = mjy_per_unit(omega_arcsec2) * apcorr
    return {
        "flux_mjy": s * scale,
        "err_mjy": e * scale,
        "bkg": bkg,
        "r_px": r,
        "ap_flags": ap_flags,
        "masked_frac": masked_frac,
        "flagged": bool((ap_flags & bad_bits) or masked_frac > max_masked_frac),
    }


def measure(image: np.ndarray, variance: np.ndarray, flags: np.ndarray, x: float, y: float,
            fwhm_arcsec: float, omega_arcsec2: float, psf_os: np.ndarray | None, oversampling: int,
            mask_bits: int, sat_bits: int, artifact_bits: int, bad_bits: int) -> dict | None:
    """Both measurements combined into the photometry record of meta.json (minus the frame fields).

    method "psf" when the ePSF fit worked, otherwise "aperture". Errors include the systematic floor.
    """
    ap = forced_photometry(image, variance, flags, x, y, fwhm_arcsec, omega_arcsec2, mask_bits, bad_bits)
    psf = None
    if psf_os is not None:
        psf = psf_photometry(image, variance, flags, x, y, psf_os, oversampling, omega_arcsec2, mask_bits,
                             sat_bits, artifact_bits)
    if psf is None and ap is None:
        return None
    rec: dict = {
        "flux_ap_mjy": ap["flux_mjy"] if ap else None,
        "err_ap_mjy": with_floor(ap["flux_mjy"], ap["err_mjy"]) if ap else None,
        "ap_flags": ap["ap_flags"] if ap else (psf["used_flags"] if psf else 0),
    }
    if psf is not None:
        rec.update(method="psf", flux_mjy=psf["flux_mjy"], err_mjy=with_floor(psf["flux_mjy"], psf["err_mjy"]),
                   saturated=psf["saturated"], npix=psf["npix"], chi2=psf["chi2"],
                   psf_weight=psf["psf_weight"], masked_frac=psf["masked_frac"], flagged=psf["flagged"])
    else:
        saturated = bool(ap["ap_flags"] & sat_bits)
        rec.update(method="aperture", flux_mjy=ap["flux_mjy"], err_mjy=rec["err_ap_mjy"], saturated=saturated,
                   npix=None, chi2=None, psf_weight=None, masked_frac=ap["masked_frac"],
                   flagged=bool(ap["flagged"] or saturated))
    return rec


def ab_mag(flux_mjy: float | None) -> float | None:
    return float(-2.5 * np.log10(flux_mjy * 1e-3 / 3631.0)) if flux_mjy and flux_mjy > 0 else None
