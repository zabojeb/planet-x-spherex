"""Known minor planets and comets seen from SPHEREx, and identification of tracklets against them.

Positions come from the JPL Small-Body Identification API with SPHEREx's own geocentric state (`xobs`, from
Horizons) and numerical propagation (`two-pass=true`), so they include SPHEREx's parallax (up to ~9.7"/D for
an object D AU away). Results are cached per (field, time, V limit).

When JPL (sb_ident, or Horizons for SPHEREx's state) fails, positions come from IMCCE SkyBoT instead. SkyBoT
cannot place the observer on SPHEREx (MPC code 288 also answers geocentric), so those objects carry
`geocentric: true`, a position error widened by the parallax (9.7"/D) and a motion tolerance widened by the
spacecraft's orbital velocity (37"/h / D); they are cached apart so the next query asks JPL again.

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

from .. import cache, endpoints, netpolicy, upstreams
from ..logs import timed
from . import observer
from .common import FieldData

SB_IDENT = "https://ssd-api.jpl.nasa.gov/sb_ident.api"
SKYBOT = "https://ssp.imcce.fr/webservices/skybot/api/conesearch.php"
SKYBOT_FALLBACK = os.environ.get("SPHEREX_SKYBOT_FALLBACK", "1") != "0"
PARALLAX_ARCSEC_AU = 9.7  # SPHEREx's ~7,000 km offset from the geocentre, seen from 1 AU
ORBIT_RATE_ARCSEC_H_AU = 37.2  # its ~7.5 km/s orbital velocity, as an angular rate seen from 1 AU
GEOCENTRIC_NOTE = 'geocentre (IMCCE SkyBoT; JPL unavailable): off by up to 9.7"/D from SPHEREx'
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


def _write(path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.part")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)


def query(ra: float, dec: float, half_deg: float, mjd: float, vmag_lim: float = 21.0,
          timeout: float = 170.0, retries: int = 5) -> list[dict]:
    """Asteroids and comets in a box around (ra, dec) at mjd as seen from SPHEREx (cached on disk); geocentric
    from SkyBoT, flagged, when JPL fails (see the module notes)."""
    key = hashlib.sha1(f"{ra:.4f}|{dec:.4f}|{half_deg:.3f}|{mjd:.4f}|{vmag_lim}".encode()).hexdigest()[:16]
    path = cache.layout().root / "known_objects" / f"{key}.json"
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        pass
    t0 = time.perf_counter()
    try:
        out = _sb_ident(ra, dec, half_deg, mjd, vmag_lim, timeout, retries)
    except Exception as e:  # noqa: BLE001 - JPL down, slow or rate-limiting: SkyBoT, if allowed
        upstreams.record(endpoints.SSO_JPL, time.perf_counter() - t0, False, e, breaker=False)
        if not SKYBOT_FALLBACK:
            raise
        geo = path.with_name(f"{key}.skybot.json")
        try:
            return json.loads(geo.read_text())
        except (OSError, ValueError):
            pass
        log.warning("known objects: JPL failed (%s); using SkyBoT geocentric positions", e)
        try:
            out = skybot(ra, dec, half_deg, mjd, vmag_lim, timeout=min(timeout, 60.0))
        except Exception as e2:  # noqa: BLE001
            raise RuntimeError(f"JPL sb_ident failed ({e}); SkyBoT failed ({e2})") from e2
        _write(geo, out)
        return out
    upstreams.record(endpoints.SSO_JPL, time.perf_counter() - t0, True, breaker=False)
    _write(path, out)
    return out


def _sb_ident(ra: float, dec: float, half_deg: float, mjd: float, vmag_lim: float, timeout: float,
              retries: int) -> list[dict]:
    xobs = ",".join(f"{v:.9E}" for v in observer.state(mjd))
    base = {"obs-time": mjd_to_datetime(mjd).strftime("%Y-%m-%d_%H:%M:%S"), "xobs": xobs,
            "fov-ra-center": _sexa_ra(ra), "fov-dec-center": _sexa_dec(dec),
            "fov-ra-hwidth": f"{half_deg:.4f}", "fov-dec-hwidth": f"{half_deg:.4f}", "two-pass": "true",
            "suppress-first-pass": "true", "vmag-lim": f"{vmag_lim:g}"}

    service = urllib.parse.urlsplit(SB_IDENT).netloc
    netpolicy.check(service)
    timeout, retries = netpolicy.timeouts(timeout, retries)

    def fetch(kind: str) -> tuple[str, dict]:
        url = SB_IDENT + "?" + urllib.parse.urlencode({**base, "sb-kind": kind}, safe="-_:,")
        for attempt in range(retries):
            try:
                with urllib.request.urlopen(url, timeout=timeout) as r:
                    out = kind, json.loads(r.read())
                netpolicy.succeeded(service)
                return out
            except urllib.error.HTTPError as e:  # JPL rate-limits (429) and sheds load (503): back off
                if e.code not in (429, 503) or attempt == retries - 1:
                    netpolicy.failed(service, e)
                    raise
                wait = float(e.headers.get("Retry-After") or 0) or 5.0 * 2 ** attempt
                time.sleep(netpolicy.pause(min(wait, 60.0)))
            except Exception as e:  # noqa: BLE001 - timeouts and connection errors open the breaker
                netpolicy.failed(service, e)
                raise
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
    return out


def skybot(ra: float, dec: float, half_deg: float, mjd: float, vmag_lim: float = 21.0,
           timeout: float = 60.0) -> list[dict]:
    """Known objects in the circle around the box from IMCCE SkyBoT, geocentric (flagged)."""
    q = {"-ep": f"{mjd + 2400000.5:.6f}", "-ra": f"{ra % 360.0:.6f}", "-dec": f"{dec:.6f}",
         "-rd": f"{half_deg * 1.4143:.4f}", "-mime": "json", "-output": "all", "-loc": "500", "-filter": "0",
         "-objFilter": "111", "-refsys": "EQJ2000", "-from": "spherex-viewer"}
    timeout, _ = netpolicy.timeouts(timeout, 1)
    netpolicy.check(endpoints.SSO_SKYBOT.service)
    t0 = time.perf_counter()
    try:
        with timed(log, "skybot", mjd=round(mjd, 4)), \
                urllib.request.urlopen(SKYBOT + "?" + urllib.parse.urlencode(q), timeout=timeout) as r:
            body = r.read()  # 204 (empty) when nothing is in the cone
    except Exception as e:
        upstreams.record(endpoints.SSO_SKYBOT, time.perf_counter() - t0, False, e)
        raise
    upstreams.record(endpoints.SSO_SKYBOT, time.perf_counter() - t0, True)
    return parse_skybot(json.loads(body) if body.strip() else [], mjd, vmag_lim)


def parse_skybot(rows: list[dict], mjd: float, vmag_lim: float) -> list[dict]:
    """SkyBoT conesearch JSON rows (-output=all) -> the objects `query` returns, flagged geocentric."""
    out = []
    for rec in rows:
        try:
            num, name = rec.get("Num"), str(rec["Name"])
            h, m, sec = (float(v) for v in str(rec["RA (hms)"]).split())
            dd, dm, ds = str(rec["DEC (dms)"]).split()
            sign = -1.0 if dd.startswith("-") else 1.0
            dec = sign * (abs(float(dd)) + float(dm) / 60 + float(ds) / 3600)
        except (KeyError, ValueError, TypeError):
            continue
        vmag = _num(rec.get("VMag (mag)"))
        if vmag is not None and vmag > vmag_lim:
            continue
        dist = max(_num(rec.get("dg (ua)")) or 1.0, 0.01)
        err = (_num(rec.get("Err (arcsec)")) or 0.0) + PARALLAX_ARCSEC_AU / dist
        cls = str(rec.get("Class", ""))
        out.append({"name": f"{num} {name}" if num not in (None, "", "-") else name,
                    "kind": "comet" if "comet" in cls.lower() else "asteroid", "class": cls, "mjd": mjd,
                    "ra": 15.0 * (h + m / 60 + sec / 3600), "dec": dec, "vmag": vmag,
                    "ra_rate": _num(rec.get("dRA (arcsec/h)")) or 0.0,  # includes cos(dec), like sb_ident
                    "dec_rate": _num(rec.get("dDEC (arcsec/h)")) or 0.0,
                    "err_ra": err / 2 ** 0.5, "err_dec": err / 2 ** 0.5, "distance_au": dist,
                    "geocentric": True})
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
        if any(o.get("geocentric") for o in objs):
            out["observer"] = GEOCENTRIC_NOTE
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
            if any(o.get("geocentric") for o in objs):
                stats["observer"], stats["positions"] = "geocentre", GEOCENTRIC_NOTE
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
        v = max(o["speed_arcsec_per_hr"], 1e-3)
        dspeed = abs(o["speed_arcsec_per_hr"] - speed) / v
        speed_tol, pa_tol = SPEED_TOL, PA_TOL
        if o.get("geocentric"):  # SPHEREx's orbital motion is not in a geocentric rate
            wobble = ORBIT_RATE_ARCSEC_H_AU / max(o.get("distance_au") or 1.0, 0.05)
            speed_tol, pa_tol = SPEED_TOL + wobble / v, PA_TOL + float(np.degrees(np.arctan2(wobble, v)))
        motion_ok = len(det) < 2 or (dpa <= pa_tol and dspeed <= speed_tol)
        out.append({"name": o["name"], "kind": o["kind"], "vmag": o["vmag"], "sep_arcsec": sep,
                    "pos_tolerance_arcsec": err, "dpa_deg": dpa, "dspeed_frac": dspeed, "motion_ok": motion_ok,
                    "speed_arcsec_per_hr": o["speed_arcsec_per_hr"], "pa_deg": o["pa_deg"],
                    "o_minus_c_arcsec": [{"dra": a, "ddec": b} for a, b in oc],
                    "geocentric": bool(o.get("geocentric")),
                    "score": float(sep / err + dpa / pa_tol + dspeed / speed_tol)})
    out = [m for m in out if m["motion_ok"]]
    out.sort(key=lambda m: m["score"])
    return out[:3]
