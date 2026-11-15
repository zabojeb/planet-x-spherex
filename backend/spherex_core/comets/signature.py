"""Spectral signature of activity in the per-exposure photometry of a moving source.

Every SPHEREx exposure samples the object at one wavelength (the linear variable filter), so a few dozen exposures
over a visit make a low-resolution 0.75-5 um spectrum. Against the reflected-sunlight continuum we test:

* CO2 nu3 4.26 um and CO 1-0 4.67 um fluorescence (gas coma; CO2 dominates beyond ~3 au, e.g. 3I/ATLAS),
* H2O 2.7 um fluorescence and water-ice absorption near 3.0 um (icy grains),

with the band fits of solar.comet (weighted linear continuum through sidebands, Monte Carlo errors). The fits use
local sidebands, so a smooth thermal tail (near-Sun asteroids at 4-5 um) does not mimic a band; the 4-5 um
reflectance excess is reported separately as context. A 4.26 um excess on one detector edge (D5 ends at 4.4 um)
is the main systematic: the result lists the channels each fit used.
"""

from __future__ import annotations

import math

import numpy as np

from ..bands import BANDS
from ..solar import sun
from ..solar.comet import FEATURES, band_fit

TESTED = ("co2", "co", "h2o_gas", "h2o_ice_3.0")
GAS = ("co2", "co")
MIN_POINTS = 6


def spectrum_points(rows: list[dict]) -> dict:
    """Arrays (wl, dwl, flux_mjy, err_mjy, band) of the usable per-exposure measurements."""
    ok = [r for r in rows if r.get("flux_mjy") is not None and r.get("err_mjy") and r["err_mjy"] > 0
          and np.isfinite(r["flux_mjy"]) and r.get("wl")]
    return {"wl": np.array([r["wl"] for r in ok], float), "dwl": np.array([r.get("dwl") or r["wl"] / 40 for r in ok], float),
            "flux": np.array([r["flux_mjy"] for r in ok], float), "err": np.array([r["err_mjy"] for r in ok], float),
            "band": [r.get("band") for r in ok]}


def _reflectance(sp: dict) -> tuple[np.ndarray, np.ndarray]:
    s = np.asarray(sun.sun_fnu(sp["wl"], sp["dwl"]), float)
    return sp["flux"] / s, sp["err"] / s


def signature(rows: list[dict], seed: int = 0) -> dict:
    """Band fits, a 4-5 um reflectance excess and a spectral evidence term for the comet-likeness score."""
    sp = spectrum_points(rows)
    out = {"n_points": int(sp["wl"].size), "bands": [], "gas_sigma": None, "llr": 0.0, "verdict": "no data"}
    if sp["wl"].size < MIN_POINTS:
        return out
    rng = np.random.default_rng(seed)
    feats = {f["id"]: f for f in FEATURES}
    for fid in TESTED:
        r = band_fit(sp["wl"], sp["dwl"], sp["flux"], sp["err"], feats[fid], rng)
        r = {k: r.get(k) for k in ("id", "label", "kind", "value", "err", "snr", "detected", "n_window", "n_side",
                                   "unit", "continuum_mjy", "note")}
        out["bands"].append(r)
    gas = [b for b in out["bands"] if b["id"] in GAS and b["snr"] is not None]
    if gas:
        out["gas_sigma"] = round(max(b["snr"] for b in gas), 2)
    # continuum: reflectance at 3.9-5 um (outside the gas windows) over 1-2.4 um
    r, e = _reflectance(sp)
    wl = sp["wl"]
    win = np.zeros(wl.shape, bool)
    for fid in GAS:
        lo, hi = feats[fid]["window"]
        win |= (wl >= lo) & (wl <= hi)
    short = (wl >= 1.0) & (wl <= 2.4)
    long = (wl >= 3.9) & ~win
    if short.sum() >= 3 and long.sum() >= 3:
        ms, ml = float(np.median(r[short])), float(np.median(r[long]))
        if ms > 0:
            es = 1.2533 * float(np.std(r[short])) / math.sqrt(short.sum())
            el = 1.2533 * float(np.std(r[long])) / math.sqrt(long.sum())
            ratio = ml / ms
            out["long_excess"] = {"ratio": round(ratio, 3),
                                  "err": round(ratio * math.hypot(es / ms, el / ml if ml else 0.0), 3),
                                  "note": "reflectance 3.9-5 um (gas windows excluded) over 1-2.4 um: > 1 from "
                                          "thermal emission (warm, near-Sun bodies) or red dust; not a gas band"}
    # per detector median for a compact spectrum
    snippet = []
    for b in BANDS:
        sel = np.array([bb == b for bb in sp["band"]])
        if sel.sum():
            snippet.append({"band": b, "wl": round(float(np.median(wl[sel])), 3),
                            "flux_mjy": float(f"{np.median(sp['flux'][sel]):.4g}"),
                            "err_mjy": float(f"{1.2533 * np.median(sp['err'][sel]) / math.sqrt(sel.sum()):.3g}"),
                            "n": int(sel.sum())})
    out["per_detector"] = snippet
    order = np.argsort(wl)
    out["points"] = [[round(float(wl[i]), 4), float(f"{sp['flux'][i]:.4g}"), float(f"{sp['err'][i]:.3g}")]
                     for i in order[:160]]
    out["llr"], out["verdict"] = evidence(out)
    return out


def evidence(sig: dict) -> tuple[float, str]:
    """Log-likelihood-ratio contribution of the spectrum to the comet-likeness, and a one-word verdict."""
    b = {x["id"]: x for x in sig.get("bands", [])}
    co2 = (b.get("co2") or {}).get("snr") or 0.0
    co = (b.get("co") or {}).get("snr") or 0.0
    ice = b.get("h2o_ice_3.0") or {}
    llr = 0.0
    if co2 >= 4:
        llr += 2.5
    elif co2 >= 3:
        llr += 1.5
    if co >= 4:
        llr += 1.5
    elif co >= 3:
        llr += 0.8
    thermal = ((sig.get("long_excess") or {}).get("ratio") or 0) > 1.5  # a warm tail bends the 3 um continuum
    if (ice.get("snr") or 0) >= 3 and (ice.get("value") or 0) > 0 and not thermal:
        llr += 0.5
    if llr >= 2.5:
        verdict = "gas emission"
    elif llr > 0:
        verdict = "possible gas or ice"
    elif sig.get("n_points", 0) >= 20:
        verdict = "no band detected"
    else:
        verdict = "too few points"
    return round(min(llr, 4.0), 2), verdict
