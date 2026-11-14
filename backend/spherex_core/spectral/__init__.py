"""Spectral analysis of SPHEREx photometry: binned spectra, features, indices, classification, synthetic
photometry and spectral variability.

Inputs are photometry records shaped like meta.json `photometry` (wl, band, flux_mjy, err_mjy, epoch,
flagged, relative), so the same functions serve a built sequence's target and any position measured with
POST /api/sequences/{id}/photometry.

Module map: grid (the common 102-bin grid), assemble (binning), features (continuum, curated features,
automatic detection, indices), templates (Pickles + SpeX standards on the grid), classify (blackbody,
templates, IR index, IR excess, summary), synphot (2MASS/WISE synthetic photometry vs catalogues),
variability (epoch ratio spectra).
"""

from __future__ import annotations

import math

from .assemble import bin_spectrum, by_epoch, epochs_of
from .classify import classify
from .features import FEATURES, continuum, detect_features, indices, measure_features
from .grid import GRID
from .synphot import compare_with_catalogs, synthetic
from .variability import variability

__all__ = ["spectrum_report", "classify_report", "variability_report", "analyze", "GRID", "FEATURES"]


def _note_kinds(records: list[dict]) -> list[str]:
    notes = []
    n = len(records)
    rel = sum(1 for r in records if r.get("relative"))
    flg = sum(1 for r in records if r.get("flagged") and not r.get("relative"))
    if n and rel / n > 0.5:
        notes.append(f"{rel} of {n} measurements are relative ring fluxes of a saturated star: no calibrated "
                     "spectrum; use the variability (ratio) analysis instead.")
    if n and flg / n > 0.3:
        notes.append(f"{flg} of {n} calibrated measurements are flagged and left out.")
    return notes


def spectrum_report(records: list[dict], include_flagged: bool = False) -> dict:
    """Binned spectrum overall and per epoch, pseudo-continuum, curated features, detections and indices."""
    overall = bin_spectrum(records, include_flagged=include_flagged)
    cont = continuum(overall)
    if cont is not None:
        for b, c in zip(overall, cont):
            b["continuum_mjy"] = float(c) if math.isfinite(c) else None
    epochs = [{"epoch": e, "bins": bins, "indices": indices(bins)}
              for e, bins in by_epoch(records, include_flagged=include_flagged).items()]
    relative = bin_spectrum(records, relative=True, include_flagged=include_flagged)
    return {
        "grid": {"bins_per_detector": 17, "n_bins": GRID.size},
        "n_measurements": len(records),
        "bins": overall,
        "relative_bins": relative,
        "epochs": epochs,
        "features": measure_features(overall, cont),
        "detections": detect_features(overall, cont),
        "continuum": continuum_quality(overall, cont),
        "indices": indices(overall),
        "notes": _note_kinds(records),
    }


def continuum_quality(bins: list[dict], cont) -> dict:
    """How well a smooth pseudo-continuum describes the spectrum. When many bins sit far below it (cool dwarfs,
    where molecular bands cover most of the range) the automatic detections are not meaningful; rely on the
    curated features and indices instead."""
    if cont is None or not bins:
        return {"available": False}
    import numpy as np

    f = np.array([b["flux_mjy"] for b in bins])
    e = np.hypot(np.array([b["err_mjy"] for b in bins]), 0.02 * np.abs(f))
    z = (f - cont) / e
    below = float(np.mean(z < -3))
    return {"available": True, "fraction_bins_below_3sigma": below, "reliable": below < 0.25,
            "note": None if below < 0.25 else "Strong molecular absorption over much of the range: the "
            "pseudo-continuum is not a true continuum, so automatic detections are not meaningful."}


def match_catalog(sources: list[dict], position_at, radius_arcsec: float = 4.0) -> dict | None:
    """Nearest catalogue source to the target, comparing at each source's own epoch (proper motion)."""
    from ..timeutil import year_to_mjd

    best, best_d = None, radius_arcsec
    for s in sources:
        if s.get("ra") is None:
            continue
        ra_t, dec_t = position_at(year_to_mjd(s.get("epoch") or 2000.0))
        cosd = math.cos(math.radians(dec_t))
        d = 3600 * math.hypot((s["ra"] - ra_t) * cosd, s["dec"] - dec_t)
        if d < best_d:
            best, best_d = {**s, "separation_arcsec": d}, d
    return best


def classify_report(records: list[dict], target=None, catalogs: bool = False) -> dict:
    """Classification of the overall calibrated spectrum, synthetic photometry and (optionally) a
    comparison with 2MASS and CatWISE magnitudes of the matched catalogue source."""
    overall = bin_spectrum(records)
    out = classify(overall)
    synth = synthetic(overall) if overall else {}
    out["synthetic_photometry"] = synth
    out["catalog_comparison"] = None
    if catalogs and target is not None and overall:
        from .. import catalogs as cat

        matches = {}
        errors = {}
        for name in ("2mass", "catwise"):
            try:
                ra0, dec0 = target.position_at(year_to_mjd_safe(2010.0))
                matches[name] = match_catalog(cat.cone(name, float(ra0), float(dec0), 1.5 / 60), target.position_at)
            except Exception as exc:  # noqa: BLE001 - catalogue outages must not break classification
                matches[name], errors[name] = None, str(exc)
        out["catalog_comparison"] = {"bands": compare_with_catalogs(synth, matches),
                                     "matches": matches, "errors": errors or None}
    out["notes"] = _note_kinds(records)
    return out


def year_to_mjd_safe(year: float) -> float:
    from ..timeutil import year_to_mjd

    return float(year_to_mjd(year))


def variability_report(records: list[dict], reference: int | None = None, include_flagged: bool = False) -> dict:
    return variability(records, reference=reference, include_flagged=include_flagged)


def analyze(records: list[dict], target=None, catalogs: bool = False, reference: int | None = None) -> dict:
    """Everything at once (for a clicked source): spectrum, classification and variability."""
    return {"spectrum": spectrum_report(records),
            "classification": classify_report(records, target=target, catalogs=catalogs),
            "variability": variability_report(records, reference=reference),
            "epochs": epochs_of(records)}


def reference() -> dict:
    """Static description of the analysis: bin grid, curated features, index windows, template library."""
    from dataclasses import asdict

    from .features import INDICES
    from .templates import library

    try:
        lib = library()
        templates = [{"name": n, "sptype": s, "family": f, "source": src}
                     for n, s, f, src in zip(lib.names, lib.sptype, lib.family, lib.source)]
        lib_error = None
    except Exception as exc:  # noqa: BLE001
        templates, lib_error = [], str(exc)
    return {
        "grid": [{"bin": i, "band": str(GRID.band[i]), "wl_lo": float(GRID.lo[i]), "wl_hi": float(GRID.hi[i]),
                  "wl_center": float(GRID.center[i])} for i in range(GRID.size)],
        "features": [asdict(f) for f in FEATURES],
        "indices": {k: {"numerator_um": list(v[0]), "denominator_um": list(v[1]), "description": v[2]}
                    for k, v in INDICES.items()},
        "templates": templates, "template_error": lib_error,
    }
