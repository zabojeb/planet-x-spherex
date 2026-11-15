"""Coma detection: is a moving source more extended than a star, and does it have a tail?

Both the object and the reference PSF are built the same way, so the resampling, the sub-pixel shifts and the
co-adding broaden them alike:

* object stamp: every exposure (static sky subtracted) is shifted with bilinear interpolation so that the object's
  predicted position lands on the stamp centre, then the stamps are averaged with inverse-variance weights;
* PSF stamp: bright, unsaturated, isolated stars in single exposures of the same detectors, shifted to their own
  centroids, normalised and median-combined per detector; mixed with the object's flux per detector.

Measures (on background-subtracted stamps, about the refined centroid):

* size: Gaussian-weighted second moment T = <r^2> (weight sigma 1.5 px). A coma (surface brightness ~ 1/rho)
  convolved with the PSF has a larger T than the PSF; the excess is converted to a Gaussian-equivalent intrinsic
  FWHM by quadrature;
* concentration: flux in the annulus 2.5-5 px over the flux inside 1.5 px, relative to the PSF's ratio;
* tail: the first azimuthal moment of the light in the annulus 1.5-5 px (PSF's subtracted), its position angle and
  significance, compared with the projected anti-solar direction and the direction opposite to the motion (dust
  trail).

Errors come from a bootstrap over the exposures (resampled with replacement: it captures per-exposure noise,
registration jitter and residual stars), plus a 5 % systematic floor and the expected track jitter. The coma
score is a logistic function of the larger of the size and concentration significances, raised when an aligned
tail is detected.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.ndimage import map_coordinates

from ..bands import BANDS
from .geometry import angle_diff, pa_of

HALF = 7  # stamps are 15 x 15 grid pixels
MOM_SIGMA = 1.5  # px, Gaussian weight of the moments
CENTROID_SIGMA = 0.8  # px, Gaussian weight of the centroid
CONC_FLOOR = 0.02  # absolute systematic on the annulus / core ratio (background and neighbour residuals)
R_CORE = 1.5
R_ANN = (2.5, 5.0)
R_TAIL = (1.5, 5.0)
R_BKG = 6.0  # outer region of the stamp sets the zero level
BOOT = 150
SYS_FLOOR = 0.05  # fractional systematic on the PSF size and concentration
STAR_SNR = (25.0, 400.0)
STAR_ISOLATION = 5.0  # px: no other source brighter than 5 % of the star within this radius
MAX_STARS_PER_BAND = 30
MAX_EXPOSURES_PER_BAND = 40
MIN_EXPOSURES = 6  # for a morphology verdict
TAIL_SIGMA = 4.0
TAIL_MIN_AMP = 0.05  # first azimuthal moment in units of the core flux: below this, interpolation skew
TAIL_ALIGN_DEG = 45.0

_yy, _xx = np.mgrid[-HALF:HALF + 1, -HALF:HALF + 1].astype(float)
_R = np.hypot(_xx, _yy)


def shift_stamp(img: np.ndarray, x: float, y: float, half: int = HALF) -> np.ndarray:
    """A (2 half + 1)^2 stamp of img with (x, y) on its centre (bilinear); NaN where the image has no data."""
    yy, xx = np.mgrid[-half:half + 1, -half:half + 1].astype(float)
    coords = np.array([y + yy, x + xx])
    finite = np.isfinite(img)
    data = map_coordinates(np.where(finite, img, 0.0).astype(float), coords, order=1, mode="constant", cval=0.0)
    cover = map_coordinates(finite.astype(float), coords, order=1, mode="constant", cval=0.0)
    with np.errstate(all="ignore"):
        return np.where(cover > 0.99, data / np.maximum(cover, 1e-9), np.nan)


def _background(st: np.ndarray) -> float:
    """Median of the stamp's outer region with neighbours' light clipped (3 robust sigmas above the median)."""
    v = st[(_R >= R_BKG) & np.isfinite(st)] if st.shape == _R.shape else st[np.isfinite(st)]
    if v.size < 8:
        return 0.0
    for _ in range(3):
        med = float(np.median(v))
        mad = 1.4826 * float(np.median(np.abs(v - med)))
        keep = v <= med + 2.5 * mad if mad > 0 else np.ones(v.shape, bool)
        if keep.all() or keep.sum() < 8:
            break
        v = v[keep]
    return float(np.median(v))


def centroid(st: np.ndarray, iterations: int = 5, max_shift: float = 1.5) -> tuple[float, float]:
    """Centroid offset (dx, dy) of a stamp's core about its centre (Gaussian weight CENTROID_SIGMA, small enough
    that a tail does not pull it)."""
    cx = cy = 0.0
    img = np.nan_to_num(st - _background(st))
    for _ in range(iterations):
        w = np.exp(-((_xx - cx) ** 2 + (_yy - cy) ** 2) / (2 * CENTROID_SIGMA ** 2)) * img
        s = w.sum()
        if not s > 0:
            return 0.0, 0.0
        nx, ny = float((w * _xx).sum() / s), float((w * _yy).sum() / s)
        if math.hypot(nx, ny) > max_shift:
            return 0.0, 0.0
        if abs(nx - cx) < 1e-3 and abs(ny - cy) < 1e-3:
            cx, cy = nx, ny
            break
        cx, cy = nx, ny
    return cx, cy


def fill_holes(st: np.ndarray, max_frac: float = 0.25) -> np.ndarray | None:
    """Masked pixels inside r <= 5 px replaced by the median of their 1-px ring; None when the core (r <= 2) has a
    hole or more than max_frac of the measured area is masked."""
    if st is None:
        return None
    inner = _R <= R_ANN[1]
    bad = ~np.isfinite(st)
    if bad[_R <= 2.0].any() or bad[inner].mean() > max_frac:
        return None
    if not bad[inner].any():
        return st
    out = st.copy()
    ring = np.floor(_R).astype(int)
    for k in np.unique(ring[inner & bad]):
        sel = ring == k
        vals = st[sel & ~bad]
        out[sel & bad] = np.median(vals) if vals.size else 0.0
    return out


def measures(st: np.ndarray, recentre: bool = True) -> dict | None:
    """Size, concentration and first azimuthal moment of a stamp (background subtracted, about its centroid)."""
    st = fill_holes(st)
    if st is None:
        return None
    img = st - _background(st)
    cx, cy = centroid(st) if recentre else (0.0, 0.0)
    dx, dy = _xx - cx, _yy - cy
    r = np.hypot(dx, dy)
    w = np.exp(-r ** 2 / (2 * MOM_SIGMA ** 2))
    f = np.nan_to_num(img)
    s = float((w * f).sum())
    core = float(f[r <= R_CORE].sum())
    if not (s > 0 and core > 0):
        return None
    t = float((w * f * r ** 2).sum() / s)
    ann = float(f[(r >= R_ANN[0]) & (r <= R_ANN[1])].sum())
    sel = (r >= R_TAIL[0]) & (r <= R_TAIL[1])
    # first azimuthal moment of the annulus light, in units of the core flux (a point source gives ~0)
    ax = float((f[sel] * dx[sel] / r[sel]).sum() / core)
    ay = float((f[sel] * dy[sel] / r[sel]).sum() / core)
    return {"T": t, "conc": ann / core, "ax": ax, "ay": ay, "cx": cx, "cy": cy, "core": core}


def gaussian_sigma(t_weighted: float) -> float | None:
    """Gaussian sigma (px) whose MOM_SIGMA-weighted second moment is t_weighted (inverse of 2 s^2 w^2/(s^2+w^2))."""
    w2 = MOM_SIGMA ** 2
    den = 2 * w2 - t_weighted
    if den <= 0 or t_weighted <= 0:
        return None
    return math.sqrt(t_weighted * w2 / den)


def gaussian_stamp(fwhm_px: float) -> np.ndarray:
    s = fwhm_px / 2.3548
    g = np.exp(-_R ** 2 / (2 * s * s))
    return g / g[_R <= 4].sum()


def _norm(st: np.ndarray) -> np.ndarray | None:
    img = st - _background(st)
    s = float(np.nansum(img[_R <= 4]))
    return img / s if s > 0 else None


# ---------------------------------------------------------------- reference PSF from field stars
def star_psfs(exposures: dict[str, list[np.ndarray]], noise: dict[str, list[float]],
              exclude: list[tuple[float, float]] | None = None, fallback_fwhm_px: dict[str, float] | None = None
              ) -> dict[str, dict]:
    """Per detector: the median normalised stamp of bright isolated stars in single exposures (each centred on its
    own centroid), plus the stamps of the individual stars for a bootstrap. exposures[band]: aligned images;
    noise[band]: their robust noise. `exclude`: (x, y) of the candidate itself. Detectors without stars fall back
    to a Gaussian of fallback_fwhm_px."""
    from scipy.ndimage import maximum_filter

    out = {}
    for band in BANDS:
        imgs = exposures.get(band) or []
        stamps, ivar = [], []
        for img, sig in list(zip(imgs, noise.get(band) or []))[:MAX_EXPOSURES_PER_BAND]:
            if not (sig and sig > 0):
                continue
            n = img.shape[0]
            s = np.nan_to_num((img - np.nanmedian(img)) / sig, nan=-np.inf)
            peaks = (s == maximum_filter(s, size=int(2 * STAR_ISOLATION + 1))) & (s >= STAR_SNR[0]) & (s <= STAR_SNR[1])
            ys, xs = np.nonzero(peaks)
            keep = (xs > HALF + 1) & (xs < n - HALF - 2) & (ys > HALF + 1) & (ys < n - HALF - 2)
            for x, y in zip(xs[keep], ys[keep]):
                if exclude and any(math.hypot(x - ex, y - ey) < HALF + 3 for ex, ey in exclude):
                    continue
                st = fill_holes(shift_stamp(img, float(x), float(y)), max_frac=0.1)
                if st is None:
                    continue
                pk = st[HALF, HALF] - _background(st)
                if np.nanmax(np.where(_R >= 2.5, st - _background(st), -np.inf)) > 0.1 * pk:
                    continue  # a neighbour or a cosmic ray in the stamp: it would bias the zero level
                dx, dy = centroid(st)
                st = fill_holes(shift_stamp(img, x + dx, y + dy), max_frac=0.1)
                st = _norm(st) if st is not None else None
                if st is not None:
                    stamps.append(np.nan_to_num(st))
                    ivar.append(interp_var([(x + dx, y + dy)])[0])
                if len(stamps) >= MAX_STARS_PER_BAND:
                    break
            if len(stamps) >= MAX_STARS_PER_BAND:
                break
        if len(stamps) >= 3:
            arr = np.stack(stamps)
            out[band] = {"stamp": np.median(arr, axis=0), "stars": arr, "source": f"{len(stamps)} field stars",
                         "interp_var": float(np.mean(ivar))}
        elif fallback_fwhm_px and fallback_fwhm_px.get(band):
            g = gaussian_stamp(fallback_fwhm_px[band])
            out[band] = {"stamp": g, "stars": g[None], "source": "Gaussian of the files' PSF FWHM (no stars)",
                         "interp_var": 0.0}
    return out


def interp_var(positions) -> np.ndarray:
    """Variance (px^2, both axes) that bilinear sampling at each position's sub-pixel phase f adds: f (1 - f) per
    axis. Stars sampled at random phases are smoothed by ~1/3 px^2 on average, an object on the grid centre by none,
    so the comparison corrects for it."""
    p = np.asarray(positions, float).reshape(-1, 2)
    f = p - np.floor(p)
    return (f * (1 - f)).sum(axis=1)


# ---------------------------------------------------------------- the coma test
def object_stamps(images: list[np.ndarray], positions: list[tuple[float, float]], noises: list[float]
                  ) -> tuple[np.ndarray, np.ndarray]:
    """Shifted stamps [k, 15, 15] and their weights 1/noise^2 (0 when a stamp has holes in the measured area)."""
    st = np.stack([shift_stamp(img, x, y) for img, (x, y) in zip(images, positions)])
    w = np.array([1.0 / n ** 2 if n and n > 0 else 0.0 for n in noises])
    core = np.isfinite(st[:, _R <= 2.0]).all(axis=1)
    holes = (~np.isfinite(st[:, _R <= R_ANN[1]])).mean(axis=1)
    return st, np.where(core & (holes <= 0.3), w, 0.0)


def _mean(st: np.ndarray, w: np.ndarray) -> np.ndarray | None:
    if w.sum() <= 0:
        return None
    sel = w > 0
    s, ww = st[sel], np.broadcast_to(w[sel, None, None], st[sel].shape)
    good = np.isfinite(s)
    den = np.where(good, ww, 0.0).sum(axis=0)
    with np.errstate(all="ignore"):
        return np.where(den > 0, np.where(good, s * ww, 0.0).sum(axis=0) / den, np.nan)


def coma_test(stamps: np.ndarray, weights: np.ndarray, bands: list[str], psfs: dict[str, dict], *,
              pixscale: float, antisolar_pa: float | None = None, motion_pa: float | None = None,
              jitter_px: float = 0.2, interp: np.ndarray | None = None, seed: int = 0) -> dict:
    """The coma evidence of one object from its per-exposure stamps (see the module docstring)."""
    rng = np.random.default_rng(seed)
    k = len(stamps)
    base = {"n_exposures": int((weights > 0).sum()), "verdict": "no data", "coma_score": None}
    if base["n_exposures"] < 2:
        if k >= 2 and (~np.isfinite(stamps[:, HALF, HALF])).mean() > 0.5:
            base["verdict"] = "core masked (saturated or flagged)"
        return base
    obj = _mean(stamps, weights)
    m = measures(obj)
    if m is None:
        return {**base, "verdict": "no flux"}
    # S/N of the object in the core aperture
    resid = stamps[weights > 0] - obj[None]
    pix_noise = float(np.nanstd(resid[:, _R >= R_BKG])) / math.sqrt(max(base["n_exposures"] - 1, 1))
    n_core = int((_R <= R_CORE).sum())
    snr = m["core"] / (pix_noise * math.sqrt(n_core)) if pix_noise > 0 else None
    # flux per detector -> PSF mixture
    flux = {}
    for b in set(bands):
        sel = np.array([bb == b for bb in bands]) & (weights > 0)
        if sel.any():
            mb = measures(_mean(stamps[sel], weights[sel]), recentre=False)
            flux[b] = max(mb["core"], 0.0) * float(weights[sel].sum()) if mb else 0.0
    have = {b: f for b, f in flux.items() if b in psfs}
    if not have or sum(have.values()) <= 0:
        return {**base, "verdict": "no reference PSF", "snr": snr}

    def mix(choice: dict[str, np.ndarray]) -> np.ndarray:
        tot = sum(have.values())
        return sum(choice[b] * (f / tot) for b, f in have.items())

    psf = mix({b: psfs[b]["stamp"] for b in have})
    p = measures(psf)
    if p is None:
        return {**base, "verdict": "no reference PSF", "snr": snr}
    # bootstrap: exposures for the object, stars for the PSF
    boots, pboots = [], []
    idx = np.nonzero(weights > 0)[0]
    for _ in range(BOOT):
        pick = rng.choice(idx, size=len(idx), replace=True)
        mb = measures(_mean(stamps[pick], weights[pick]))
        if mb:
            boots.append(mb)
        choice = {b: np.median(psfs[b]["stars"][rng.integers(0, len(psfs[b]["stars"]), len(psfs[b]["stars"]))],
                               axis=0) for b in have}
        pb = measures(mix(choice))
        if pb:
            pboots.append(pb)
    if len(boots) < BOOT // 3 or not pboots:
        return {**base, "verdict": "unstable (too faint or masked)", "snr": snr}

    def sd(rows, key):
        return float(np.std([r[key] for r in rows]))

    # what the PSF lacks: the object's track jitter and its (different) bilinear smoothing. A per-axis variance v
    # raises the weighted <r^2> of a Gaussian of variance s^2 by v w^4 / (s^2 + w^2)^2 (w: MOM_SIGMA)
    sp0 = gaussian_sigma(p["T"]) or 0.7
    gain = MOM_SIGMA ** 4 / (sp0 ** 2 + MOM_SIGMA ** 2) ** 2
    v_obj = float(np.average(interp[weights > 0], weights=weights[weights > 0])) if interp is not None else 0.0
    tot = sum(have.values())
    v_psf = sum(psfs[b].get("interp_var", 0.0) * f / tot for b, f in have.items())
    jit_t = gain * (2 * jitter_px ** 2 + v_obj - v_psf)
    t_psf = p["T"] + jit_t
    s_t = math.sqrt(sd(boots, "T") ** 2 + sd(pboots, "T") ** 2 + (SYS_FLOOR * t_psf) ** 2)
    z_size = (m["T"] - t_psf) / s_t if s_t > 0 else 0.0
    s_c = math.sqrt(sd(boots, "conc") ** 2 + sd(pboots, "conc") ** 2 + max(SYS_FLOOR * abs(p["conc"]), CONC_FLOOR) ** 2)
    z_conc = (m["conc"] - p["conc"]) / s_c if s_c > 0 else 0.0
    so, sp = gaussian_sigma(m["T"]), gaussian_sigma(t_psf)
    intrinsic = (2.3548 * math.sqrt(so ** 2 - sp ** 2) * pixscale) if so and sp and so > sp else 0.0
    # tail: the object's first azimuthal moment minus the PSF's
    ax, ay = m["ax"] - p["ax"], m["ay"] - p["ay"]
    sax = math.sqrt(sd(boots, "ax") ** 2 + sd(pboots, "ax") ** 2)
    say = math.sqrt(sd(boots, "ay") ** 2 + sd(pboots, "ay") ** 2)
    amp = math.hypot(ax, ay)
    z_tail = math.hypot(ax / sax if sax > 0 else 0.0, ay / say if say > 0 else 0.0)
    tail_pa = pa_of(-ax, ay)  # grid x grows to the west
    tail = {"pa_deg": round(tail_pa, 1), "amplitude": round(amp, 4), "sigma": round(z_tail, 2),
            "detected": bool(z_tail >= TAIL_SIGMA and amp >= TAIL_MIN_AMP)}
    aligned = False
    if antisolar_pa is not None:
        tail["antisolar_pa_deg"] = round(antisolar_pa, 1)
        tail["offset_from_antisolar_deg"] = round(angle_diff(tail_pa, antisolar_pa), 1)
        aligned |= tail["offset_from_antisolar_deg"] <= TAIL_ALIGN_DEG
    if motion_pa is not None:
        trail = (motion_pa + 180.0) % 360.0
        tail["trail_pa_deg"] = round(trail, 1)
        tail["offset_from_trail_deg"] = round(angle_diff(tail_pa, trail), 1)
        aligned |= tail["offset_from_trail_deg"] <= TAIL_ALIGN_DEG
    tail["aligned"] = bool(tail["detected"] and aligned)
    z_ext = max(z_size, z_conc)
    score = 1.0 / (1.0 + math.exp(-1.2 * (z_ext - 3.0)))
    if tail["aligned"]:
        score = 1.0 - (1.0 - score) * 0.5
    if snr is not None and snr < 8:
        verdict = "too faint"
    elif base["n_exposures"] < MIN_EXPOSURES:
        verdict, score = "too few exposures", None  # a bootstrap over a handful of frames is not an error
    elif z_ext >= 5:
        verdict = "extended"
    elif z_ext >= 3:
        verdict = "possibly extended"
    elif z_ext < 2 and (snr or 0) >= 20:
        verdict = "point-like"
    else:
        verdict = "inconclusive"
    return {"n_exposures": base["n_exposures"], "snr": round(snr, 1) if snr else None, "verdict": verdict,
            "coma_score": round(score, 3) if score is not None else None, "extent_sigma": round(z_ext, 2),
            "size": {"sigma": round(z_size, 2), "T_px2": round(m["T"], 4), "T_psf_px2": round(t_psf, 4),
                     "fwhm_arcsec": round(2.3548 * so * pixscale, 2) if so else None,
                     "psf_fwhm_arcsec": round(2.3548 * sp * pixscale, 2) if sp else None,
                     "intrinsic_fwhm_arcsec": round(intrinsic, 2)},
            "concentration": {"sigma": round(z_conc, 2), "ratio": round(m["conc"], 4), "psf_ratio": round(p["conc"], 4)},
            "tail": tail, "centroid_offset_px": [round(m["cx"], 3), round(m["cy"], 3)],
            "psf_source": {b: psfs[b]["source"] for b in have}, "jitter_px": jitter_px,
            "stamp": _list(obj), "psf_stamp": _list(psf * (m["core"] / p["core"] if p["core"] else 1.0))}


def _list(st: np.ndarray | None) -> list | None:
    if st is None:
        return None
    return [[None if not np.isfinite(v) else float(f"{v:.4g}") for v in row] for row in st]
