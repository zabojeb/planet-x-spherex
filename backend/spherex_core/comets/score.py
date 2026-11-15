"""Comet-likeness of a moving source: one 0-100 number from motion, morphology and spectrum, with its evidence.

log-odds L = logit(P_orbit) + L_morphology + L_spectrum, score = 100 / (1 + e^-L):

* P_orbit: the comet-like share of the digest2-style orbit classes (orbits.orbit_classes; constrained by the
  distance when an ephemeris or a parallax fit gives one), clipped to [0.01, 0.9]. It carries the rate, position
  angle, ecliptic latitude and solar elongation through the geometry; a main-belt-like motion starts near
  logit(0.01) = -4.6;
* L_morphology, when the object is bright enough to judge (stamp S/N >= 8): 8 (s - 0.5) above s = 0.5 (up to +4
  for a clearly extended source; coma_score s includes an aligned tail), -2 for a source the test calls
  point-like (bright and < 2 sigma), 0 when inconclusive. A coma is the defining property of a comet, so it can
  outweigh the motion prior;
* L_spectrum: +2.5 for CO2 >= 4 sigma, +1.5 at 3-4 sigma, CO adds up to +1.5, water ice +0.5 (signature.evidence),
  counted only for a source detected along its track (stamp S/N >= 8): bands "found" in residual noise are not;
* without coma or gas evidence the orbit term is capped at logit(0.5) = 0: the motion alone never makes a source
  comet-like (very fast movers are often random alignments of residuals, which no bound orbit explains).

So a point-like main-belt mover scores ~0.1, an extended one ~35, extended with CO2 ~85; an extended source on a
motion no asteroid class explains ~98. The weights are hand-set, not trained (no labelled SPHEREx comet sample
exists yet); validation numbers are in ADR-0063. Known objects keep their score: the ranking shows how comet-like
the measurements are, and the identification says what the object is.
"""

from __future__ import annotations

import math

P_CLIP = (0.01, 0.9)
MIN_SNR = 8.0


def logit(p: float, hi: float = P_CLIP[1]) -> float:
    p = min(max(p, P_CLIP[0]), hi)
    return math.log(p / (1 - p))


def morphology_llr(coma: dict | None) -> float:
    if not coma or coma.get("coma_score") is None or (coma.get("snr") or 0) < MIN_SNR:
        return 0.0
    s = coma["coma_score"]
    if s >= 0.5:
        return 8.0 * (s - 0.5)
    return -2.0 if coma.get("verdict") == "point-like" else 0.0


def comet_likeness(orbit: dict | None, coma: dict | None, spectrum: dict | None) -> dict:
    parts = {}
    p = (orbit or {}).get("comet_like")
    parts["morphology"] = round(morphology_llr(coma), 2)
    bright = bool(coma) and (coma.get("snr") or 0) >= MIN_SNR
    parts["spectrum"] = round(float((spectrum or {}).get("llr") or 0.0), 2) if bright else 0.0
    # the motion alone never makes a source comet-like: without coma or gas evidence it is capped at even odds
    evidence = parts["morphology"] > 0 or parts["spectrum"] > 0
    parts["orbit"] = round(logit(p, P_CLIP[1] if evidence else 0.5), 2) if p is not None else 0.0
    total = sum(parts.values())
    score = 100.0 / (1.0 + math.exp(-total))
    why = []
    if orbit and orbit.get("most_likely"):
        why.append(f"motion fits {orbit['most_likely']} orbits best (comet-like share {100 * (p or 0):.0f}%"
                   + (", distance known" if orbit.get("constrained") else "") + ")")
    if coma and coma.get("verdict") not in (None, "no data"):
        why.append(f"morphology {coma['verdict']} ({coma.get('extent_sigma') or 0:.1f} sigma)"
                   + (", aligned tail" if (coma.get("tail") or {}).get("aligned") else ""))
    if spectrum and spectrum.get("verdict"):
        why.append(f"spectrum: {spectrum['verdict']}"
                   + (f" (gas {spectrum['gas_sigma']:.1f} sigma)" if spectrum.get("gas_sigma") is not None else ""))
    return {"score": round(score, 1), "log_odds": round(total, 2), "terms": parts, "why": why}
