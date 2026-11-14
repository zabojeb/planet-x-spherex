"""Known minor planets and comets seen from SPHEREx, and identification of tracklets against them.

Positions come from the JPL Small-Body Identification API with SPHEREx's own geocentric state (`xobs`, from
Horizons) and numerical propagation (`two-pass=true`), so they include SPHEREx's parallax (up to ~9.7"/D for
an object D AU away). SkyBoT with MPC code 288 returns geocentric positions and is not used. Results are
cached per (field, time, V limit).

Identification follows Astrometrica's criteria: a known object matches a tracklet when it is within the
position tolerance at the tracklet's times, its direction of motion agrees within +-15 deg and its speed
within +-30 %; matches are ranked by a score and carry O-C residuals per detection.

The rates sb_ident returns are instantaneous rates as seen from SPHEREx, so they include the spacecraft's
~7.5 km/s orbital velocity: about +-15"/h for an object 2.4 AU away, changing sign every 98-min orbit (for
97 Klotho the Dec rate read -11.7"/h at one moment and +14"/h half an orbit later while the object drifted
south at 2"/h). Propagating with them over hours put the object 25-40" off and made identification fail.
Tracklets are therefore matched against SPHEREx-centric positions queried at the tracklet's first and last
detection times and interpolated linearly between them; speed and direction come from that displacement.
The residual 98-min wobble (up to ~9.7"/D arcsec) stays inside the position tolerance for D > 0.5 AU.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import numpy as np

from .. import cache
from ..logs import timed
from . import observer
from .common import FieldData

SB_IDENT = "https://ssd-api.jpl.nasa.gov/sb_ident.api"
PA_TOL = 15.0  # deg
SPEED_TOL = 0.30  # fraction
POS_TOL = 30.0  # arcsec floor (two-pass positions are good to a few arcsec for numbered objects)
log = logging.getLogger(__name__)


def mjd_to_datetime(mjd: float) -> datetime:
    return datetime(1858, 11, 17) + timedelta(days=float(mjd))


def _sexa_ra(ra: float) -> str:
    h = (ra % 360.0) / 15.0
    hh, mm = int(h), int((h - int(h)) * 60)
    ss = (h - hh - mm / 60.0) * 3600
    return f"{hh:02d}-{mm:02d}-{ss:05.2f}"


def _sexa_dec(dec: float) -> str:
    s = "M" if dec < 0 else ""
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
    d, m = abs(float(t[0])), float(t[1])
    s = float(t[2]) if len(t) > 2 else 0.0
    return sign * (d + m / 60 + s / 3600)


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def query(ra: float, dec: float, half_deg: float, mjd: float, vmag_lim: float = 21.0,
          timeout: float = 170.0, retries: int = 5) -> list[dict]:
    """Asteroids and comets in a box around (ra, dec) at mjd as seen from SPHEREx (cached on disk)."""
    key = hashlib.sha1(f"{ra:.4f}|{dec:.4f}|{half_deg:.3f}|{mjd:.4f}|{vmag_lim}".encode()).hexdigest()[:16]
    path = cache.layout().root / "known_objects" / f"{key}.json"
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        pass
    xobs = ",".join(f"{v:.9E}" for v in observer.state(mjd))
    base = {"obs-time": mjd_to_datetime(mjd).strftime("%Y-%m-%d_%H:%M:%S"), "xobs": xobs,
            "fov-ra-center": _sexa_ra(ra), "fov-dec-center": _sexa_dec(dec),
            "fov-ra-hwidth": f"{half_deg:.4f}", "fov-dec-hwidth": f"{half_deg:.4f}", "two-pass": "true",
            "suppress-first-pass": "true", "vmag-lim": f"{vmag_lim:g}"}

    def fetch(kind: str) -> tuple[str, dict]:
        url = SB_IDENT + "?" + urllib.parse.urlencode({**base, "sb-kind": kind}, safe="-_:,")
        for attempt in range(retries):
            try:
                with urllib.request.urlopen(url, timeout=timeout) as r:
                    return kind, json.loads(r.read())
            except urllib.error.HTTPError as e:  # JPL rate-limits (429) and sheds load (503): back off
                if e.code not in (429, 503) or attempt == retries - 1:
                    raise
                wait = float(e.headers.get("Retry-After") or 0) or 5.0 * 2 ** attempt
                time.sleep(min(wait, 60.0))
        raise RuntimeError("sb_ident retries exhausted")

    with timed(log, "sb_ident", mjd=round(mjd, 4)):
        with ThreadPoolExecutor(2) as ex:
            answers = list(ex.map(fetch, ("a", "c")))
    out = []
    for kind, data in answers:
        fields = data.get("fields_second") or data.get("fields_first") or []
        rows = data.get("data_second_pass") or data.get("data_first_pass") or []
        for row in rows:
            rec = dict(zip(fields, row))
            name = rec.get("Object name")
            ra_t = next((v for k, v in rec.items() if k.startswith("Astrometric RA")), None)
            dec_t = next((v for k, v in rec.items() if k.startswith("Astrometric Dec")), None)
            if not (name and ra_t and dec_t):
                continue
            try:
                o = {"name": name, "kind": "comet" if kind == "c" else "asteroid", "mjd": mjd,
                     "ra": _parse_ra(ra_t), "dec": _parse_dec(dec_t),
                     "vmag": _num(next((v for k, v in rec.items() if k.startswith("Visual magnitude")), None)),
                     "ra_rate": _num(next((v for k, v in rec.items() if k.startswith("RA rate")), None)) or 0.0,
                     "dec_rate": _num(next((v for k, v in rec.items() if k.startswith("Dec rate")), None)) or 0.0,
                     "err_ra": _num(next((v for k, v in rec.items() if k.startswith("Est. error RA")), None)),
                     "err_dec": _num(next((v for k, v in rec.items() if k.startswith("Est. error Dec")), None))}
            except (ValueError, IndexError):
                continue
            out.append(o)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.part")
    tmp.write_text(json.dumps(out))
    tmp.replace(path)
    return out


def position_at(o: dict, mjd: float) -> tuple[float, float]:
    """Position at mjd: interpolated between the queried SPHEREx-centric positions in `track` when present
    (see the module notes), else linear propagation with the instantaneous rates ("/h, RA rate including
    cos dec)."""
    track = o.get("track")
    if track and len(track) >= 2:
        (t0, ra0, dec0), (t1, ra1, dec1) = track[0], track[-1]
        if t1 > t0:
            f = (mjd - t0) / (t1 - t0)
            dra = ((ra1 - ra0 + 180.0) % 360.0) - 180.0
            return (ra0 + f * dra) % 360.0, dec0 + f * (dec1 - dec0)
    dt_h = (mjd - o["mjd"]) * 24.0
    cosd = np.cos(np.radians(o["dec"]))
    return o["ra"] + o["ra_rate"] * dt_h / 3600.0 / cosd, o["dec"] + o["dec_rate"] * dt_h / 3600.0


def field_objects(fd: FieldData, mjd: float, vmag_lim: float = 21.0, **query_kw) -> list[dict]:
    half = fd.grid.fov_arcmin / 120.0 * 1.15 + 0.02
    objs = query(fd.grid.ra, fd.grid.dec, half, mjd, vmag_lim, **query_kw)
    for o in objs:
        x, y = fd.grid.world_to_pixel(o["ra"], o["dec"])
        o["x"], o["y"] = float(x), float(y)
        o["inside"] = bool(0 <= x < fd.n and 0 <= y < fd.n)
        o["speed_arcsec_per_hr"] = float(np.hypot(o["ra_rate"], o["dec_rate"]))
        o["pa_deg"] = float(np.degrees(np.arctan2(o["ra_rate"], o["dec_rate"])) % 360.0)
    return objs


def overlay(fd: FieldData, times: list[float], vmag_lim: float = 21.0) -> dict:
    """Known objects at each time (epoch mid-times or exposure times), positions in grid pixels."""
    out = {"vmag_lim": vmag_lim, "observer": "SPHEREx (Horizons -163182)", "times": []}
    for mjd in times:
        objs = field_objects(fd, mjd, vmag_lim)
        out["times"].append({"mjd": mjd, "objects": [o for o in objs if o["inside"]]})
    return out


def _pa(vx_east: float, vy_north: float) -> float:
    return float(np.degrees(np.arctan2(vx_east, vy_north)) % 360.0)


def bracket_objects(fd: FieldData, t0: float, t1: float, vmag_lim: float = 21.0, **query_kw) -> list[dict]:
    """Known objects with SPHEREx-centric positions at t0 and t1 (two sb_ident queries), each carrying a
    `track` [[t0, ra, dec], [t1, ra, dec]] and speed / direction from that displacement. Objects seen at only
    one of the two times keep their instantaneous rates."""
    first = field_objects(fd, t0, vmag_lim, **query_kw)
    if t1 - t0 < 1e-4:
        return first
    second = {o["name"]: o for o in field_objects(fd, t1, vmag_lim, **query_kw)}
    out = []
    for o in first:
        o2 = second.pop(o["name"], None)
        if o2 is not None:
            dt_h = (t1 - t0) * 24.0
            cosd = np.cos(np.radians(0.5 * (o["dec"] + o2["dec"])))
            vx = (((o2["ra"] - o["ra"] + 180.0) % 360.0) - 180.0) * cosd * 3600.0 / dt_h
            vy = (o2["dec"] - o["dec"]) * 3600.0 / dt_h
            o = {**o, "track": [[t0, o["ra"], o["dec"]], [t1, o2["ra"], o2["dec"]]],
                 "inside": bool(o["inside"] or o2["inside"]), "mean_ra_rate": vx, "mean_dec_rate": vy,
                 "speed_arcsec_per_hr": float(np.hypot(vx, vy)), "pa_deg": _pa(vx, vy),
                 "instant_speed_arcsec_per_hr": o["speed_arcsec_per_hr"], "instant_pa_deg": o["pa_deg"]}
        out.append(o)
    out.extend(second.values())
    return out


MAX_BRACKETED = 10  # tracklets per epoch that get their own pair of queries; the rest share the epoch's pair


def identify(fd: FieldData, tracklets: list[dict], vmag_lim: float = 21.0, **query_kw) -> dict:
    """Attach `identification` to every tracklet: ranked candidates with O-C, or unknown."""
    stats = {"queries": 0, "objects_in_field": 0, "matched": 0, "error": None, "observer": "SPHEREx",
             "positions": "SPHEREx-centric, queried at each tracklet's first and last detection"}
    if not tracklets:
        return stats
    for e in sorted({t["epoch"] for t in tracklets}):
        group = [t for t in tracklets if t["epoch"] == e]
        times = [d["mjd"] for t in group for d in t["detections"]]
        shared = (min(times), max(times))
        for k, t in enumerate(group):  # one query pair at a time: JPL rate-limits bursts
            own = [d["mjd"] for d in t["detections"]]
            t0, t1 = (min(own), max(own)) if k < MAX_BRACKETED else shared
            try:
                objs = bracket_objects(fd, t0, t1, vmag_lim, **query_kw)
            except Exception as ex:  # noqa: BLE001 - JPL down or slow: the tracklet stays unidentified
                stats["error"] = f"{ex.__class__.__name__}: {ex}"[:200]
                log.warning("known-object query failed: %r", ex)
                continue
            stats["queries"] += 1 if t1 - t0 < 1e-4 else 2
            stats["objects_in_field"] = max(stats["objects_in_field"], sum(o["inside"] for o in objs))
            t["identification"] = _rank(t, objs)
            t["known"] = t["identification"][0] if t["identification"] else None
            stats["matched"] += t["known"] is not None
    return stats


def _rank(t: dict, objs: list[dict]) -> list[dict]:
    det = t["detections"]
    speed = t["rate_arcsec_per_hr"]
    pa = t["pa_deg"]
    out = []
    for o in objs:
        oc = []
        for d in det:
            ra, dec = position_at(o, d["mjd"])
            cosd = np.cos(np.radians(dec))
            oc.append(((d["ra"] - ra) * cosd * 3600.0, (d["dec"] - dec) * 3600.0))
        sep = float(np.mean([np.hypot(*v) for v in oc]))
        err = max(POS_TOL, 3.0 * float(np.hypot(o.get("err_ra") or 0.0, o.get("err_dec") or 0.0)))
        if sep > err:
            continue
        dpa = abs((o["pa_deg"] - pa + 180.0) % 360.0 - 180.0) if len(det) >= 2 else 0.0
        dspeed = abs(o["speed_arcsec_per_hr"] - speed) / max(o["speed_arcsec_per_hr"], 1e-3)
        motion_ok = len(det) < 2 or (dpa <= PA_TOL and dspeed <= SPEED_TOL)
        out.append({"name": o["name"], "kind": o["kind"], "vmag": o["vmag"], "sep_arcsec": sep,
                    "pos_tolerance_arcsec": err, "dpa_deg": dpa, "dspeed_frac": dspeed, "motion_ok": motion_ok,
                    "speed_arcsec_per_hr": o["speed_arcsec_per_hr"], "pa_deg": o["pa_deg"],
                    "o_minus_c_arcsec": [{"dra": a, "ddec": b} for a, b in oc],
                    "score": float(sep / err + dpa / PA_TOL + dspeed / SPEED_TOL)})
    out = [m for m in out if m["motion_ok"]]
    out.sort(key=lambda m: m["score"])
    return out[:3]
