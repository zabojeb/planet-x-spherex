"""'Click a point -> Analyze': every automatic analysis of one sky position in a built sequence, in one job.

Steps (each independent: a failure becomes {"error": ...} in its slot, the job still finishes):
  identification  nearest Gaia DR3 / CatWISE2020 / 2MASS / SIMBAD sources, moved to every epoch's date
  photometry      forced photometry in every exposure (re-measure; uses a Gaia motion when one matches)
  spectrum        binned spectrum, features, indices, classification and spectral variability (spectral/)
  motion          blind proper-motion fit from per-epoch co-add centroids (analysis/motion)
  periodogram     Lomb-Scargle of the point's photometry
  colours         red excess: 3.8-5 um against < 2.4 um flux density (cold brown dwarfs are > 0.8)
  known_objects   asteroids and comets at the point seen from SPHEREx at every epoch mid-time
  tracklets       candidates from a finished field analysis of the same sequence near the point
  verdict         plain-language findings with evidence, a confidence each and an overall novelty score

The job runs in a thread of the API process through the field job runner (deduplicated per sequence and
rounded position, cancellable between steps; results stored next to the sequence).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass

import numpy as np

from ..bands import BANDS
from ..pipeline import results
from ..targets import Target
from ..timeutil import mjd_to_year
from .common import SequenceContext

log = logging.getLogger(__name__)
POINT_VERSION = 1
CATALOGS = ("gaia", "catwise", "2mass", "simbad")
SEARCH_RADIUS = 15.0  # arcsec around the point for catalogue matches
MATCH_RADIUS = 4.0  # arcsec: a catalogue source this close at the sequence's epochs is "the" counterpart
# Cone radii at each catalogue's own epoch, wide enough for fast movers (Barnard's Star is 95" from its Gaia
# 2016 position and 4.5' from its SIMBAD J2000 one by 2026); 2MASS has no motions, so only nearby rows count.
CONE_RADIUS = {"gaia": 180.0, "catwise": 180.0, "2mass": 30.0, "simbad": 300.0}
KNOWN_RADIUS = 30.0  # arcsec: known solar-system object this close at an epoch
TRACKLET_RADIUS = 20.0  # arcsec: field-analysis candidates this close to the point
MAX_KNOWN_EPOCHS = 8
KNOWN_BUDGET_S = 60.0  # stop starting sb_ident queries after this long
CATALOG_BUDGET_S = 60.0  # deadline for the four catalogue cones together
KNOWN_TIMEOUT_S = 45.0  # per HTTP request
SIGNIFICANT_PM = 100.0  # mas/yr, and > 3 sigma
RED_RATIO = 0.8
STEPS = ("identification", "photometry", "spectrum", "motion", "periodogram", "colours", "known_objects",
         "tracklets")


@dataclass
class PointRequest:
    ra: float
    dec: float
    pmra: float | None = None
    pmdec: float | None = None
    ref_epoch: float = 2000.0
    name: str | None = None

    def key(self) -> dict:
        """Cache key: position rounded to ~0.4" and the motion to 1 mas/yr."""
        return {"ra": round(self.ra, 4), "dec": round(self.dec, 4),
                "pmra": None if self.pmra is None else round(self.pmra), "pmdec": None if self.pmdec is None
                else round(self.pmdec), "ref": round(self.ref_epoch, 2), "v": POINT_VERSION}


def _err(e: BaseException) -> dict:
    return {"error": f"{e.__class__.__name__}: {e}"[:300]}


def _sep(ra1, dec1, ra2, dec2) -> float:
    cosd = math.cos(math.radians(0.5 * (dec1 + dec2)))
    return math.hypot(((ra1 - ra2 + 180.0) % 360.0 - 180.0) * cosd, dec1 - dec2) * 3600.0


def _propagate(src: dict, year: float) -> tuple[float, float]:
    ra, dec = float(src["ra"]), float(src["dec"])
    pmra, pmdec = src.get("pmra"), src.get("pmdec")
    if pmra is None or pmdec is None:
        return ra, dec
    dt = year - float(src.get("epoch") or 2000.0)
    dec2 = dec + pmdec * dt / 3.6e6
    return (ra + pmra * dt / 3.6e6 / math.cos(math.radians(dec2))) % 360.0, dec2


# ------------------------------------------------------------------------------------------------- steps
def identification(ctx: SequenceContext, req: PointRequest, years: list[float]) -> dict:
    """Nearest catalogue sources (at the sequence's mean epoch) with positions at every epoch."""
    from .. import catalogs

    y_mid = float(np.mean(years))
    out: dict = {"radius_arcsec": SEARCH_RADIUS, "reference_year": y_mid}
    # All four catalogues at once with one deadline: IRSA TAP can take minutes when busy. A query still running
    # at the deadline keeps going in its thread and fills the disk cache for the next analysis.
    ex = ThreadPoolExecutor(len(CATALOGS))
    futures = {name: ex.submit(catalogs.cone, name, req.ra, req.dec, CONE_RADIUS[name] / 3600.0)
               for name in CATALOGS}
    wait(futures.values(), timeout=CATALOG_BUDGET_S)
    ex.shutdown(wait=False)
    for name in CATALOGS:
        fut = futures[name]
        if not fut.done():
            out[name] = {"error": f"TimeoutError: no answer within {CATALOG_BUDGET_S:.0f} s (retry later)"}
            continue
        try:
            rows = fut.result()
        except Exception as e:  # noqa: BLE001 - one catalogue down must not hide the others
            out[name] = _err(e)
            continue
        found = []
        for s in rows:
            if s.get("ra") is None or s.get("dec") is None:
                continue
            ra_m, dec_m = _propagate(s, y_mid)
            sep = _sep(ra_m, dec_m, req.ra, req.dec)
            if sep <= SEARCH_RADIUS:
                found.append((sep, s))
        found.sort(key=lambda t: t[0])
        matches = []
        for sep, s in found[:3]:
            m = {k: s.get(k) for k in ("id", "ra", "dec", "epoch", "pmra", "pmdec", "mag", "mag_band", "otype",
                                        "sptype", "parallax") if k in s}
            m["sep_arcsec"] = sep
            pm = (s.get("pmra"), s.get("pmdec"))
            m["pm_total"] = math.hypot(*pm) if None not in pm else None
            track = []
            for e, yr in zip(ctx.meta["epochs"], years):
                ra_e, dec_e = _propagate(s, yr)
                x, y = ctx.grid.world_to_pixel(ra_e, dec_e)
                track.append({"epoch": e["index"], "ra": ra_e, "dec": dec_e, "x": float(x), "y": float(y)})
            m["track"] = track
            matches.append(m)
        out[name] = {"matches": matches, "n_in_radius": len(found),
                     "counterpart": bool(matches and matches[0]["sep_arcsec"] <= MATCH_RADIUS)}
    return out


def photometry(ctx: SequenceContext, target: Target) -> dict:
    from .remeasure import measure_position

    res = asyncio.run(measure_position(ctx, target))
    recs = res["photometry"]
    per_band = {}
    for b in BANDS:
        sel = [p for p in recs if p["band"] == b]
        good = [p["flux_mjy"] for p in sel if not p["flagged"] and not p.get("relative")]
        per_band[b] = {"n": len(sel), "n_good": len(good), "median_flux_mjy": float(np.median(good)) if good else None}
    methods: dict[str, int] = {}
    for p in recs:
        methods[p.get("method", "?")] = methods.get(p.get("method", "?"), 0) + 1
    res["summary"] = {"n": len(recs), "n_flagged": sum(bool(p["flagged"]) for p in recs),
                      "n_saturated": sum(bool(p.get("saturated")) for p in recs),
                      "n_relative": sum(bool(p.get("relative")) for p in recs), "methods": methods,
                      "per_band": per_band}
    return res


def colours(records: list[dict]) -> dict:
    """Red excess from calibrated, unflagged points: mean flux density at 3.8-5 um over that below 2.4 um."""
    def stat(lo: float, hi: float) -> tuple[float | None, float | None, int]:
        pts = [p for p in records if lo <= p["wl"] < hi and not p["flagged"] and not p.get("relative")
               and p.get("err_mjy")]
        if not pts:
            return None, None, 0
        w = np.array([1.0 / p["err_mjy"] ** 2 for p in pts])
        f = np.array([p["flux_mjy"] for p in pts])
        return float((w * f).sum() / w.sum()), float(1.0 / math.sqrt(w.sum())), len(pts)

    long_f, long_e, nl = stat(3.8, 5.1)
    short_f, short_e, ns = stat(0.7, 2.4)
    out = {"long_um": [3.8, 5.0], "short_um": [0.75, 2.4], "long_flux_mjy": long_f, "long_err_mjy": long_e,
           "short_flux_mjy": short_f, "short_err_mjy": short_e, "n_long": nl, "n_short": ns, "ratio": None,
           "long_snr": long_f / long_e if long_f is not None and long_e else None,
           "short_snr": short_f / short_e if short_f is not None and short_e else None, "red_excess": False}
    if long_f is not None and short_f is not None and short_f > 0:
        out["ratio"] = long_f / short_f
    snr_l, snr_s = out["long_snr"] or 0.0, out["short_snr"] or 0.0
    out["red_excess"] = bool(snr_l >= 5 and ((out["ratio"] or 0.0) >= RED_RATIO or snr_s < 3))
    return out


def known_objects(ctx: SequenceContext, req: PointRequest, budget_s: float = KNOWN_BUDGET_S) -> dict:
    """Asteroids/comets within KNOWN_RADIUS of the point at each epoch mid-time, seen from SPHEREx.

    JPL sb_ident (two-pass) can take a minute per query when busy, so the step stops starting new queries after
    `budget_s` and reports the epochs it skipped; answers are cached, so a repeat finishes them."""
    import time

    from ..field import known

    epochs = ctx.meta["epochs"][:MAX_KNOWN_EPOCHS]
    half = (KNOWN_RADIUS + 60.0) / 3600.0
    out = {"radius_arcsec": KNOWN_RADIUS, "observer": "SPHEREx (Horizons -163182)", "epochs": [],
           "matches": [], "skipped_epochs": []}
    t0 = time.monotonic()
    for e in epochs:
        if time.monotonic() - t0 > budget_s:
            out["skipped_epochs"].append(e["index"])
            continue
        try:  # one attempt: an interactive analysis must not sit in JPL's 429 back-off for minutes
            objs = known.query(req.ra, req.dec, half, float(e["mjd_mean"]), timeout=KNOWN_TIMEOUT_S, retries=1)
        except Exception as ex:  # noqa: BLE001 - JPL busy: report per epoch, keep the other epochs
            out["epochs"].append({"epoch": e["index"], "mjd": e["mjd_mean"], "error": _err(ex)["error"]})
            continue
        near = []
        for o in objs:
            sep = _sep(o["ra"], o["dec"], req.ra, req.dec)
            if sep <= KNOWN_RADIUS:
                near.append({"name": o["name"], "kind": o["kind"], "vmag": o.get("vmag"), "sep_arcsec": sep,
                             "ra": o["ra"], "dec": o["dec"]})
        out["epochs"].append({"epoch": e["index"], "mjd": e["mjd_mean"], "n_in_box": len(objs), "near": near})
        out["matches"].extend({**n, "epoch": e["index"]} for n in near)
    return out


def tracklets(ctx: SequenceContext, req: PointRequest) -> dict:
    """Field-analysis candidates near the point, from any finished field analysis of this sequence."""
    fdir = ctx.dir / "field"
    files = sorted(fdir.glob("f*.json")) if fdir.exists() else []
    if not files:
        return {"available": False, "reason": "no field analysis of this sequence yet (POST /field)",
                "candidates": []}
    data = json.loads(max(files, key=lambda p: p.stat().st_mtime).read_text())
    near = []
    near_tracklets: list[dict] = []

    def visit(obj, category: str) -> None:
        if isinstance(obj, dict):
            if "detections" in obj and "rate_arcsec_per_hr" in obj:
                seps = [_sep(d["ra"], d["dec"], req.ra, req.dec) for d in obj["detections"]]
                if seps and min(seps) <= TRACKLET_RADIUS:
                    if not obj.get("known") and len(near_tracklets) < 2:
                        near_tracklets.append(obj)
                    near.append({"category": category, "kind": "tracklet", "id": obj.get("id"),
                                 "sep_arcsec": min(seps), "rate_arcsec_per_hr": obj["rate_arcsec_per_hr"],
                                 "pa_deg": obj.get("pa_deg"), "n": obj.get("n"), "quality": obj.get("quality"),
                                 "mean_rb": obj.get("mean_rb"), "partner_fraction": obj.get("partner_fraction"),
                                 "known": (obj.get("known") or {}).get("name") if obj.get("known") else None})
                return
            if obj.get("ra") is not None and obj.get("dec") is not None and isinstance(obj.get("ra"), (int, float)):
                sep = _sep(obj["ra"], obj["dec"], req.ra, req.dec)
                if sep <= TRACKLET_RADIUS:
                    near.append({"category": category, "kind": "source", "id": obj.get("id"), "sep_arcsec": sep,
                                 **{k: obj[k] for k in ("score", "pm_total", "ratio", "chi2_red", "amplitude",
                                                        "anomaly", "rb") if k in obj}})
            for k, v in obj.items():
                if k not in ("detections", "track", "points", "light_curve"):
                    visit(v, category)
        elif isinstance(obj, list):
            for v in obj:
                visit(v, category)

    for category, items in (data.get("candidates") or {}).items():
        visit(items, category)
    # Unidentified tracklets near the point: identify them now (bracketed SPHEREx-centric positions, ADR-0054),
    # which also fixes results stored before that change.
    reid = None
    if near_tracklets:
        from types import SimpleNamespace

        from ..field import known

        trk = [dict(t) for t in near_tracklets]
        reid = known.identify(SimpleNamespace(grid=ctx.grid, n=ctx.grid.n), trk, timeout=KNOWN_TIMEOUT_S, retries=1)
        names = {t.get("id"): (t.get("known") or {}).get("name") for t in trk}
        for c in near:
            if c["kind"] == "tracklet" and not c.get("known") and names.get(c["id"]):
                c["known"] = names[c["id"]]
    near.sort(key=lambda c: c["sep_arcsec"])
    return {"available": True, "field_job": data.get("id"), "candidates": near[:20], "reidentified": reid}


# ------------------------------------------------------------------------------------------------- verdict
def _f(kind: str, text: str, confidence: float, novelty: float, **evidence) -> dict:
    return {"kind": kind, "text": text, "confidence": round(float(confidence), 2), "novelty": round(novelty, 2),
            "evidence": evidence}


def verdict(r: dict) -> dict:
    """Findings in plain language, each with evidence and a confidence, and an overall novelty in [0, 1]."""
    out: list[dict] = []
    mover_near = any(c["kind"] == "tracklet" for c in (r.get("tracklets") or {}).get("candidates") or [])
    ident = r.get("identification") or {}
    gaia = ident.get("gaia") or {}
    simbad = (ident.get("simbad") or {}).get("matches") or []
    gaia_m = (gaia.get("matches") or [None])[0]
    has_gaia = bool(gaia.get("counterpart"))
    has_catwise = bool((ident.get("catwise") or {}).get("counterpart"))
    has_2mass = bool((ident.get("2mass") or {}).get("counterpart"))

    if simbad and simbad[0]["sep_arcsec"] <= MATCH_RADIUS * 1.5:
        s = simbad[0]
        out.append(_f("identity", f"Known object: {s.get('id')}" + (f" ({s.get('otype')})" if s.get("otype") else ""),
                      0.9, -0.3, simbad=s.get("id"), sep_arcsec=s["sep_arcsec"]))
    elif has_gaia:
        out.append(_f("identity", f"Matches {gaia_m['id']} (G={gaia_m.get('mag'):.1f})" if gaia_m.get("mag") else
                      f"Matches {gaia_m['id']}", 0.85, -0.15, gaia=gaia_m["id"], sep_arcsec=gaia_m["sep_arcsec"]))
    elif not ident.get("error"):
        out.append(_f("identity", "No Gaia DR3 counterpart within 4″ at the SPHEREx epochs", 0.6,
                      0.0 if mover_near else 0.2, catwise=has_catwise, twomass=has_2mass))

    mo = (r.get("motion") or {}).get("combined")
    if mo and mo.get("pm_total"):
        pm, err = mo["pm_total"], mo.get("pm_total_err") or float("inf")
        if pm >= SIGNIFICANT_PM and pm / err >= 3:
            arcsec = pm / 1000.0
            g_pm = gaia_m.get("pm_total") if has_gaia and gaia_m else None
            if g_pm:
                agree = abs(pm - g_pm) <= max(3 * err, 0.3 * g_pm)
                out.append(_f("motion", f"Moves {arcsec:.2f}″/yr toward PA {mo['pa_deg']:.0f}°; Gaia gives "
                              f"{g_pm / 1000:.2f}″/yr" + ("" if agree else " (disagrees)"),
                              0.8 if agree else 0.5, 0.05 if agree else 0.3, pm_mas_yr=pm, err=err, gaia_pm=g_pm))
            elif mover_near:
                out.append(_f("motion", f"Centroid shifts {arcsec:.2f}″/yr, but an intra-visit mover passes here, so "
                              "the shift is not a proper motion", 0.5, 0.0, pm_mas_yr=pm, err=err))
            elif pm / err >= 5:
                out.append(_f("motion", f"Moves {arcsec:.2f}″/yr toward PA {mo['pa_deg']:.0f}° with no Gaia "
                              "counterpart → high-proper-motion candidate (brown dwarf?)",
                              min(0.9, 0.4 + 0.05 * pm / err), 0.55, pm_mas_yr=pm, err=err))
            else:
                out.append(_f("motion", f"Possible motion {arcsec:.2f}″/yr ({pm / err:.1f}σ) with no Gaia counterpart; "
                              "needs more epochs", 0.35, 0.15, pm_mas_yr=pm, err=err))
        elif pm / max(err, 1e-9) < 2:
            out.append(_f("motion", f"No significant motion ({pm:.0f} ± {err:.0f} mas/yr)", 0.6, 0.0))

    cls = ((r.get("spectrum") or {}).get("classification") or {}).get("summary") or {}
    fam, label = cls.get("family"), cls.get("label")
    if label and label != "unclassified":
        nov = {"brown_dwarf": 0.25, "composite": 0.2, "yso": 0.1, "yso_candidate": 0.1, "nonstellar": 0.15,
               "late_m_dwarf": 0.1}.get(fam, 0.0)
        out.append(_f("spectrum", f"{label} spectrum", 0.7, nov, family=fam))

    # Ring (relative) photometry drifts by up to ~14 % on constant stars (ADR-0020), so changes below 20 % are not
    # claimed for saturated sources; calibrated photometry needs 5 %. Both need |z| >= 5 per detector.
    ph_sum = (r.get("photometry") or {}).get("summary") or {}
    mostly_relative = bool(ph_sum.get("n")) and ph_sum.get("n_relative", 0) >= 0.5 * ph_sum["n"]
    thr = 0.25 if mostly_relative else 0.05
    min_bands = 2 if mostly_relative else 1  # one drifting detector (D2/D3 rings, ADR-0020) is not a variable star
    var = (r.get("spectrum") or {}).get("variability") or {}
    bands_var: dict[str, float] = {}
    for e in var.get("epochs") or []:
        for b, v in (e.get("per_band") or {}).items():
            ratio, z = v.get("ratio"), v.get("z")
            if ratio is not None and z is not None and abs(z) >= 5 and abs(ratio - 1.0) >= thr:
                bands_var[b] = max(bands_var.get(b, 0.0), abs(ratio - 1.0))
    if len(bands_var) >= min_bands:
        amp = max(bands_var.values())
        out.append(_f("variability", f"Varies between epochs by up to {amp * 100:.0f} % in "
                      f"{', '.join(sorted(bands_var))}", 0.75 if not mostly_relative else 0.6, 0.15,
                      bands=sorted(bands_var), relative_photometry=mostly_relative))
    per = r.get("periodogram") or {}
    best = per.get("best") or {}
    fap = best.get("false_alarm_probability")
    n_epochs = len({p["epoch"] for p in (r.get("photometry") or {}).get("photometry") or []})
    # A period needs enough visits to be sampled (>= 5 epochs) and must exceed the within-visit cadence.
    if best.get("period_days") and fap is not None and fap < 0.01 and n_epochs >= 5 and best["period_days"] > 20 \
            and per.get("baseline_days", 0) > 1.5 * best["period_days"]:
        out.append(_f("variability", f"Periodic: P ≈ {best['period_days']:.0f} d (FAP {fap:.1e})", 0.75, 0.1,
                      period_days=best["period_days"], fap=fap, epochs=n_epochs))

    col = r.get("colours") or {}
    if col.get("red_excess"):
        ratio = col.get("ratio")
        text = f"Very red: 3.8–5 µm / <2.4 µm flux ratio {ratio:.2f}" if ratio else \
            "Detected at 3.8–5 µm, not below 2.4 µm"
        out.append(_f("colour", text + ("" if has_gaia else " — cold brown-dwarf candidate"), 0.7,
                      0.15 if has_gaia else 0.35, ratio=ratio))

    kn = (r.get("known_objects") or {}).get("matches") or []
    if kn:
        k = min(kn, key=lambda m: m["sep_arcsec"])
        out.append(_f("solar_system", f"Known {k['kind']} {k['name']} passes {k['sep_arcsec']:.0f}″ away "
                      f"(epoch {k['epoch']})", 0.85, -0.1, name=k["name"]))
    trk = [c for c in (r.get("tracklets") or {}).get("candidates") or [] if c["kind"] == "tracklet"]
    for c in trk[:2]:
        if c.get("known"):
            out.append(_f("solar_system", f"Moving {c['rate_arcsec_per_hr']:.1f}″/h within a visit: {c['known']}",
                          0.8, -0.05, tracklet=c["id"]))
        else:
            q = c.get("quality") == "high"
            out.append(_f("solar_system", f"Unidentified mover {c['rate_arcsec_per_hr']:.1f}″/h within a visit "
                          f"({c['id']}) → possible new asteroid/comet", 0.6 if q else 0.35, 0.5 if q else 0.25,
                          tracklet=c["id"], real_bogus=c.get("mean_rb")))

    ph = (r.get("photometry") or {}).get("summary") or {}
    if ph.get("n") and ph.get("n_relative", 0) >= 0.5 * ph["n"]:
        out.append(_f("data", "Saturated: fluxes are relative (ring photometry), no calibrated spectrum", 0.9, 0.0))
    elif ph.get("n") == 0:
        out.append(_f("data", "Nothing measurable at this position", 0.7, 0.0))

    novelty = 0.1 + sum(f["novelty"] for f in out)
    novelty = float(min(1.0, max(0.0, novelty)))
    out.sort(key=lambda f: -abs(f["novelty"]) * f["confidence"])
    headline = next((f["text"] for f in out if f["novelty"] > 0.2), out[0]["text"] if out else "No findings")
    return {"novelty": round(novelty, 2), "headline": headline, "findings": out}


# ------------------------------------------------------------------------------------------------- driver
def analyze_point(ctx: SequenceContext, req: PointRequest, progress, check) -> dict:
    from .. import spectral
    from .motion import fit_motion
    from .periodogram import periodogram

    years = [float(mjd_to_year(e["mjd_mean"])) for e in ctx.meta["epochs"]]
    x, y = ctx.grid.world_to_pixel(req.ra, req.dec)
    result: dict = {"sequence": ctx.seq_id, "version": POINT_VERSION, "request": asdict(req),
                    "point": {"ra": req.ra, "dec": req.dec, "x": float(x), "y": float(y),
                              "inside": bool(0 <= x < ctx.grid.n and 0 <= y < ctx.grid.n)}}
    total = len(STEPS)

    def step(i: int, name: str, fn):
        check()
        progress(name, i, total)
        try:
            result[name] = fn()
        except Exception as e:  # noqa: BLE001 - every step reports its own failure
            if e.__class__.__name__ == "FieldCancelled":
                raise
            log.warning("point analysis %s: %s failed: %r", ctx.seq_id, name, e)
            result[name] = _err(e)

    step(0, "identification", lambda: identification(ctx, req, years))

    pmra, pmdec, ref, pm_source = req.pmra, req.pmdec, req.ref_epoch, "request"
    ident = result["identification"] if isinstance(result.get("identification"), dict) else {}
    # Follow a catalogue counterpart with a motion (Gaia first, then SIMBAD, e.g. for brown dwarfs too faint for
    # Gaia): its position at its own epoch plus its motion.
    follow = None
    if pmra is None or pmdec is None:
        for cat in ("gaia", "simbad"):
            c = ident.get(cat) or {}
            if c.get("counterpart") and c["matches"][0].get("pmra") is not None \
                    and c["matches"][0].get("pmdec") is not None:
                follow = (cat, c["matches"][0])
                break
    if follow:
        cat, m = follow
        target = Target(ra=m["ra"], dec=m["dec"], name=req.name or m["id"], pmra=m["pmra"], pmdec=m["pmdec"],
                        ref_epoch=float(m.get("epoch") or (2016.0 if cat == "gaia" else 2000.0)))
        pm_source = f"{'Gaia DR3' if cat == 'gaia' else 'SIMBAD'} ({m['id']})"
    elif pmra is None or pmdec is None:
        target = Target(ra=req.ra, dec=req.dec, name=req.name, ref_epoch=float(np.mean(years)))
        pm_source = "none (static position at the sequence's epochs)"
    else:
        target = Target(ra=req.ra, dec=req.dec, name=req.name, pmra=pmra, pmdec=pmdec, ref_epoch=ref)
    result["point"]["motion_used"] = {"pmra": target.pmra, "pmdec": target.pmdec, "ref_epoch": target.ref_epoch,
                                      "source": pm_source}

    step(1, "photometry", lambda: photometry(ctx, target))
    records = (result.get("photometry") or {}).get("photometry") or []

    def spec():
        if not records:
            raise ValueError("no photometry")
        s = spectral.analyze(records, target=target, catalogs=False)
        return {"spectrum": s["spectrum"], "classification": s["classification"], "variability": s["variability"]}

    step(2, "spectrum", spec)

    def motion():
        coadds = results.read_array(ctx.dir, ctx.meta, "coadds")
        return fit_motion(ctx.meta, coadds, ctx.grid, float(x), float(y))

    step(3, "motion", motion)
    step(4, "periodogram", lambda: _trim_periodogram(periodogram(records)))
    step(5, "colours", lambda: colours(records))
    step(6, "known_objects", lambda: known_objects(ctx, req))
    step(7, "tracklets", lambda: tracklets(ctx, req))
    check()
    progress("verdict", total, total)
    result["verdict"] = verdict(result)
    return result


def _trim_periodogram(p: dict) -> dict:
    """Keep the curve light: at most 400 samples (the full one is at /periodogram)."""
    curve = p.get("periodogram") or {}
    n = len(curve.get("period_days") or [])
    if n > 400:
        idx = np.linspace(0, n - 1, 400).astype(int)
        p["periodogram"] = {k: [v[i] for i in idx] for k, v in curve.items()}
    return p
