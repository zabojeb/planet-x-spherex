"""Spectral variability between epochs: ratio spectra with per-bin significance.

Each epoch is binned on the common grid; every epoch is divided by a reference epoch bin by bin. Ratios
are scale-free, so ring measurements of saturated stars (relative within a detector, ADR-0020) are as
usable as calibrated ones, as long as numerator and denominator use the same kind. A 3 % systematic is
added in quadrature to each bin's error, matching the scatter of constant stars between epochs.
"""

from __future__ import annotations

import math

import numpy as np

from ..bands import BANDS
from .assemble import by_epoch

SYSTEMATIC = 0.03


MIN_SNR = 3.0  # both bins must be detected; ratios of noise or of a blended faint source mean nothing


def _ratio(num: dict, den: dict) -> dict | None:
    a, b = num["flux_mjy"], den["flux_mjy"]
    if not (b and b > 0 and a is not None and a > 0):
        return None
    if a < MIN_SNR * num["err_mjy"] or b < MIN_SNR * den["err_mjy"]:
        return None
    ea = math.hypot(num["err_mjy"], SYSTEMATIC * abs(a))
    eb = math.hypot(den["err_mjy"], SYSTEMATIC * abs(b))
    r = a / b
    er = abs(r) * math.hypot(ea / a if a else 0.0, eb / b)
    return {"ratio": r, "err": er, "z": (r - 1.0) / er if er > 0 else None}


def variability(records: list[dict], reference: int | None = None, include_flagged: bool = False) -> dict:
    """Ratio of each epoch's spectrum to the reference epoch (default: the epoch with most bins)."""
    kinds = {}
    for relative in (False, True):
        per = by_epoch(records, relative=relative, include_flagged=include_flagged)
        per = {e: b for e, b in per.items() if b}
        if per:
            kinds["relative" if relative else "calibrated"] = per
    if not kinds:
        return {"available": False, "reason": "no usable measurements"}
    epochs = sorted({e for per in kinds.values() for e in per})
    if len(epochs) < 2:
        return {"available": False, "reason": "fewer than two epochs with measurements", "epochs": epochs}
    counts = {e: sum(len(per.get(e, [])) for per in kinds.values()) for e in epochs}
    ref = reference if reference in counts else max(counts, key=counts.get)
    out_epochs = []
    for e in epochs:
        if e == ref:
            continue
        ratios = []
        for kind, per in kinds.items():
            num, den = per.get(e, []), per.get(ref, [])
            den_by = {b["bin"]: b for b in den}
            for b in num:
                d = den_by.get(b["bin"])
                r = _ratio(b, d) if d else None
                if r:
                    ratios.append({"band": b["band"], "bin": b["bin"], "wl": b["wl"], "kind": kind, **r})
        ratios.sort(key=lambda x: x["wl"])
        per_band = {}
        for band in BANDS:
            sel = [x for x in ratios if x["band"] == band]
            if not sel:
                continue
            w = np.array([1 / x["err"] ** 2 for x in sel])
            r = np.array([x["ratio"] for x in sel])
            mean = float((w * r).sum() / w.sum())
            err = float(math.sqrt(1 / w.sum()))
            err = max(err, float(np.std(r, ddof=1) / math.sqrt(r.size))) if r.size > 1 else err
            per_band[band] = {"ratio": mean, "err": err, "z": (mean - 1) / err if err > 0 else None,
                              "n_bins": len(sel), "kind": sel[0]["kind"]}
        z = np.array([x["z"] for x in ratios if x["z"] is not None])
        chi2 = float((z ** 2).sum() / z.size) if z.size else None
        sig_bands = [b for b, v in per_band.items() if v["z"] is not None and abs(v["z"]) >= 3]
        out_epochs.append({"epoch": e, "ratios": ratios, "per_band": per_band, "chi2_red_constant": chi2,
                           "variable": bool(sig_bands), "significant_bands": sig_bands})
    return {"available": True, "reference_epoch": ref, "epochs": out_epochs,
            "kinds": sorted(kinds), "systematic": SYSTEMATIC}
