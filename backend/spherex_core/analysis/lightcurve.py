"""Relative fluxes for light curves and periodograms.

Every exposure sees a different wavelength, so brightness changes are measured against the source's own
median spectrum: each measurement is divided by the median flux of its wavelength bin over all epochs. This
is the exact normalisation of the web client's light curve (web/src/components/chart/LightCurveChart.tsx):
102 log-spaced bins over the observed wavelength range, calibrated and relative-only (ring) measurements
binned separately, the reference taken from unflagged points when the bin has any, and the upper median
(sorted[n // 2]).
"""

from __future__ import annotations

import math
from collections import defaultdict

N_BINS = 102


def _upper_median(values: list[float]) -> float:
    if not values:
        return math.nan
    s = sorted(values)
    return s[len(s) // 2]


def relative_fluxes(photometry: list[dict]) -> list[dict]:
    """Photometry records (meta.json shape) with `rel` and `rel_err` added; unusable records are dropped."""
    usable = [p for p in photometry if p.get("flux_mjy") is not None and math.isfinite(p["flux_mjy"])
              and p.get("wl") and p["wl"] > 0]
    if not usable:
        return []
    wls = [p["wl"] for p in usable]
    lo, hi = math.log(min(wls) * 0.999), math.log(max(wls) * 1.001)

    def bin_of(p: dict) -> int:
        k = min(N_BINS - 1, math.floor((math.log(p["wl"]) - lo) / (hi - lo) * N_BINS))
        return k + (N_BINS if p.get("relative") else 0)

    clean: dict[int, list[float]] = defaultdict(list)
    anyb: dict[int, list[float]] = defaultdict(list)
    for p in usable:
        k = bin_of(p)
        anyb[k].append(p["flux_mjy"])
        if not p.get("flagged"):
            clean[k].append(p["flux_mjy"])
    out = []
    for p in usable:
        k = bin_of(p)
        ref = _upper_median(clean[k]) if clean[k] else _upper_median(anyb[k])
        if not (ref > 0):
            continue
        err = p.get("err_mjy")
        out.append({**p, "rel": p["flux_mjy"] / ref, "rel_err": err / ref if err else None})
    return out
