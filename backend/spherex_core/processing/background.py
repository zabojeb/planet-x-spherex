"""Robust background level and noise of a cutout."""

from __future__ import annotations

import numpy as np
from astropy.stats import sigma_clipped_stats


def background(img: np.ndarray) -> tuple[float, float]:
    """Sigma-clipped median and standard deviation of the finite pixels."""
    finite = img[np.isfinite(img)]
    if finite.size < 10:
        return 0.0, float("nan")
    _, med, std = sigma_clipped_stats(finite, sigma=3.0, maxiters=5)
    return float(med), float(std)
