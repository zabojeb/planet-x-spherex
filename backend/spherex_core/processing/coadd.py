"""Co-add aligned exposures with per-pixel outlier rejection."""

from __future__ import annotations

import warnings

import numpy as np


def coadd(stack: np.ndarray, weights: np.ndarray, noise: float, clip: float = 5.0) -> tuple[np.ndarray, np.ndarray]:
    """Weighted mean of aligned frames; per-pixel values beyond clip x max(MAD, noise) from the median are
    rejected (asteroids, unflagged cosmic rays). Returns (mean, number of frames used per pixel)."""
    data = np.array(stack, dtype=np.float32, copy=True)
    if data.shape[0] >= 4:
        with np.errstate(invalid="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)  # all-NaN slices
            med = np.nanmedian(data, axis=0)
            mad = 1.4826 * np.nanmedian(np.abs(data - med), axis=0)
            floor = noise if np.isfinite(noise) and noise > 0 else 0.0
            thresh = clip * np.maximum(mad, floor)
            data[np.abs(data - med) > thresh] = np.nan
    good = np.isfinite(data)
    w = np.asarray(weights, dtype=np.float64)[:, None, None] * good
    wsum = w.sum(axis=0)
    total = np.where(good, data, 0.0).astype(np.float64) * w
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.where(wsum > 0, total.sum(axis=0) / wsum, np.nan)
    return mean.astype(np.float32), good.sum(axis=0).astype(np.uint16)


def fill_holes(mean: np.ndarray, coverage: np.ndarray, raw_stack: np.ndarray) -> np.ndarray:
    """Fill pixels that no clean frame covered (coverage 0) with the median of the unmasked values.

    Saturated star cores carry masked bits (SUR_ERROR, TRANSIENT, ...) in every exposure, which used to
    leave a hole of zero coverage at the star. raw_stack holds the same aligned frames without masking.
    Modifies mean in place; returns the boolean map of filled pixels (to be flagged FILLED).
    """
    hole = coverage == 0
    if not hole.any():
        return hole
    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)  # all-NaN columns outside the footprint
        fill = np.nanmedian(raw_stack[:, hole], axis=0)
    ok = np.isfinite(fill)
    filled = np.zeros_like(hole)
    idx = np.nonzero(hole)
    mean[idx[0][ok], idx[1][ok]] = fill[ok]
    filled[idx[0][ok], idx[1][ok]] = True
    return filled
