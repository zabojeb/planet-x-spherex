"""Shared pieces of the solar-system analyses: reading the object-frame exposures of a co-moving sequence and
measuring them in circular apertures centred on the object."""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass

import numpy as np

from ..analysis.common import SequenceContext
from ..bands import BANDS
from ..errors import NotFound
from ..photometry import SYSTEMATIC_FLOOR, mjy_per_unit
from ..pipeline import results

MAX_MASKED_IN_APERTURE = 0.35  # an aperture with more masked area than this is not measured
# Noise of a median of k templates relative to one exposure (efficiency of the median, 1.57 = pi/2).
TEMPLATE_NOISE = {k: math.sqrt(1.0 + (math.pi / 2) / k) for k in range(1, 9)}


def require_comoving(ctx: SequenceContext) -> None:
    if (ctx.meta.get("grid") or {}).get("frame") != "comoving":
        raise NotFound("this analysis needs a co-moving sequence of a solar-system object (build it with an "
                       "object name)")


@dataclass
class ObjectFrames:
    """Every exposure of a co-moving sequence on its object-centred grid: images [frames, n, n] (float32) in the
    order of meta['exposures'], whether they are static-sky subtracted, and the per-pixel noise of each."""

    images: np.ndarray
    clean: bool
    noise: np.ndarray  # MJy/sr per pixel, per exposure
    usable: np.ndarray  # bool per exposure


def load_frames(ctx: SequenceContext, prefer_clean: bool = True) -> ObjectFrames:
    meta = ctx.meta
    exps = meta["exposures"]
    n = ctx.grid.n
    arrays = meta.get("arrays") or {}
    name = "clean_exposures" if prefer_clean and arrays.get("clean_exposures") else "exposures"
    if not arrays.get(name):
        raise NotFound("single exposures are not stored for this sequence (field too large); rebuild it with a "
                       "smaller field of view")
    images = np.full((len(exps), n, n), np.nan, np.float32)
    for ep in range(len(meta["epochs"])):
        for b in BANDS:
            try:
                stack, idx = results.read_exposures(ctx.dir, meta, ep, b, name=name)
            except NotFound:
                continue
            images[idx[0]: idx[-1] + 1] = stack
    clean = name == "clean_exposures"
    noise = np.array([float(e.get("rms") or np.nan) * (TEMPLATE_NOISE.get(int(e.get("ss_n") or 0), 1.0) if clean
                                                        else 1.0) for e in exps])
    usable = np.array([np.isfinite(images[i]).any() and (not clean or e.get("ss_method", "none") != "none")
                       for i, e in enumerate(exps)])
    return ObjectFrames(images, clean, noise, usable)


def _radius_map(n: int, x: float, y: float) -> np.ndarray:
    yy, xx = np.mgrid[0:n, 0:n]
    return np.hypot(xx - x, yy - y)


def fill_rings(img: np.ndarray, x: float, y: float) -> tuple[np.ndarray, np.ndarray]:
    """Masked (NaN) pixels replaced by the mean of their 1-pixel radial ring about (x, y): a coma is close to
    azimuthally symmetric, so this keeps aperture sums unbiased where stars were masked. Returns (filled, masked)."""
    n = img.shape[0]
    r = _radius_map(n, x, y)
    ring = np.floor(r).astype(int)
    bad = ~np.isfinite(img)
    out = img.copy()
    if bad.any():
        good = ~bad
        sums = np.bincount(ring[good], weights=img[good], minlength=ring.max() + 1)
        cnts = np.bincount(ring[good], minlength=ring.max() + 1)
        with np.errstate(invalid="ignore", divide="ignore"):
            mean = np.where(cnts > 0, sums / np.maximum(cnts, 1), np.nan)
        out[bad] = mean[ring[bad]]
    return out, bad


def aperture_flux(img: np.ndarray, x: float, y: float, r_px: float, pixscale: float, noise: float,
                  sub: int = 5) -> dict | None:
    """Flux (mJy) of an MJy/sr image inside a circle of r_px pixels about (x, y), with sub x sub sub-pixel weights.
    Masked pixels are ring-filled; None when more than MAX_MASKED_IN_APERTURE of the aperture is masked or it
    leaves the grid."""
    n = img.shape[0]
    if r_px <= 0 or x - r_px < -0.5 or y - r_px < -0.5 or x + r_px > n - 0.5 or y + r_px > n - 0.5:
        return None
    filled, bad = fill_rings(img, x, y)
    off = (np.arange(sub) + 0.5) / sub - 0.5
    lo, hi = int(max(0, math.floor(y - r_px - 1))), int(min(n, math.ceil(y + r_px + 2)))
    lx, hx = int(max(0, math.floor(x - r_px - 1))), int(min(n, math.ceil(x + r_px + 2)))
    yy, xx = np.mgrid[lo:hi, lx:hx]
    dx = xx[..., None, None] + off[None, None, None, :] - x
    dy = yy[..., None, None] + off[None, None, :, None] - y
    w = (np.hypot(dx, dy) <= r_px).mean(axis=(2, 3))
    area = float(w.sum())
    if area <= 0:
        return None
    cut, cbad = filled[lo:hi, lx:hx], bad[lo:hi, lx:hx]
    masked = float((w * cbad).sum() / area)
    if masked > MAX_MASKED_IN_APERTURE or not np.isfinite(cut[w > 0]).all():
        return None
    k = mjy_per_unit(pixscale ** 2)
    flux = float(np.sum(w * cut)) * k
    err = float(noise * math.sqrt(area)) * k if np.isfinite(noise) else float("nan")
    return {"flux_mjy": flux, "err_mjy": float(np.hypot(err, SYSTEMATIC_FLOOR * abs(flux))), "stat_err_mjy": err,
            "area_px": area, "masked_frac": masked}


def ring_flux(img: np.ndarray, x: float, y: float, r_in: float, r_out: float, pixscale: float, noise: float,
              max_masked: float = 0.4) -> dict | None:
    """Flux (mJy) in the annulus r_in <= r < r_out (pixel centres) about (x, y), masked pixels ring-filled: the
    relative photometry of a saturated object (ADR-0020) measured on a clean image."""
    r = _radius_map(img.shape[0], x, y)
    sel = (r >= r_in) & (r < r_out)
    if sel.sum() < 4 or r_out + 1 > min(x, y, img.shape[0] - 1 - x, img.shape[0] - 1 - y):
        return None
    filled, bad = fill_rings(img, x, y)
    masked = float(bad[sel].mean())
    if masked > max_masked or not np.isfinite(filled[sel]).all():
        return None
    k = mjy_per_unit(pixscale ** 2)
    flux = float(filled[sel].sum()) * k
    err = float(noise * math.sqrt(sel.sum())) * k if np.isfinite(noise) else float("nan")
    return {"flux_mjy": flux, "err_mjy": float(np.hypot(err, SYSTEMATIC_FLOOR * abs(flux))), "masked_frac": masked}


def radial_profile(img: np.ndarray, x: float, y: float, r_max: float, step: float = 1.0) -> dict:
    """Azimuthal mean surface brightness (MJy/sr) in rings of `step` pixels, with the standard error per ring."""
    n = img.shape[0]
    r = _radius_map(n, x, y)
    edges = np.arange(0.0, r_max + step, step)
    mid, mean, err, npx = [], [], [], []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        for a, b in zip(edges[:-1], edges[1:]):
            sel = (r >= a) & (r < b) & np.isfinite(img)
            v = img[sel]
            mid.append(0.5 * (a + b))
            npx.append(int(v.size))
            mean.append(float(np.mean(v)) if v.size else float("nan"))
            err.append(float(np.std(v) / math.sqrt(v.size)) if v.size > 1 else float("nan"))
    return {"r_px": mid, "sb": mean, "err": err, "npix": npx}


def weighted_mean(values, errors) -> tuple[float, float]:
    v, e = np.asarray(values, float), np.asarray(errors, float)
    ok = np.isfinite(v) & np.isfinite(e) & (e > 0)
    if not ok.any():
        return float("nan"), float("nan")
    w = 1.0 / e[ok] ** 2
    return float(np.sum(w * v[ok]) / np.sum(w)), float(1.0 / math.sqrt(np.sum(w)))
