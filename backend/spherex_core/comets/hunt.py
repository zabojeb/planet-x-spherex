"""Comet search over a built sequence (a field-analysis job, kind 'h'): candidates ranked by comet-likeness.

Fixed-position sequence ("hunt"):
  1. movers: the field analysis' straight-line tracklets inside one group of exposures (> 2"/h; reused from a
     finished field job or detected again), slow links over days and shift-and-stack hits (slow.py), each with a
     motion model x(t) = x0 + v dt + w B(t);
  2. per candidate, along its model positions in the static-sky subtracted exposures (frames.py): the coma test
     (coma.py), the spectral signature (signature.py), the digest2-style orbit classes (orbits.py) and the
     comet-likeness (score.py); forced positions and S/N per exposure, a cutout strip;
  3. the best candidates are identified against JPL's small-body database (comets included) with SPHEREx-centric
     positions (field.known); ADES rows for the measured ones (station 288, follow-up data).
Co-moving sequence ("object check"): the same tests on the sequence's own object, per visit, so a known comet or
asteroid shows what the score says about it (the validation of ADR-0063).
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import asdict, dataclass

import numpy as np

from ..analysis.common import SequenceContext
from ..bands import BANDS
from ..errors import NotFound
from ..field.common import Check, load_field, no_check
from . import coma as coma_mod
from . import geometry, orbits, slow
from .score import comet_likeness
from .signature import signature

log = logging.getLogger(__name__)
SEARCH_VERSION = 1
AP_R = 2.0  # px aperture for the spectrum points
MAX_CANDIDATES = 120  # measured per search
MAX_IDENTIFY = 12  # best candidates sent to JPL
IDENTIFY_BUDGET_S = 150.0
MIN_TRACK_SNR = 5.0  # a candidate must show in forced photometry along its track (co-added stamp S/N)
STRIP_FRAMES = 10
STRIP_FOR = 20  # candidates that carry a cutout strip
FAST_PAD_D = 0.02  # a tracklet's stamps: its detections' span plus this
CAVEATS = [
    "The comet-likeness is a hand-weighted combination (no labelled SPHEREx comet sample exists to train it); the "
    "orbit-class term uses a crude population prior, so treat scores as a ranking.",
    "A coma is judged against field stars measured the same way; registration jitter along a fitted track and "
    "residual stars can mimic a small extension (the bootstrap and a 5 % floor cover most of it).",
    "Spectra of off-centre candidates use each exposure's wavelength at the field centre: up to half a channel off "
    "at the edge of a 10' field.",
    "Slow links assume a straight heliocentric motion plus the observer's parallax over <= window_days; strongly "
    "curved tracks of objects closer than ~1 au can be missed.",
    "Unidentified means no JPL small-body (asteroid or comet with elements) moved along the track as seen from "
    "SPHEREx; check the MPC (MPChecker, NEOCP, comet lists) before reporting. ADES output is follow-up data, not a "
    "submission.",
]


@dataclass
class HuntOptions:
    field_job: str | None = None
    slow: bool = True
    stack: bool = True
    vmag_lim: float = 21.0
    window_days: float = 6.0
    stack_rate_max: float = 2.0  # "/h
    identify: bool = True

    def to_dict(self) -> dict:
        return {**asdict(self), "v": SEARCH_VERSION}

    @classmethod
    def from_dict(cls, d: dict | None) -> "HuntOptions":
        d = dict(d or {})
        d.pop("v", None)
        o = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        if not (10 <= o.vmag_lim <= 26):
            raise ValueError("vmag_lim must be between 10 and 26")
        if not (1.0 <= o.window_days <= 30.0):
            raise ValueError("window_days must be between 1 and 30")
        if not (0.1 <= o.stack_rate_max <= 5.0):
            raise ValueError("stack_rate_max must be between 0.1 and 5 arcsec/h")
        return o


def comet_search(ctx: SequenceContext, opts: HuntOptions, progress=None, check: Check = no_check,
                 field_result: dict | None = None) -> dict:
    progress = progress or (lambda *a, **k: None)
    if (ctx.meta.get("grid") or {}).get("frame") == "comoving":
        return object_check(ctx, opts, progress, check)
    return hunt(ctx, opts, progress, check, field_result)


# ---------------------------------------------------------------- shared measurement
def _aperture(img: np.ndarray, x: float, y: float, noise: float, pixscale: float) -> tuple[float, float] | None:
    st = coma_mod.shift_stamp(img, x, y)
    r = coma_mod._R
    ap = r <= AP_R
    if not np.isfinite(st[ap]).all():
        return None
    bkg = st[(r >= coma_mod.R_BKG) & np.isfinite(st)]
    b = float(np.median(bkg)) if bkg.size >= 8 else 0.0
    sr = (pixscale / geometry.ARCSEC_PER_RAD) ** 2 * 1e9  # MJy/sr per pixel -> mJy
    f = float((st[ap] - b).sum()) * sr
    e = float(noise * math.sqrt(ap.sum() * (1 + ap.sum() / max(bkg.size, 1)))) * sr
    return f, e


def _observer_motion(t0: float, t1: float) -> tuple[np.ndarray, np.ndarray, str]:
    o, v, who = geometry.observer_helio([t0, t1])
    if t1 - t0 >= 0.005:
        return 0.5 * (o[0] + o[1]), (o[1] - o[0]) / (t1 - t0), who
    return o[0], v[0], who


def _sky_context(ra: float, dec: float, mjd: float) -> dict:
    lon, lat = geometry.ecliptic(ra, dec)
    return {"ecliptic_latitude": round(float(lat), 2), "elongation_deg": round(geometry.elongation(ra, dec, mjd), 1),
            "antisolar_pa_deg": round(geometry.antisolar_pa(ra, dec, mjd), 1)}


def _strip(images, positions, rows, k: int = STRIP_FRAMES) -> list[dict]:
    if not len(images):
        return []
    pick = np.unique(np.linspace(0, len(images) - 1, min(k, len(images))).round().astype(int))
    out = []
    for i in pick:
        st = coma_mod.shift_stamp(images[i], *positions[i])
        out.append({"mjd": rows[i]["mjd"], "band": rows[i]["band"], "x": round(float(positions[i][0]), 2),
                    "y": round(float(positions[i][1]), 2), "image": coma_mod._list(st)})
    return out


# ---------------------------------------------------------------- fixed field: the hunt
def _tracklet_model(t: dict) -> dict:
    det = t["detections"]
    tt = np.array([d["mjd"] for d in det])
    t_ref = float(tt.mean())
    A = np.column_stack([np.ones_like(tt), tt - t_ref])
    cx, *_ = np.linalg.lstsq(A, np.array([d["x"] for d in det]), rcond=None)
    cy, *_ = np.linalg.lstsq(A, np.array([d["y"] for d in det]), rcond=None)
    return {"x0": float(cx[0]), "y0": float(cy[0]), "vx": float(cx[1]), "vy": float(cy[1]), "w": 0.0,
            "t_ref": t_ref, "t_lo": float(tt.min() - FAST_PAD_D), "t_hi": float(tt.max() + FAST_PAD_D)}


def _fast_tracklets(fd, opts: HuntOptions, field_result: dict | None, check) -> tuple[list[dict], dict]:
    if field_result is not None:
        tr = (field_result.get("candidates") or {}).get("tracklets") or []
        ident = ((field_result.get("stats") or {}).get("transients") or {}).get("identification")
        return [t for t in tr if t.get("n", 0) >= 3], {"source": "field job", "identification": ident}
    from ..field.transients import detect, difference_maps, link

    maps = difference_maps(fd, check)
    dets, st = detect(fd, check, maps)
    tracklets, _ = link(dets, fd.grid.pixscale)
    for t in tracklets:  # not identified yet: the search does it for the best candidates
        t.pop("identification", None)
        t.pop("known", None)
    return tracklets, {"source": "detected", **st}


def hunt(ctx: SequenceContext, opts: HuntOptions, progress, check: Check, field_result: dict | None) -> dict:
    from .frames import FieldImages

    t_start = time.monotonic()
    secs: dict[str, float] = {}
    fd = load_field(ctx)
    meta = fd.meta
    pix = fd.grid.pixscale
    n = fd.n
    exps = meta["exposures"]
    progress("reading exposures", 0, 1)
    fi = FieldImages(fd, check)
    secs["read"] = time.monotonic() - t_start
    progress("fast movers", 0, 1)
    t0 = time.monotonic()
    tracklets, fast_stats = _fast_tracklets(fd, opts, field_result, check)
    secs["fast"] = time.monotonic() - t0
    raw: list[dict] = []
    for t in tracklets:
        raw.append({"kind": "tracklet", "epoch": int(t["epoch"]), "model": _tracklet_model(t), "source": t,
                    "prelim": float(t.get("score") or t.get("mean_snr") or 0.0)})
    # movers seen in a single short pass (minutes): too fast to be in the field at the next pass
    n_pass = 0
    for e in sorted({exps[i]["epoch"] for i in fi.diff}):
        for p in slow.pass_tracklets(fi, e, pix, check):
            m = {k: p[k] for k in ("x0", "y0", "vx", "vy", "w", "t_ref")}
            if any(r["epoch"] == e and abs(r["model"]["t_ref"] - p["t_ref"]) < 0.1 and math.hypot(
                    r["model"]["x0"] + r["model"]["vx"] * (p["t_ref"] - r["model"]["t_ref"]) - p["x0"],
                    r["model"]["y0"] + r["model"]["vy"] * (p["t_ref"] - r["model"]["t_ref"]) - p["y0"]) < 2.0
                   for r in raw):
                continue  # the field analysis' linker has it
            ra, dec = fd.sky([d["x"] for d in p["detections"]], [d["y"] for d in p["detections"]])
            for d, a, b in zip(p["detections"], ra, dec):
                d["ra"], d["dec"] = float(a), float(b)
            tt = [d["mjd"] for d in p["detections"]]
            m |= {"t_lo": min(tt) - FAST_PAD_D, "t_hi": max(tt) + FAST_PAD_D}
            raw.append({"kind": "tracklet", "epoch": e, "model": m, "prelim": 3.0 + p["mean_snr"],
                        "source": {"id": f"P{n_pass + 1:03d}", "n": len(p["detections"]), "mean_snr": p["mean_snr"],
                                   "rms_arcsec": p["rms"] * pix, "span_minutes": p["span_minutes"],
                                   "detections": p["detections"], "quality": "pass"}})
            n_pass += 1
    fast_stats["pass_tracklets"] = n_pass
    # parallax basis per visit (SPHEREx states for every exposure time, cached)
    observer = "SPHEREx"
    basis_by_epoch = {}
    epochs = sorted({exps[i]["epoch"] for i in fi.diff})
    for e in epochs:
        idx = fi.exposures(e)
        tt = np.array([exps[i]["mjd"] for i in idx])
        order = np.argsort(tt)
        tt = tt[order]
        t_mid = float(0.5 * (tt.min() + tt.max()))
        b_arc, who = geometry.parallax_basis(tt, fd.grid.ra, fd.grid.dec, t_mid)
        if who != "SPHEREx":
            observer = who
        bpx = np.column_stack([-b_arc[:, 0] / pix, b_arc[:, 1] / pix])  # grid x grows to the west

        def basis(times, tt=tt, bpx=bpx):
            times = np.atleast_1d(np.asarray(times, float))
            return np.column_stack([np.interp(times, tt, bpx[:, 0]), np.interp(times, tt, bpx[:, 1])])

        basis_by_epoch[e] = basis
    slow_stats = {"links": 0, "stack_hits": 0, "stack": [], "groups": {}}
    if opts.slow or opts.stack:
        t0 = time.monotonic()
        for k, e in enumerate(epochs):
            progress("slow movers", k, len(epochs))
            gs = slow.groups(fi, e, check)
            slow_stats["groups"][str(e)] = len(gs)
            if len(gs) < 3:
                continue
            basis = basis_by_epoch[e]
            links = []
            if opts.slow:
                dets = slow.detections(gs)
                links = slow.link(dets, gs, basis, pix, opts.window_days, check=check)
                for L in links:
                    mem = L["members"]
                    raw.append({"kind": "slow", "epoch": e, "prelim": 5.0 + L["mean_snr"],
                                "model": {k2: L[k2] for k2 in ("x0", "y0", "vx", "vy", "w", "t_ref")} |
                                {"t_lo": float(dets["t"][mem].min() - 0.03), "t_hi": float(dets["t"][mem].max() + 0.03)},
                                "source": {"span_days": L["span_days"], "groups": len(L["groups"]), "rms_px": L["rms"],
                                           "motion_sigma": L["motion_sigma"], "distance_au": L["distance_au"],
                                           "w_au^-1": L["w"], "detections": [
                                               {"t": float(dets["t"][j]), "x": float(dets["x"][j]),
                                                "y": float(dets["y"][j]), "snr": float(dets["snr"][j])} for j in mem]}})
                slow_stats["links"] += len(links)
            if opts.stack:
                hits, st = slow.stack_search(gs, basis, pix, rate_max=opts.stack_rate_max,
                                             window_days=opts.window_days, check=check)
                slow_stats["stack"].append({"epoch": e, **st})
                for h in hits:
                    near = any(abs(h["t_ref"] - r["model"]["t_ref"]) < opts.window_days and r["epoch"] == e and
                               math.hypot(*(np.array(slow.predict(r["model"], [h["t_ref"]], basis([h["t_ref"]])))[:, 0]
                                            - [h["x0"], h["y0"]])) < 3.0 for r in raw if r["kind"] == "slow")
                    if near:
                        continue
                    raw.append({"kind": "stack", "epoch": e, "prelim": h["snr"],
                                "model": {k2: h[k2] for k2 in ("x0", "y0", "vx", "vy", "w", "t_ref")} |
                                {"t_lo": h["window_mjd"][0] - 0.03, "t_hi": h["window_mjd"][1] + 0.03},
                                "source": {"snr": h["snr"], "halves_snr": h["halves_snr"], "groups": len(h["groups"]),
                                           "step_px_per_day": h["step_px_per_day"]}})
                    slow_stats["stack_hits"] += 1
        secs["slow"] = time.monotonic() - t0
    raw.sort(key=lambda r: -r["prelim"])
    raw = raw[:MAX_CANDIDATES]
    # PSFs of the field stars per detector (the candidates themselves excluded)
    t0 = time.monotonic()
    by_band: dict[str, list] = {}
    noise_by_band: dict[str, list] = {}
    for i in sorted(fi.image):
        b = exps[i]["band"]
        if len(by_band.setdefault(b, [])) < coma_mod.MAX_EXPOSURES_PER_BAND:
            by_band[b].append(fi.image[i])
            noise_by_band.setdefault(b, []).append(fi.raw_noise[i])
    fwhm = {}
    for b in BANDS:
        v = [float(x.get("psf_fwhm") or 0) for x in exps if x["band"] == b and x.get("psf_fwhm")]
        if v:
            fwhm[b] = float(np.median(v)) / pix
    psfs = coma_mod.star_psfs(by_band, noise_by_band, fallback_fwhm_px=fwhm)
    secs["psf"] = time.monotonic() - t0
    # measure every candidate
    t0 = time.monotonic()
    out = []
    dropped: dict[str, int] = {}
    for k, r in enumerate(raw):
        if k % 5 == 0:
            progress("measuring candidates", k, len(raw))
            check()
        c = _measure(fd, fi, r, basis_by_epoch[r["epoch"]], psfs, pix, n)
        if c is None:
            continue
        if (c["coma"].get("snr") or 0) < MIN_TRACK_SNR:
            dropped[c["kind"]] = dropped.get(c["kind"], 0) + 1  # not confirmed along the track: noise alignments
            continue
        out.append(c)
    secs["measure"] = time.monotonic() - t0
    slow_stats["unconfirmed"] = dropped
    out.sort(key=lambda c: -c["comet_likeness"]["score"])
    # identification of the best ones (the measured kinds first: stack hits are the least certain)
    ident_stats = fast_stats.get("identification") if field_result is not None else None
    if opts.identify and out:
        progress("identifying", 0, 1)
        t0 = time.monotonic()
        order = sorted(out, key=lambda c: (c["kind"] == "stack", -c["comet_likeness"]["score"]))
        ident_stats = _identify(fd, order, opts.vmag_lim, check)
        secs["identify"] = time.monotonic() - t0
    for c in out:
        c["status"] = ("known" if c.get("known") else "unidentified" if c.get("identification") is not None
                       else "unverified")
    for i, c in enumerate(out):
        c["id"] = f"H{i + 1:03d}"
        if i >= STRIP_FOR:
            c.pop("strip", None)
    secs["total"] = time.monotonic() - t_start
    tracklets_ades = _ades_tracklets(out, ctx.seq_id)
    counts = {"candidates": len(out), "tracklet": sum(c["kind"] == "tracklet" for c in out),
              "slow": sum(c["kind"] == "slow" for c in out), "stack": sum(c["kind"] == "stack" for c in out),
              "unidentified": sum(c["status"] == "unidentified" for c in out),
              "comet_like": sum(c["comet_likeness"]["score"] >= 50 for c in out)}
    return {"kind": "comet_search", "mode": "field", "version": SEARCH_VERSION, "sequence": ctx.seq_id,
            "options": opts.to_dict(), "observer": observer, "counts": counts,
            "stats": {"fast": {k: v for k, v in fast_stats.items() if k != "identification"},
                      "slow": slow_stats, "identification": ident_stats, "psf": {b: p["source"] for b, p in psfs.items()},
                      "single_visit_template": fi.single_visit, "seconds": {k: round(v, 2) for k, v in secs.items()}},
            "candidates": {"comets": out, "tracklets": tracklets_ades}, "caveats": CAVEATS}


def _measure(fd, fi, r: dict, basis, psfs, pix: float, n: int) -> dict | None:
    exps = fd.meta["exposures"]
    m = r["model"]
    idx = [i for i in fi.exposures(r["epoch"]) if m["t_lo"] <= exps[i]["mjd"] <= m["t_hi"]]
    if not idx:
        return None
    tt = np.array([exps[i]["mjd"] for i in idx])
    px, py = slow.predict(m, tt, basis(tt))
    inside = (px >= coma_mod.HALF + 1) & (px <= n - coma_mod.HALF - 2) & (py >= coma_mod.HALF + 1) & \
             (py <= n - coma_mod.HALF - 2)
    idx = [i for i, ok in zip(idx, inside) if ok]
    if len(idx) < 3:
        return None
    px, py, tt = px[inside], py[inside], tt[inside]
    images = [fi.diff[i] for i in idx]
    noises = [fi.noise[i] for i in idx]
    rows_meta = [exps[i] for i in idx]
    bands = [x["band"] for x in rows_meta]
    positions = list(zip(px.tolist(), py.tolist()))
    # forced photometry along the track
    spec_rows, dets = [], []
    ras, decs = fd.sky(px, py)
    for k, i in enumerate(idx):
        a = _aperture(images[k], px[k], py[k], noises[k], pix)
        if a is None:
            continue
        f, e = a
        spec_rows.append({"wl": rows_meta[k].get("wl"), "dwl": rows_meta[k].get("dwl"), "flux_mjy": f, "err_mjy": e,
                          "band": bands[k]})
        dets.append({"exposure": int(i), "mjd": float(tt[k]), "band": bands[k], "x": round(float(px[k]), 3),
                     "y": round(float(py[k]), 3), "ra": float(ras[k]), "dec": float(decs[k]),
                     "snr": round(f / e, 2) if e > 0 else None})
    # geometry
    t_mid = float(np.median(tt))
    ra_a, dec_a = fd.sky([float(np.median(px))], [float(np.median(py))])
    ra_m, dec_m = float(ra_a[0]), float(dec_a[0])
    v = math.hypot(m["vx"], m["vy"])
    rate = v * pix / 24.0
    pa = float(math.degrees(math.atan2(-m["vx"], m["vy"])) % 360.0)
    sky = _sky_context(ra_m, dec_m, t_mid)
    stamps, weights = coma_mod.object_stamps(images, positions, noises)
    span = float(tt.max() - tt.min())
    jitter = 0.25 if r["kind"] == "tracklet" else 0.35
    cm = coma_mod.coma_test(stamps, weights, bands, psfs, pixscale=pix, antisolar_pa=sky["antisolar_pa_deg"],
                            motion_pa=pa if rate > 0.3 else None, jitter_px=jitter,
                            interp=coma_mod.interp_var(positions))
    sig = signature(spec_rows)
    o, ov, _ = _observer_motion(float(tt.min()), float(tt.max()))
    d_fit = (r["source"] or {}).get("distance_au") if r["kind"] == "slow" else None
    dist = (d_fit / 1.3, d_fit * 1.3) if d_fit else None  # the parallax fit's distance, generously
    orb = orbits.orbit_classes(ra_m, dec_m, rate, pa, o, ov, dist) if rate > 0 else None
    score = comet_likeness(orb, cm, sig)
    src = r["source"]
    cand = {"kind": r["kind"], "epoch": r["epoch"], "ra": ra_m, "dec": dec_m, "mjd": t_mid,
            "x": round(float(np.median(px)), 2), "y": round(float(np.median(py)), 2),
            "rate_arcsec_per_hr": round(rate, 4), "pa_deg": round(pa, 1), "span_hours": round(span * 24, 2),
            "n_exposures": len(idx), "bands": sorted(set(bands), key=BANDS.index),
            "model": {k: (round(v2, 6) if isinstance(v2, float) else v2) for k, v2 in m.items()},
            **sky, "comet_likeness": score, "coma": cm, "spectrum": sig, "orbit": orb,
            "path": [[round(float(a), 2), round(float(b), 2)] for a, b in positions],
            "forced": dets, "strip": _strip(images, positions, rows_meta)}
    if r["kind"] == "tracklet":
        cand["tracklet"] = {k: src.get(k) for k in ("id", "n", "mean_snr", "mean_rb", "partner_fraction", "rms_arcsec",
                                                     "quality", "score")}
        cand["detections"] = [{k: d.get(k) for k in ("exposure", "mjd", "band", "x", "y", "ra", "dec", "snr")}
                              for d in src["detections"]]
        if "identification" in src:
            cand["identification"] = src.get("identification")
            cand["known"] = src.get("known")
    elif r["kind"] == "slow":
        cand["link"] = {k: v2 for k, v2 in src.items() if k != "detections"}
        if src.get("distance_au"):
            cand["distance_au"] = round(src["distance_au"], 3)
        gx, gy = [d["x"] for d in src["detections"]], [d["y"] for d in src["detections"]]
        gra, gdec = fd.sky(gx, gy)
        cand["detections"] = [{"mjd": d["t"], "band": "/".join(cand["bands"]), "x": d["x"], "y": d["y"],
                               "ra": float(a), "dec": float(b), "snr": round(d["snr"], 2)}
                              for d, a, b in zip(src["detections"], gra, gdec)]
    else:
        cand["stack"] = src
    return cand


def _identify(fd, cands: list[dict], vmag_lim: float, check: Check = no_check) -> dict:
    """JPL sb_ident (asteroids and comets) at each candidate's first and last time, SPHEREx-centric, one candidate
    at a time with interactive timeouts and an overall budget (JPL rate-limits bursts)."""
    from .. import netpolicy
    from ..field import known

    todo = [c for c in cands if "identification" not in c][:MAX_IDENTIFY]
    stats = {"queried": 0, "matched": 0, "skipped": 0, "error": None, "observer": "SPHEREx",
             "positions": "SPHEREx-centric, at each candidate's first and last time"}
    t0 = time.monotonic()
    for c in todo:
        check()
        if time.monotonic() - t0 > IDENTIFY_BUDGET_S:
            stats["skipped"] += 1
            continue
        det = c.get("detections") or c["forced"]
        t = {"id": "", "epoch": c["epoch"], "detections": det, "rate_arcsec_per_hr": c["rate_arcsec_per_hr"],
             "pa_deg": c["pa_deg"]}
        try:
            with netpolicy.fast():
                st = known.identify(fd, [t], vmag_lim)
        except Exception as e:  # noqa: BLE001 - JPL unreachable: the candidate stays unverified
            st = {"error": f"{e.__class__.__name__}: {e}"[:200]}
        if st.get("error"):
            stats["error"] = st["error"]
        if st.get("observer") == "geocentre":
            stats["observer"], stats["positions"] = "geocentre", st.get("positions")
        stats["queried"] += 1
        if "identification" in t:
            c["identification"] = t["identification"]
            c["known"] = t.get("known")
            stats["matched"] += t.get("known") is not None
    return stats


def _ades_tracklets(cands: list[dict], seq_id: str) -> list[dict]:
    out = []
    for i, c in enumerate(cands):
        det = c.get("detections") or []
        if len(det) < 3:
            continue
        good = c["kind"] in ("tracklet", "slow") and c["comet_likeness"]["score"] >= 30
        out.append({"id": f"H{seq_id[:3]}{i + 1:03d}"[:8], "candidate": c["id"], "epoch": c["epoch"],
                    "n": len(det), "detections": det, "rate_arcsec_per_hr": c["rate_arcsec_per_hr"],
                    "pa_deg": c["pa_deg"], "quality": "high" if good else "low", "known": c.get("known")})
    return out


# ---------------------------------------------------------------- co-moving: the object check
def object_check(ctx: SequenceContext, opts: HuntOptions, progress, check: Check) -> dict:
    from ..solar.common import load_frames
    from ..solar.geometry import exposure_geometry

    t_start = time.monotonic()
    meta = ctx.meta
    exps = meta["exposures"]
    pix = ctx.grid.pixscale
    n = ctx.grid.n
    c0 = (n - 1) / 2.0
    progress("reading exposures", 0, 1)
    frames = load_frames(ctx, prefer_clean=True)
    plain = frames
    if frames.clean and (meta.get("arrays") or {}).get("exposures"):
        try:
            plain = load_frames(ctx, prefer_clean=False)
        except NotFound:
            plain = frames
    check()
    # reference PSFs from stars in the plain exposures (the object itself excluded)
    by_band, noise_by_band = {}, {}
    for i, e in enumerate(exps):
        if plain.usable[i] and len(by_band.setdefault(e["band"], [])) < coma_mod.MAX_EXPOSURES_PER_BAND:
            by_band[e["band"]].append(plain.images[i])
            noise_by_band.setdefault(e["band"], []).append(float(plain.noise[i]))
    fwhm = {b: float(np.median([x["psf_fwhm"] for x in exps if x["band"] == b and x.get("psf_fwhm")])) / pix
            for b in BANDS if any(x["band"] == b and x.get("psf_fwhm") for x in exps)}
    psfs = coma_mod.star_psfs(by_band, noise_by_band, exclude=[(c0, c0)], fallback_fwhm_px=fwhm)
    try:
        geom = exposure_geometry(meta)
    except Exception as e:  # noqa: BLE001
        log.warning("object geometry failed: %r", e)
        geom = [{}] * len(exps)
    rows = []
    for ep in meta["epochs"]:
        check()
        progress("visits", ep["index"], len(meta["epochs"]))
        idx = [i for i, e in enumerate(exps) if e["epoch"] == ep["index"] and frames.usable[i]
               and np.isfinite(frames.noise[i])]
        if len(idx) < 3:
            continue
        rows.append(_object_epoch(ctx, ep, idx, frames, psfs, geom, pix, c0))
    out = {"kind": "comet_search", "mode": "object", "version": SEARCH_VERSION, "sequence": ctx.seq_id,
           "object": (meta.get("moving") or {}).get("object"), "options": opts.to_dict(),
           "image_source": "static-sky subtracted" if frames.clean else "plain (stars not removed)",
           "psf": {b: p["source"] for b, p in psfs.items()},
           "seconds": round(time.monotonic() - t_start, 2), "caveats": CAVEATS}
    for i, r in enumerate(rows):
        r["id"] = f"V{r['epoch'] + 1}"
        r["status"] = "known"
        r["known"] = {"name": out["object"], "kind": "comet" if "/" in str(out["object"]) or
                      str((meta.get("params") or {}).get("object_type", "")).lower() == "comet" else "object"}
    scores = [r["comet_likeness"]["score"] for r in rows]
    out["summary"] = {"visits": len(rows), "max_score": max(scores) if scores else None,
                      "median_score": float(np.median(scores)) if scores else None,
                      "extended_visits": sum((r["coma"].get("verdict") == "extended") for r in rows),
                      "gas_visits": sum(((r["spectrum"].get("gas_sigma") or 0) >= 3) for r in rows)}
    out["candidates"] = {"comets": rows, "tracklets": []}
    return out


def _object_epoch(ctx, ep: dict, idx: list[int], frames, psfs, geom, pix: float, c0: float) -> dict:
    exps = ctx.meta["exposures"]
    images = [frames.images[i] for i in idx]
    noises = [float(frames.noise[i]) for i in idx]
    bands = [exps[i]["band"] for i in idx]
    # registration: the ephemeris puts the object on the centre; refine one offset for the visit, and per exposure
    # when the object is bright enough to centroid in single frames
    st0, w0 = coma_mod.object_stamps(images, [(c0, c0)] * len(idx), noises)
    obj0 = coma_mod._mean(st0, w0)
    dx, dy = coma_mod.centroid(obj0) if obj0 is not None else (0.0, 0.0)
    positions = [(c0 + dx, c0 + dy)] * len(idx)
    per_exp = []
    for k, img in enumerate(images):
        a = _aperture(img, c0 + dx, c0 + dy, noises[k], pix)
        per_exp.append(a[0] / a[1] if a and a[1] > 0 else 0.0)
    jitter = 0.25
    if float(np.median(per_exp)) >= 12:
        refined = []
        for k, img in enumerate(images):
            st = coma_mod.shift_stamp(img, *positions[k])
            ddx, ddy = coma_mod.centroid(st, max_shift=1.0)
            refined.append((positions[k][0] + ddx, positions[k][1] + ddy))
        positions = refined
        jitter = 0.1
    stamps, weights = coma_mod.object_stamps(images, positions, noises)
    tt = np.array([exps[i]["mjd"] for i in idx])
    # apparent motion of the object from the ephemeris (exposure centres) around the visit's middle
    t_mid = float(np.median(tt))
    near = [i for i in idx if abs(exps[i]["mjd"] - t_mid) <= 0.75]
    if len(near) < 2 or np.ptp([exps[i]["mjd"] for i in near]) < 0.05:
        near = sorted(idx, key=lambda i: abs(exps[i]["mjd"] - t_mid))[:max(3, len(idx) // 4)]
    tn = np.array([exps[i]["mjd"] for i in near])
    ra_n = np.array([exps[i]["center"][0] for i in near])
    dec_n = np.array([exps[i]["center"][1] for i in near])
    ra_m, dec_m = float(np.interp(t_mid, np.sort(tn), ra_n[np.argsort(tn)])), float(np.interp(t_mid, np.sort(tn), dec_n[np.argsort(tn)]))
    rate = pa = None
    if tn.max() - tn.min() > 0.01:
        A = np.column_stack([np.ones_like(tn), tn - t_mid])
        cosd = math.cos(math.radians(dec_m))
        dra = ((ra_n - ra_m + 180.0) % 360.0 - 180.0) * cosd * 3600.0
        ddec = (dec_n - dec_m) * 3600.0
        ve = float(np.linalg.lstsq(A, dra, rcond=None)[0][1]) / 24.0
        vn = float(np.linalg.lstsq(A, ddec, rcond=None)[0][1]) / 24.0
        rate, pa = math.hypot(ve, vn), geometry.pa_of(ve, vn)
    sky = _sky_context(ra_m, dec_m, t_mid)
    cm = coma_mod.coma_test(stamps, weights, bands, psfs, pixscale=pix, antisolar_pa=sky["antisolar_pa_deg"],
                            motion_pa=pa, jitter_px=jitter, interp=coma_mod.interp_var(positions))
    spec_rows = []
    for k, img in enumerate(images):
        a = _aperture(img, *positions[k], noises[k], pix)
        if a:
            e = exps[idx[k]]
            spec_rows.append({"wl": e.get("wl"), "dwl": e.get("dwl"), "flux_mjy": a[0], "err_mjy": a[1], "band": bands[k]})
    sig = signature(spec_rows)
    orb = None
    g = [geom[i] for i in idx if geom[i].get("r_au")]
    if rate:
        o, ov, _ = _observer_motion(float(tn.min()), float(tn.max()))
        # the ephemeris knows the distance: rank the orbit classes at it (and its rate of change)
        gd = [(exps[i]["mjd"], geom[i]["delta_au"]) for i in idx if geom[i].get("delta_au")]
        near_gd = [x for x in gd if abs(x[0] - t_mid) <= 3.0]
        gd = near_gd if len(near_gd) >= 2 else gd
        dist = rr = None
        if len(gd) >= 2:
            tg, dg = map(np.asarray, zip(*sorted(gd)))
            d_mid = float(np.interp(t_mid, tg, dg))
            dist = (0.97 * d_mid, 1.03 * d_mid)
            if tg[-1] - tg[0] > 0.2:
                slope = float(np.polyfit(tg - t_mid, dg, 1)[0]) / orbits.KMS_AU_DAY  # km/s
                rr = (slope - 2.0, slope + 2.0)
        orb = orbits.orbit_classes(ra_m, dec_m, rate, pa, o, ov, dist, rr)
    return {"kind": "object", "epoch": ep["index"], "dates": [ep["date_min"], ep["date_max"]], "ra": ra_m, "dec": dec_m,
            "mjd": t_mid, "x": round(positions[0][0], 2), "y": round(positions[0][1], 2),
            "rate_arcsec_per_hr": round(rate, 3) if rate else None, "pa_deg": round(pa, 1) if pa is not None else None,
            "n_exposures": len(idx), "bands": sorted(set(bands), key=BANDS.index),
            "r_au": float(np.median([x["r_au"] for x in g])) if g else None,
            "delta_au": float(np.median([x["delta_au"] for x in g])) if g else None,
            **sky, "comet_likeness": comet_likeness(orb, cm, sig), "coma": cm, "spectrum": sig, "orbit": orb,
            "registration": {"offset_px": [round(dx, 3), round(dy, 3)], "per_exposure": jitter < 0.2},
            "strip": _strip(images, positions, [exps[i] for i in idx])}


def load_field_result(seq_id: str, job_id: str | None) -> dict | None:
    """A finished field analysis of the same sequence to reuse (ValueError / NotFound otherwise)."""
    if not job_id:
        return None
    from ..field.jobs import result_path, split_id

    seq, key = split_id(job_id)
    if seq != seq_id or key[0] != "f":
        raise ValueError("field_job must be a field analysis job of the same sequence")
    p = result_path(job_id)
    if not p.exists():
        raise NotFound("field analysis result not ready")
    return json.loads(p.read_text())
