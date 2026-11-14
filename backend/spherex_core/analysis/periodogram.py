"""Lomb-Scargle periodograms of relative light curves, single-band or multiband, with phase folding.

SPHEREx samples a source in visits about six months apart, with many exposures (one per wavelength) inside
each visit, so long periods are constrained by the visits and short ones by the dense sampling inside them.
The multiband model (astropy LombScargleMultiband) lets each detector have its own amplitude, which suits
stars whose variability changes with wavelength (Miras such as R Dra).
"""

from __future__ import annotations

import numpy as np
from astropy.timeseries import LombScargle, LombScargleMultiband

from .lightcurve import relative_fluxes

MIN_POINTS = 6
DEFAULT_MIN_PERIOD = 0.5  # days
MAX_CURVE_POINTS = 2000  # periodogram samples returned (maxima kept when downsampling)
MODEL_POINTS = 200


def _downsample_max(freq: np.ndarray, power: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Keep the maximum of each of n chunks so peaks survive."""
    if freq.size <= n:
        return freq, power
    edges = np.linspace(0, freq.size, n + 1).astype(int)
    idx = np.array([a + int(np.argmax(power[a:b])) for a, b in zip(edges[:-1], edges[1:]) if b > a])
    return freq[idx], power[idx]


def _peaks(freq: np.ndarray, power: np.ndarray, n: int, min_sep: float) -> list[int]:
    """Indices of the n highest local maxima at least min_sep apart in frequency."""
    if freq.size < 3:
        return [int(np.argmax(power))] if freq.size else []
    loc = np.flatnonzero((power[1:-1] >= power[:-2]) & (power[1:-1] >= power[2:])) + 1
    out: list[int] = []
    for i in loc[np.argsort(power[loc])[::-1]]:
        if all(abs(freq[i] - freq[j]) >= min_sep for j in out):
            out.append(int(i))
        if len(out) == n:
            break
    return out


def periodogram(photometry: list[dict], *, method: str = "standard", band: str | None = None,
                min_period: float | None = None, max_period: float | None = None, include_flagged: bool = False,
                samples_per_peak: int = 10, n_peaks: int = 5) -> dict:
    """Periodogram of photometry records (meta.json shape, fluxes normalised per wavelength bin first).

    method: "standard" (one model for all points) or "multiband" (one per detector). Raises ValueError when
    the selection leaves too few points or the period range is empty.
    """
    if method not in ("standard", "multiband"):
        raise ValueError("method must be 'standard' or 'multiband'")
    pts = [p for p in relative_fluxes(photometry)
           if (include_flagged or not p.get("flagged")) and (band is None or p["band"] == band)]
    if len(pts) < MIN_POINTS:
        raise ValueError(f"need at least {MIN_POINTS} usable measurements, have {len(pts)}")
    pts.sort(key=lambda p: p["mjd"])
    t = np.array([p["mjd"] for p in pts], float)
    y = np.array([p["rel"] for p in pts], float)
    errs = [p["rel_err"] for p in pts]
    dy = np.array(errs, float) if all(e is not None and e > 0 for e in errs) else None
    bands = np.array([p["band"] for p in pts])
    baseline = float(t.max() - t.min())
    if baseline <= 0:
        raise ValueError("all measurements are at the same time")
    pmin = float(min_period or DEFAULT_MIN_PERIOD)
    pmax = float(max_period or baseline)
    if not 0 < pmin < pmax:
        raise ValueError("min_period must be positive and below max_period")
    fmin, fmax = 1.0 / pmax, 1.0 / pmin

    if method == "multiband" and len(set(bands)) > 1:
        ls = LombScargleMultiband(t, y, bands, dy)
    else:
        method = "standard"
        ls = LombScargle(t, y, dy)
    freq, power = ls.autopower(minimum_frequency=fmin, maximum_frequency=fmax, samples_per_peak=samples_per_peak)
    freq, power = np.asarray(freq, float), np.asarray(power, float)
    best = int(np.argmax(power))
    f_best = float(freq[best])
    fap = None
    if method == "standard":
        fap = float(ls.false_alarm_probability(power[best], minimum_frequency=fmin, maximum_frequency=fmax,
                                               method="baluev"))
    peak_idx = _peaks(freq, power, n_peaks, min_sep=1.0 / baseline)
    period = 1.0 / f_best

    t0 = float(t.min())
    phase = ((t - t0) / period) % 1.0
    grid = np.linspace(0.0, 1.0, MODEL_POINTS)
    model: dict = {"phase": grid.tolist()}
    if method == "standard":
        model["rel"] = ls.model(t0 + grid * period, f_best).tolist()
    else:
        ub = np.array(sorted(set(bands)))
        curves = np.atleast_2d(ls.model(t0 + grid * period, f_best, bands_fit=ub))  # [bands, phases]
        model["rel_by_band"] = {str(b): curves[i].tolist() for i, b in enumerate(ub)}
    fs, ps = _downsample_max(freq, power, MAX_CURVE_POINTS)
    order = np.argsort(1.0 / fs)
    return {
        "method": method, "band": band, "n_points": len(pts), "bands": sorted(set(bands.tolist())),
        "include_flagged": include_flagged, "relative_points": sum(bool(p.get("relative")) for p in pts),
        "baseline_days": baseline, "min_period_days": pmin, "max_period_days": pmax,
        "best": {"period_days": period, "frequency_per_day": f_best, "power": float(power[best]),
                 "false_alarm_probability": fap},
        "peaks": [{"period_days": 1.0 / float(freq[i]), "power": float(power[i])} for i in peak_idx],
        "periodogram": {"period_days": (1.0 / fs[order]).tolist(), "power": ps[order].tolist()},
        "t0_mjd": t0,
        "folded": [{"index": p.get("index"), "mjd": p["mjd"], "phase": float(ph), "rel": p["rel"],
                    "rel_err": p["rel_err"], "band": p["band"], "wl": p["wl"], "flagged": bool(p.get("flagged")),
                    "relative": bool(p.get("relative"))} for p, ph in zip(pts, phase)],
        "model": model,
    }
