"""Moving objects inside a visit (asteroids, comets) from single exposures.

For every exposure the epoch co-add of its detector is subtracted. The co-add rejects outliers per pixel, so
an asteroid present in one exposure is not in it and stands out in the difference. Peaks are scored by the
real/bogus model, then linked within each epoch into tracklets: three or more detections at different times
on a straight line with constant rate. Tracklets are checked against known minor planets with the JPL
Small-Body Identification API (geocentric; SPHEREx's low orbit shifts nearby objects by at most a few
arcseconds) and exported as ADES PSV for follow-up (not submission-ready: positions are on our resampled grid
and the observatory code is a geocentric placeholder).
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import numpy as np
from scipy.ndimage import binary_dilation

from ..bands import BANDS
from ..errors import NotFound
from ..pipeline import results
from . import realbogus
from .common import Check, FieldData, local_peaks, no_check, robust_noise, snr_image

DETECT_SNR = 6.0
BRIGHT_SNR = 10.0  # co-add S/N above which a star's wavelength-dependent residuals dominate (masked)
BRIGHT_GROW = 2
MAX_SHARPNESS = 0.75  # a single hot pixel or cosmic ray, not a point source
MIN_RB = 0.3  # detections below this real/bogus probability are not linked
MIN_DT = 0.01  # d between linked detections (~15 min)
MAX_DT = 3.0  # d
MIN_MOVE = 0.8  # px between the two seed detections
MIN_RATE = 7.8  # px/day (~2"/h): slower "tracklets" are static residuals
MIN_SPAN = 2.0  # px covered by a tracklet
MAX_RATE = 3000.0  # px/day (~770"/h)
LINK_TOL = 1.5  # px around the predicted position
MAX_RMS = 1.0  # px rms about the fitted line
SB_IDENT = "https://ssd-api.jpl.nasa.gov/sb_ident.api"
log = logging.getLogger(__name__)


def mjd_to_datetime(mjd: float) -> datetime:
    return datetime(1858, 11, 17) + timedelta(days=float(mjd))


def detect(fd: FieldData, check: Check = no_check) -> tuple[list[dict], dict]:
    """Detections in exposure-minus-co-add S/N images, with features and real/bogus probabilities."""
    meta = fd.meta
    exposures = meta["exposures"]
    dets: list[dict] = []
    feats: list[np.ndarray] = []
    n_images = 0
    for e in range(fd.coadds.shape[0]):
        for b, band in enumerate(BANDS):
            coadd = fd.coadds[e, b]
            if not np.isfinite(coadd).any():
                continue
            try:
                stack, idx = results.read_exposures(fd.ctx.dir, meta, e, band)
            except NotFound:
                continue
            csnr = snr_image(coadd)
            bright = binary_dilation(np.nan_to_num(csnr, nan=0.0) > BRIGHT_SNR, iterations=BRIGHT_GROW)
            by, bx = np.nonzero(np.nan_to_num(csnr, nan=0.0) > BRIGHT_SNR)
            bright_xy = np.column_stack([bx, by]) if bx.size else None
            flags = fd.flags[e, b]
            for k, i in enumerate(idx):
                d = stack[k].astype(float) - coadd
                _, noise = robust_noise(d)
                if not (noise > 0):
                    continue
                s = d / noise
                s[bright] = np.nan
                n_images += 1
                for y, x in zip(*local_peaks(s, DETECT_SNR, size=5, edge=3)):
                    ex = exposures[i]
                    fv = realbogus.features(s, flags, int(x), int(y), bright_xy)
                    if fv[1] > MAX_SHARPNESS:
                        continue
                    feats.append(fv)
                    dets.append({"exposure": int(i), "epoch": e, "band": band, "mjd": float(ex["mjd"]),
                                 "x": float(x), "y": float(y), "snr": float(s[y, x]),
                                 "flux": float(d[y, x])})
            check()
    if dets:
        probs = realbogus.score(np.array(feats))
        for det, p, f in zip(dets, probs, feats):
            det["rb"] = float(p)
            det["features"] = {name: float(v) for name, v in zip(realbogus.FEATURES, f)}
        ra, dec = fd.sky([d["x"] for d in dets], [d["y"] for d in dets])
        for det, r, dd in zip(dets, ra, dec):
            det["ra"], det["dec"] = float(r), float(dd)
    return dets, {"images": n_images, "detections": len(dets),
                  "model": "trained" if realbogus.load_model() else "heuristic"}


def _fit_line(pts: list[dict]) -> tuple[np.ndarray, np.ndarray, float]:
    t = np.array([p["mjd"] for p in pts])
    a = np.column_stack([np.ones_like(t), t - t.mean()])
    cx, *_ = np.linalg.lstsq(a, np.array([p["x"] for p in pts]), rcond=None)
    cy, *_ = np.linalg.lstsq(a, np.array([p["y"] for p in pts]), rcond=None)
    res = np.hypot(a @ cx - [p["x"] for p in pts], a @ cy - [p["y"] for p in pts])
    return cx, cy, float(np.sqrt(np.mean(res ** 2)))


def link(dets: list[dict], pixscale: float) -> tuple[list[dict], list[dict]]:
    """Tracklets (>= 3 detections) and strong pairs, per epoch."""
    tracklets, pairs = [], []
    for e in sorted({d["epoch"] for d in dets}):
        pool = sorted([d for d in dets if d["epoch"] == e and d["rb"] >= MIN_RB], key=lambda d: d["mjd"])
        used: set[int] = set()
        seeds = []
        for i, a in enumerate(pool):
            for j in range(i + 1, len(pool)):
                b = pool[j]
                dt = b["mjd"] - a["mjd"]
                if dt < MIN_DT:
                    continue
                if dt > MAX_DT:
                    break
                dist = np.hypot(b["x"] - a["x"], b["y"] - a["y"])
                if dist < MIN_MOVE or dist / dt > MAX_RATE or dist / dt < MIN_RATE:
                    continue
                seeds.append((a["snr"] + b["snr"], i, j))
        seeds.sort(reverse=True)
        for _, i, j in seeds:
            if i in used or j in used:
                continue
            a, b = pool[i], pool[j]
            dt = b["mjd"] - a["mjd"]
            vx, vy = (b["x"] - a["x"]) / dt, (b["y"] - a["y"]) / dt
            members = [i, j]
            for k, c in enumerate(pool):
                if k in used or k in (i, j) or min(abs(c["mjd"] - a["mjd"]), abs(c["mjd"] - b["mjd"])) < MIN_DT / 2:
                    continue
                px, py = a["x"] + vx * (c["mjd"] - a["mjd"]), a["y"] + vy * (c["mjd"] - a["mjd"])
                if np.hypot(c["x"] - px, c["y"] - py) <= LINK_TOL:
                    members.append(k)
            pts = sorted((pool[k] for k in members), key=lambda d: d["mjd"])
            # one detection per exposure time
            uniq = {round(p["mjd"], 4): p for p in pts}
            pts = sorted(uniq.values(), key=lambda d: d["mjd"])
            if len(pts) >= 3:
                cx, cy, rms = _fit_line(pts)
                span = float(np.hypot(pts[-1]["x"] - pts[0]["x"], pts[-1]["y"] - pts[0]["y"]))
                along = [(p["x"] - pts[0]["x"]) * vx + (p["y"] - pts[0]["y"]) * vy for p in pts]
                if rms > MAX_RMS or span < MIN_SPAN or any(b2 <= a2 for a2, b2 in zip(along, along[1:])):
                    continue
                used.update(members)
                rate_px_day = float(np.hypot(cx[1], cy[1]))
                pa = float(np.degrees(np.arctan2(-cx[1], cy[1])) % 360.0)  # east is -x
                tracklets.append(_tracklet(len(tracklets), e, pts, rate_px_day * pixscale / 24.0, pa,
                                           rms * pixscale))
            elif a["snr"] >= 10 and b["snr"] >= 10 and a["rb"] >= 0.7 and b["rb"] >= 0.7:
                rate = np.hypot(vx, vy) * pixscale / 24.0
                pa = float(np.degrees(np.arctan2(-vx, vy)) % 360.0)
                pairs.append(_tracklet(len(pairs), e, [a, b], float(rate), pa, None, prefix="P"))
    return tracklets, pairs


def _tracklet(i: int, epoch: int, pts: list[dict], rate: float, pa: float, rms: float | None,
              prefix: str = "T") -> dict:
    keep = ("exposure", "mjd", "band", "x", "y", "ra", "dec", "snr", "rb")
    det = [{k: p[k] for k in keep} for p in pts]
    mid = float(np.mean([p["mjd"] for p in pts]))
    return {"id": f"{prefix}{i + 1:03d}", "epoch": epoch, "n": len(pts), "mjd_mid": mid,
            "rate_arcsec_per_hr": rate, "pa_deg": pa, "rms_arcsec": rms, "detections": det,
            "score": float(np.mean([p["rb"] for p in pts]) * np.sqrt(sum(p["snr"] ** 2 for p in pts))),
            "known": None}


def _sexa_ra(ra: float) -> str:
    h = ra / 15.0
    hh, mm = int(h), int((h - int(h)) * 60)
    ss = (h - hh - mm / 60.0) * 3600
    return f"{hh:02d}-{mm:02d}-{ss:05.2f}"


def _sexa_dec(dec: float) -> str:
    s = "-" if dec < 0 else ""
    a = abs(dec)
    dd, mm = int(a), int((a - int(a)) * 60)
    ss = (a - dd - mm / 60.0) * 3600
    return f"{s}{dd:02d}-{mm:02d}-{ss:04.1f}"


def _parse_ra(text: str) -> float:
    h, m, s = (float(v) for v in text.split(":"))
    return 15.0 * (h + m / 60 + s / 3600)


def _parse_dec(text: str) -> float:
    t = text.replace("'", " ").replace('"', " ").split()
    sign = -1.0 if t[0].startswith("-") else 1.0
    d, m, s = abs(float(t[0])), float(t[1]), float(t[2]) if len(t) > 2 else 0.0
    return sign * (d + m / 60 + s / 3600)


def known_objects(fd: FieldData, mjd: float, timeout: float = 90.0) -> list[dict]:
    """Minor planets and comets in the field at mjd (JPL sb_ident, first pass, geocentric)."""
    half = fd.grid.fov_arcmin / 120.0 + 0.02
    q = {"sb-kind": "a", "mpc-code": "500", "obs-time": mjd_to_datetime(mjd).strftime("%Y-%m-%d_%H:%M:%S"),
         "fov-ra-center": _sexa_ra(fd.grid.ra), "fov-dec-center": _sexa_dec(fd.grid.dec),
         "fov-ra-hwidth": f"{half:.4f}", "fov-dec-hwidth": f"{half:.4f}", "vmag-lim": "21"}
    def fetch(kind: str) -> dict:
        with urllib.request.urlopen(SB_IDENT + "?" + urllib.parse.urlencode({**q, "sb-kind": kind}, safe="-_:"),
                                    timeout=timeout) as r:
            return json.loads(r.read())

    with ThreadPoolExecutor(2) as ex:
        answers = dict(zip(("a", "c"), ex.map(fetch, ("a", "c"))))
    out = []
    for kind, data in answers.items():
        fields = data.get("fields_first") or []
        for row in data.get("data_first_pass") or []:
            rec = dict(zip(fields, row))
            try:
                out.append({"name": rec["Object name"], "ra": _parse_ra(rec["Astrometric RA (hh:mm:ss)"]),
                            "dec": _parse_dec(rec["Astrometric Dec (dd mm'ss\")"]),
                            "vmag": float(rec.get("Visual magnitude (V)") or "nan"),
                            "ra_rate": float(rec.get('RA rate ("/h)') or 0.0),
                            "dec_rate": float(rec.get('Dec rate ("/h)') or 0.0), "mjd": mjd,
                            "kind": "comet" if kind == "c" else "asteroid"})
            except (KeyError, ValueError):
                continue
    return out


def identify(fd: FieldData, tracklets: list[dict], match_arcsec: float = 40.0) -> dict:
    """Attach the nearest known object (position propagated with its rates) to every tracklet."""
    stats = {"queries": 0, "objects_in_field": 0, "matched": 0, "error": None}
    epochs = sorted({t["epoch"] for t in tracklets})
    mjds = {e: float(np.mean([t["mjd_mid"] for t in tracklets if t["epoch"] == e])) for e in epochs}

    def query(e: int):
        try:
            return e, known_objects(fd, mjds[e]), None
        except Exception as ex:  # noqa: BLE001 - JPL down or slow: tracklets stay unidentified
            return e, [], ex

    with ThreadPoolExecutor(4) as pool:
        answers = list(pool.map(query, epochs))
    for e, objs, err in answers:
        ts = [t for t in tracklets if t["epoch"] == e]
        if err is not None:
            stats["error"] = f"JPL sb_ident: {err}"[:200]
            log.warning("sb_ident failed: %r", err)
            continue
        stats["queries"] += 1
        stats["objects_in_field"] += len(objs)
        for t in ts:
            best = None
            for d in t["detections"]:
                for o in objs:
                    dt_h = (d["mjd"] - o["mjd"]) * 24.0
                    cosd = np.cos(np.radians(o["dec"]))
                    ra = o["ra"] + o["ra_rate"] * dt_h / 3600.0 / cosd
                    dec = o["dec"] + o["dec_rate"] * dt_h / 3600.0
                    sep = float(np.hypot((d["ra"] - ra) * cosd, d["dec"] - dec) * 3600.0)
                    if sep <= match_arcsec and (best is None or sep < best["sep_arcsec"]):
                        best = {"name": o["name"], "kind": o["kind"], "vmag": o["vmag"], "sep_arcsec": sep,
                                "rate_arcsec_per_hr": float(np.hypot(o["ra_rate"], o["dec_rate"]))}
            t["known"] = best
            stats["matched"] += best is not None
    return stats


def ades_psv(tracklets: list[dict], sequence: str) -> str:
    """ADES pipe-separated observations of the tracklets (for follow-up, not for submission)."""
    lines = ["# version=2022", "# observatory", "! mpcCode 500",
             f"# comment\n! line Planet X x SPHEREx tracklets from sequence {sequence}; positions on the resampled",
             "! line grid, geocentric placeholder station; not submission-ready",
             "trkSub   |mode|stn |obsTime                 |ra         |dec        |rmsRA|rmsDec|mag  |band"]
    for t in tracklets:
        for d in t["detections"]:
            ts = mjd_to_datetime(d["mjd"]).strftime("%Y-%m-%dT%H:%M:%S.%f")[:23] + "Z"
            lines.append(f"{t['id']:<9}|CCD |500 |{ts:<24}|{d['ra']:11.6f}|{d['dec']:+11.6f}|  3.0|   3.0|     |"
                         f"{d['band']}")
    return "\n".join(lines) + "\n"
