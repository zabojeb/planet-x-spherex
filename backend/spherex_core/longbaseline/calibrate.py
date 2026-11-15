"""Cross-calibration with field stars: archival frames against catalogues, SPHEREx against the archive.

Calibration stars are CatWISE2020 sources around the target (AllWISE, or Gaia DR3 without WISE magnitudes, when
IRSA does not answer: refcat.py) that are unsaturated in NEOWISE and SPHEREx, bright enough for per-visit precision
and isolated (no neighbour within ISOLATION_ARCSEC brighter than W1 + 2.5). Each is measured exactly like the
target: on the archival frames and in every SPHEREx exposure.

Two zero points per band, each the median over stars with its robust scatter (1.4826 MAD) and count:

1. frames -> catalogue: our NEOWISE / AllWISE frame photometry against CatWISE2020 (AllWISE) w1mpro / w2mpro, and 2MASS
   image photometry against the 2MASS PSC. It replaces the enclosed-energy aperture correction when at least
   MIN_STARS stars agree; otherwise the default (instruments.py) stays and the report says so.
2. SPHEREx -> archive: synthetic W1 / W2 of each star's all-epoch SPHEREx spectrum against its calibrated
   NEOWISE mean, and synthetic J / H / Ks against the 2MASS PSC, using only stars that are constant in both
   (robust scatter of NEOWISE visits and SPHEREx time bins below CONSTANT_SCATTER). The offset is added to the
   target's synthetic magnitudes, so every era of a band shares the NEOWISE (or 2MASS) scale.
"""

from __future__ import annotations

import math

import numpy as np

from ..targets import Target
from ..timeutil import mjd_to_year

W1_RANGE = (9.0, 14.0)  # NEOWISE saturates at W1 ~ 8.0, W2 ~ 7.0; fainter stars are too noisy per visit
W2_MAX = 13.5
ISOLATION_ARCSEC = 16.0
MIN_TARGET_SEP_ARCSEC = 40.0
MAX_STARS = 16
MIN_STARS = 4
MIN_SPHEREX_STARS = 3
CONSTANT_SCATTER = 0.05  # mag
G_RANGE = (10.5, 15.5)  # Gaia-only fallback: roughly W1 8-15 for FGKM stars
CATWISE_MJD = 57170.0
# SPHEREx synthetic -> NEOWISE / 2MASS PSC (mag, added to synthetic magnitudes) when a field has too few
# calibration stars: medians over the validation fields (analysis/validation/long_baseline_calibration.py).
DEFAULT_SPHEREX_OFFSETS = {"J": 0.0, "H": 0.0, "Ks": 0.0, "W1": 0.0, "W2": 0.0}


def _sep_arcsec(ra1, dec1, ra2, dec2) -> float:
    d = math.radians
    c = (math.sin(d(dec1)) * math.sin(d(dec2)) + math.cos(d(dec1)) * math.cos(d(dec2)) * math.cos(d(ra1 - ra2)))
    return math.degrees(math.acos(max(-1.0, min(1.0, c)))) * 3600.0


def select_stars(catwise: list[dict], centre: tuple[float, float], radius_arcsec: float,
                 avoid: list[tuple[float, float]], max_stars: int = MAX_STARS) -> list[dict]:
    """Isolated, unsaturated CatWISE stars within radius of the centre, away from the positions in `avoid`."""
    pts = [s for s in catwise if s.get("ra") is not None and s.get("w1") is not None and s.get("mag") is not None]
    out = []
    for s in pts:
        if not (W1_RANGE[0] <= s["w1"] <= W1_RANGE[1] and s["mag"] <= W2_MAX and s["mag"] >= 7.5):
            continue
        if s.get("ccf") not in (None, "", "0000") or s.get("extended"):  # AllWISE artefact / extended flags
            continue
        if _sep_arcsec(s["ra"], s["dec"], *centre) > radius_arcsec:
            continue
        if any(_sep_arcsec(s["ra"], s["dec"], a, b) < MIN_TARGET_SEP_ARCSEC for a, b in avoid):
            continue
        crowded = any(o is not s and o.get("w1") is not None and o["w1"] < s["w1"] + 2.5
                      and _sep_arcsec(s["ra"], s["dec"], o["ra"], o["dec"]) < ISOLATION_ARCSEC for o in pts)
        if not crowded:
            out.append(s)
    out.sort(key=lambda s: s["w1"])
    return out[:max_stars]


def select_gaia_stars(gaia: list[dict], centre: tuple[float, float], radius_arcsec: float,
                      avoid: list[tuple[float, float]], max_stars: int = MAX_STARS) -> list[dict]:
    """Fallback without a WISE catalogue: isolated Gaia DR3 stars with G in G_RANGE. They carry no W1 / W2 (frame
    zero points are skipped); stars our NEOWISE photometry finds saturated or too faint drop out downstream."""
    pts = [s for s in gaia if s.get("ra") is not None and s.get("mag") is not None]
    out = []
    for s in pts:
        if not G_RANGE[0] <= s["mag"] <= G_RANGE[1]:
            continue
        if _sep_arcsec(s["ra"], s["dec"], *centre) > radius_arcsec:
            continue
        if any(_sep_arcsec(s["ra"], s["dec"], a, b) < MIN_TARGET_SEP_ARCSEC for a, b in avoid):
            continue
        crowded = any(o is not s and o["mag"] < s["mag"] + 2.5
                      and _sep_arcsec(s["ra"], s["dec"], o["ra"], o["dec"]) < ISOLATION_ARCSEC for o in pts)
        if not crowded:
            out.append({**s, "g": s["mag"], "mag": None, "w1": None})
    out.sort(key=lambda s: s["g"])
    return out[:max_stars]


def star_target(s: dict) -> Target:
    """A CatWISE source as a Target at its catalogue epoch with its catalogue motion (0 when missing)."""
    return Target(ra=float(s["ra"]), dec=float(s["dec"]), name=s.get("id"), pmra=float(s.get("pmra") or 0.0),
                  pmdec=float(s.get("pmdec") or 0.0), ref_epoch=float(s.get("epoch") or mjd_to_year(CATWISE_MJD)))


def match_2mass(stars: list[dict], psc: list[dict], max_sep: float = 2.5) -> dict[str, dict]:
    """Nearest 2MASS PSC source of each star (positions at 2MASS epochs; slow movers only)."""
    out = {}
    for s in stars:
        best, best_d = None, max_sep
        for p in psc:
            if p.get("ra") is None:
                continue
            d = _sep_arcsec(s["ra"], s["dec"], p["ra"], p["dec"])
            if d < best_d:
                best, best_d = p, d
        if best is not None:
            out[s["id"]] = best
    return out


def robust_offset(deltas: list[float], min_n: int) -> dict:
    """Median offset, robust scatter and count; `applied` when there are at least min_n values."""
    d = np.array([x for x in deltas if x is not None and math.isfinite(x)], float)
    if d.size == 0:
        return {"offset_mag": 0.0, "scatter_mag": None, "n": 0, "applied": False}
    med = float(np.median(d))
    scatter = float(1.4826 * np.median(np.abs(d - med))) if d.size >= 3 else None
    return {"offset_mag": med, "scatter_mag": scatter, "n": int(d.size), "applied": bool(d.size >= min_n),
            "err_mag": float(1.2533 * (scatter or 0.0) / math.sqrt(d.size)) if scatter is not None else None}


def _median_mag(points: list[dict], survey: str, band: str) -> tuple[float | None, float | None]:
    sel = [p["mag"] for p in points if p["survey_id"] == survey and p["band"] == band and p.get("mag") is not None
           and not p.get("flagged") and p.get("method") == "aperture"]
    if not sel:
        return None, None
    a = np.array(sel)
    return float(np.median(a)), (float(1.4826 * np.median(np.abs(a - np.median(a)))) if a.size >= 3 else None)


def frame_zero_points(stars: list[dict], star_points: dict[str, list[dict]], psc: dict[str, dict],
                      wise_reference: str = "CatWISE2020") -> dict:
    """Catalogue minus our frame photometry, per survey and band."""
    out = {}
    cat_col = {"W1": "w1", "W2": "mag", "J": "j", "H": "h", "Ks": "mag"}
    for survey, bands in (("neowise", ("W1", "W2")), ("allwise", ("W1", "W2")), ("2mass", ("J", "H", "Ks"))):
        for band in bands:
            deltas, ids = [], []
            for s in stars:
                m, _ = _median_mag(star_points.get(s["id"], []), survey, band)
                ref = (psc.get(s["id"]) or {}).get(cat_col[band]) if survey == "2mass" else s.get(cat_col[band])
                if m is not None and ref is not None:
                    deltas.append(float(ref) - m)
                    ids.append(s["id"])
            r = robust_offset(deltas, MIN_STARS)
            out[f"{survey}:{band}"] = {**r, "reference": "2MASS PSC" if survey == "2mass" else wise_reference,
                                       "stars": ids}
    return out


def apply_offset(points: list[dict], survey: str, band: str, offset_mag: float) -> None:
    """Shift points of a survey and band by offset_mag (fluxes scaled accordingly), in place."""
    k = 10 ** (-0.4 * offset_mag)
    for p in points:
        if p.get("survey_id") == survey and p["band"] == band and p.get("mag") is not None:
            p["mag"] += offset_mag
            if p.get("flux_mjy") is not None:
                p["flux_mjy"] *= k
                p["err_mjy"] *= k
            p["calibrated"] = True


def spherex_zero_points(stars: list[dict], star_points: dict[str, list[dict]], star_synth: dict[str, dict],
                        star_bins: dict[str, list[dict]], psc: dict[str, dict]) -> dict:
    """Archive minus SPHEREx synthetic magnitudes of constant calibration stars, per band."""
    out = {}
    for band in ("W1", "W2", "J", "H", "Ks"):
        deltas, ids, rejected = [], [], 0
        for s in stars:
            syn = (star_synth.get(s["id"]) or {}).get(band)
            if not syn or syn.get("mag") is None:
                continue
            if band in ("W1", "W2"):
                ref, scatter = _median_mag(star_points.get(s["id"], []), "neowise", band)
            else:
                col = {"J": "j", "H": "h", "Ks": "mag"}[band]
                ref, scatter = (psc.get(s["id"]) or {}).get(col), None
            if ref is None:
                continue
            sp = [p["mag"] for p in star_bins.get(s["id"], []) if p["band"] == band and p.get("mag") is not None]
            sp_scatter = float(1.4826 * np.median(np.abs(np.array(sp) - np.median(sp)))) if len(sp) >= 3 else None
            if any(x is not None and x > CONSTANT_SCATTER for x in (scatter, sp_scatter)):
                rejected += 1
                continue
            deltas.append(float(ref) - syn["mag"])
            ids.append(s["id"])
        r = robust_offset(deltas, MIN_SPHEREX_STARS)
        if not r["applied"]:
            r["default_offset_mag"] = DEFAULT_SPHEREX_OFFSETS[band]
        out[band] = {**r, "reference": "NEOWISE frames" if band in ("W1", "W2") else "2MASS PSC",
                     "stars": ids, "rejected_variable": rejected}
    return out
