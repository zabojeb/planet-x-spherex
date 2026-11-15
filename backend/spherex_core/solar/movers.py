"""Unidentified movers: tracklets from the field analysis that no known object explains (ADR-0060).

The Moving Object Detection of the field analysis (field.transients: difference images, real/bogus, simultaneous
detector-pair check, linking) and the identification against JPL (field.known, SPHEREx-centric positions) are run,
or read from a finished field analysis of the same sequence. A tracklet whose identification ran and found no
object within the position and motion tolerances becomes an "unidentified mover" candidate, with the evidence a
follow-up observer needs: rate and position angle, motion relative to the ecliptic, number of detections and
time span, detector-pair confirmation (D1/D4, D2/D5, D3/D6 image the same sky simultaneously), real/bogus score,
S/N, fit residual, and whether a tracklet of another visit sits at the same position (a static residual; the tier
drops one step). Tracklets whose identification failed (JPL unreachable) are listed as "unverified", never
as unidentified.

Rates give a rough population hint (near-Earth > 60"/h, main belt 15-60"/h, outer < 5"/h near opposition), which
is a heuristic, not an orbit. The result keeps the field-analysis layout under candidates.tracklets, so the
existing ADES export (GET /api/field/{job}/ades.psv, station 288, "FOLLOW-UP DATA, NOT A SUBMISSION") writes them.
"""

from __future__ import annotations

import math

import numpy as np

from ..analysis.common import SequenceContext
from ..errors import NotFound

MOVERS_VERSION = 1
OBLIQUITY = math.radians(23.4392911)
TIERS = ("strong", "candidate", "weak")
REPEAT_ARCSEC = 6.0  # a tracklet this close to one of another visit is probably a static residual (about 1 px)
CAVEATS = [
    "Unidentified means no object in JPL's small-body database (sb_ident, V <= vmag_lim) moved along this track "
    "as seen from SPHEREx; faint known objects, comets without current elements and artefacts remain possible.",
    "Tracklets are straight lines inside one visit; below ~1.5 au SPHEREx's orbital parallax bends real tracks by "
    ">= 1 px, which can split or drop near-Earth objects.",
    "Rates are converted to a population hint without an orbit; one tracklet does not constrain the distance.",
    "Positions are the field analysis' detections on the resampled grid (6.2\" pixels); the ADES uncertainties are "
    "its per-detection estimates. Check MPChecker and the NEOCP before reporting anything "
    "(docs/research/astrometrica.md).",
    "A tracklet that sits where a tracklet of another visit was (repeats_in_other_visits) is more likely a static "
    "residual than a mover; its tier is lowered one step.",
]


def ecliptic_pa(ra: float, dec: float) -> float:
    """Position angle (deg east of north) of the direction of increasing ecliptic longitude at (ra, dec)."""
    a, d = math.radians(ra), math.radians(dec)
    # unit vectors: ecliptic pole in equatorial coordinates, then the local east-along-ecliptic direction
    pole = np.array([0.0, -math.sin(OBLIQUITY), math.cos(OBLIQUITY)])
    p = np.array([math.cos(d) * math.cos(a), math.cos(d) * math.sin(a), math.sin(d)])
    t = np.cross(pole, p)
    t /= np.linalg.norm(t)
    east = np.array([-math.sin(a), math.cos(a), 0.0])
    north = np.array([-math.sin(d) * math.cos(a), -math.sin(d) * math.sin(a), math.cos(d)])
    return float(math.degrees(math.atan2(t @ east, t @ north)) % 360.0)


def ecliptic_latitude(ra: float, dec: float) -> float:
    a, d = math.radians(ra), math.radians(dec)
    z = -math.cos(d) * math.sin(a) * math.sin(OBLIQUITY) + math.sin(d) * math.cos(OBLIQUITY)
    return float(math.degrees(math.asin(max(-1.0, min(1.0, z)))))


def population_hint(rate_arcsec_h: float) -> str:
    if rate_arcsec_h > 60:
        return "fast (near-Earth-like)"
    if rate_arcsec_h >= 15:
        return "main-belt-like"
    if rate_arcsec_h >= 5:
        return "slow (outer main belt, Hilda, Trojan-like)"
    return "very slow (Centaur/TNO-like, or a static residual)"


def tier(t: dict) -> str:
    pf = t.get("partner_fraction")
    if t.get("quality") == "high" and t["n"] >= 4 and (pf is None or pf >= 0.5) and t["mean_rb"] >= 0.6:
        return "strong"
    if t["n"] >= 3 and t["mean_rb"] >= 0.45:
        return "candidate"
    return "weak"


def describe(t: dict, ident_error: str | None) -> dict:
    """The evidence of one tracklet and its status: unidentified, known or unverified."""
    det = t["detections"]
    ra = float(np.mean([d["ra"] for d in det]))
    dec = float(np.mean([d["dec"] for d in det]))
    epa = ecliptic_pa(ra, dec)
    d_pa = (t["pa_deg"] - epa + 180.0) % 360.0 - 180.0
    along = min(abs(d_pa), 180 - abs(d_pa))  # prograde or retrograde along the ecliptic
    if t.get("known"):
        status = "known"
    elif ident_error and not t.get("identification"):
        status = "unverified"
    else:
        status = "unidentified"
    bands = sorted({d["band"] for d in det})
    return {
        "id": t["id"], "status": status, "tier": tier(t), "epoch": t["epoch"], "n": t["n"],
        "mjd_first": min(d["mjd"] for d in det), "mjd_last": max(d["mjd"] for d in det),
        "span_hours": 24.0 * (max(d["mjd"] for d in det) - min(d["mjd"] for d in det)),
        "ra": ra, "dec": dec, "ecliptic_latitude_deg": ecliptic_latitude(ra, dec),
        "rate_arcsec_per_hr": t["rate_arcsec_per_hr"], "pa_deg": t["pa_deg"],
        "angle_from_ecliptic_deg": float(along), "retrograde": bool(abs(d_pa) > 90),
        "rms_arcsec": t.get("rms_arcsec"), "mean_snr": t["mean_snr"], "mean_rb": t["mean_rb"],
        "partner_fraction": t.get("partner_fraction"), "quality": t.get("quality"), "bands": bands,
        "population_hint": population_hint(t["rate_arcsec_per_hr"]),
        "known": t.get("known"), "nearest_known": (t.get("identification") or [None])[0],
    }


def mark_repeats(rows: list[dict], radius_arcsec: float = REPEAT_ARCSEC) -> None:
    """A real mover is somewhere else six months later; a tracklet at the position of a tracklet from another
    visit is more likely a static residual (bright-star wings, a variable). Adds repeats_in_other_visits and lowers
    the tier one step when it is not zero."""
    for r in rows:
        cosd = math.cos(math.radians(r["dec"]))
        r["repeats_in_other_visits"] = sum(
            1 for o in rows if o["epoch"] != r["epoch"]
            and 3600.0 * math.hypot((o["ra"] - r["ra"]) * cosd, o["dec"] - r["dec"]) < radius_arcsec)
        if r["repeats_in_other_visits"]:
            r["tier"] = TIERS[min(len(TIERS) - 1, TIERS.index(r["tier"]) + 1)]


def unknown_movers(ctx: SequenceContext, vmag_lim: float = 21.0, field_result: dict | None = None,
                   progress=None, check=None) -> dict:
    """Run (or reuse) the moving-object detection and identification and keep what is left unexplained."""
    progress = progress or (lambda stage, done, total: None)
    check = check or (lambda: None)
    if (ctx.meta.get("grid") or {}).get("frame") == "comoving":
        raise NotFound("unknown movers need a fixed-position sequence: in a co-moving one the stars move")
    if field_result is not None:
        tracklets = list(field_result.get("candidates", {}).get("tracklets", []))
        stats = dict(field_result.get("stats", {}).get("transients", {}))
        ident = stats.get("identification") or {}
        source = f"field analysis {field_result.get('sequence')} (reused)"
    else:
        from ..field import known, transients
        from ..field.common import load_field

        progress("detect", 0, 3)
        fd = load_field(ctx)
        dets, stats = transients.detect(fd, check)
        check()
        progress("link", 1, 3)
        tracklets, _pairs = transients.link(dets, fd.grid.pixscale)
        progress("identify", 2, 3)
        ident = known.identify(fd, tracklets, vmag_lim) if tracklets else {}
        stats = {**stats, "tracklets": len(tracklets), "identification": ident}
        source = "moving-object detection run by this job"
    check()
    err = (ident or {}).get("error")
    rows = [describe(t, err) for t in tracklets]
    mark_repeats(rows)
    by_id = {t["id"]: t for t in tracklets}
    unknown = [r for r in rows if r["status"] == "unidentified"]
    unknown.sort(key=lambda r: (TIERS.index(r["tier"]), -r["mean_snr"]))
    seq = ctx.seq_id
    for k, r in enumerate(unknown):  # ADES trkSub: <= 8 characters, unique within this export
        r["trk_sub"] = f"U{seq[:3]}{k + 1:03d}"
    export = []
    for r in unknown:
        t = dict(by_id[r["id"]])
        t["id"] = r["trk_sub"]
        t["quality"] = "high" if r["tier"] == "strong" else "low"
        export.append(t)
    progress("done", 3, 3)
    return {
        "kind": "unknown_movers", "version": MOVERS_VERSION, "sequence": seq, "vmag_lim": vmag_lim,
        "source": source, "identification": ident,
        "counts": {"tracklets": len(rows), "known": sum(r["status"] == "known" for r in rows),
                   "unidentified": len(unknown), "unverified": sum(r["status"] == "unverified" for r in rows),
                   "strong": sum(r["tier"] == "strong" for r in unknown)},
        "unidentified": unknown, "others": [r for r in rows if r["status"] != "unidentified"],
        # the field-analysis layout, so GET /api/field/{job}/ades.psv exports the unidentified ones
        "candidates": {"tracklets": export},
        "ades": {"station": "288", "note": "strong tracklets by default, all with ?all=true; follow-up data, "
                                           "not a submission"},
        "stats": {"transients": stats}, "caveats": CAVEATS,
    }
