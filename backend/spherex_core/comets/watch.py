"""Known-comet watch: which comets are bright now, which of them SPHEREx saw, and how their activity changes.

1. `comets_now`: comets from the JPL SBDB Query API with perihelion within ~2 years of now and q < 6 au (cached a
   day), propagated as two-body orbits from their osculating elements (orbits.propagate) to now: r, Delta,
   elongation, RA/Dec and the total magnitude M1 + 5 log Delta + K1 log r. Positions are good to ~1 deg for a
   list and a fly-to; the co-moving sequences use IRSA MOST / JPL Horizons ephemerides.
2. `refresh` (a background task, one at a time across workers): the watched set is the pinned designations
   (SPHEREX_COMET_WATCH, default 3I, C/2025 A6, 29P) plus the brightest predicted comets (<= MAG_LIMIT) up to
   MAX_WATCH. For each: an IRSA MOST search, a co-moving sequence on the build queue at prewarm priority and pinned
   (it never blocks interactive users and is not cancelled for lack of viewers), then the comet analysis (Afrho,
   CO2/CO bands, ADR-0060) and the comet-search object check (ADR-0063), polled like any client would.
3. `state`: per comet the latest visit's r_h, Afrho (rho = 10^4 km, D1), CO2 signal, the comet-likeness, the
   activity slope Afrho ~ r^k and outburst flags (a visit >= 2x, i.e. 0.75 mag, above the power law of the other
   visits at >= 3 sigma; with two visits, above an r^-2 scaling).

The state lives in <cache>/comets/watch.json; the API serves it with the current positions.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import numpy as np

from .. import cache, netpolicy
from . import geometry, orbits

log = logging.getLogger(__name__)
SBDB_QUERY = "https://ssd-api.jpl.nasa.gov/sbdb_query.api"
FIELDS = "full_name,pdes,prefix,e,q,tp,i,om,w,M1,K1,epoch"
SBDB_MAX_AGE_S = 24 * 3600
PINNED = tuple(s.strip() for s in os.environ.get("SPHEREX_COMET_WATCH", "3I,C/2025 A6,29P").split(",") if s.strip())
MAX_WATCH = int(os.environ.get("SPHEREX_COMET_WATCH_MAX", "8"))
MAG_LIMIT = 16.0
WATCH_FOV = 6.0
CLIENT = "comet-watch"
MOST_WAIT_S = 900.0
BUILD_WAIT_S = 2400.0
JOB_WAIT_S = 900.0
POLL_S = 10.0
RHO_KEY = "10000"
OUTBURST_RATIO = 2.0
OUTBURST_SIGMA = 3.0


def _dir():
    d = cache.layout().root / "comets"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _now_mjd() -> float:
    return time.time() / 86400.0 + 40587.0


def designation(row: dict) -> str:
    """MOST/Horizons-friendly designation: 29P, 240P-B, C/2025 A6, 3I."""
    pdes, prefix = str(row.get("pdes") or "").strip(), str(row.get("prefix") or "").strip()
    if re.fullmatch(r"\d+[PDI](-[A-Z]+)?", pdes):  # numbered periodic / interstellar, fragments included
        return pdes
    return f"{prefix}/{pdes}" if prefix else pdes


def _num(v) -> float | None:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- the comet list
def sbdb_comets(refresh: bool = False, timeout: float = 60.0) -> list[dict]:
    """Comets with perihelion within 2 years of now and q < 6 au, from the JPL SBDB Query API (cached a day)."""
    path = _dir() / "sbdb_comets.json"
    if not refresh and path.exists() and time.time() - path.stat().st_mtime < SBDB_MAX_AGE_S:
        return json.loads(path.read_text())
    jd = _now_mjd() + 2400000.5
    cdata = json.dumps({"AND": ["q|LT|6", f"tp|RG|{jd - 730:.1f}|{jd + 730:.1f}"]})
    url = SBDB_QUERY + "?" + urllib.parse.urlencode({"fields": FIELDS, "sb-kind": "c", "sb-cdata": cdata})
    try:
        netpolicy.check("jpl-sbdb")
        t, _ = netpolicy.timeouts(timeout, 1)
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "spherex-comet-watch"}),
                                    timeout=t) as r:
            data = json.loads(r.read())
        netpolicy.succeeded("jpl-sbdb")
    except Exception as e:
        netpolicy.failed("jpl-sbdb", e)
        if path.exists():  # a stale list beats none
            return json.loads(path.read_text())
        raise
    rows = [dict(zip(data["fields"], row)) for row in data.get("data") or []]
    out = []
    for r in rows:
        el = {k: _num(r.get(k)) for k in ("e", "q", "tp", "i", "om", "w", "M1", "K1")}
        if None in (el["e"], el["q"], el["tp"], el["i"], el["om"], el["w"]):
            continue
        out.append({"name": str(r["full_name"]).strip(), "designation": designation(r), **el})
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(out))
    tmp.replace(path)
    return out


def position(c: dict, mjd: float) -> dict:
    """Two-body position of a comet from SBDB elements at mjd: RA/Dec, r, Delta, elongation, magnitude."""
    jd = mjd + 2400000.5
    r_ecl, _ = orbits.propagate(c["q"], c["e"], c["i"], c["om"], c["w"], c["tp"], jd)
    r_eq = orbits.ecl_to_eq(r_ecl)
    earth, _ = geometry.earth_helio(mjd)
    d = r_eq - earth[0]
    delta = float(np.linalg.norm(d))
    rh = float(np.linalg.norm(r_eq))
    ra, dec = geometry.radec(d)
    elong = float(math.degrees(math.acos(np.clip(np.dot(-earth[0], d) / (np.linalg.norm(earth[0]) * delta), -1, 1))))
    mag = None
    if c.get("M1") is not None:
        mag = c["M1"] + 5 * math.log10(delta) + (c.get("K1") if c.get("K1") is not None else 10.0) * math.log10(rh)
    return {"ra": round(float(ra), 4), "dec": round(float(dec), 4), "r_au": round(rh, 3), "delta_au": round(delta, 3),
            "elongation_deg": round(elong, 1), "mag": round(mag, 1) if mag is not None else None,
            "days_from_perihelion": round(jd - c["tp"], 1)}


def comets_now(mjd: float | None = None, limit: int = 40) -> list[dict]:
    """Comets sorted by predicted brightness now, pinned ones included whatever their brightness."""
    mjd = _now_mjd() if mjd is None else mjd
    out = []
    for c in sbdb_comets():
        try:
            p = position(c, mjd)
        except (ValueError, ZeroDivisionError, OverflowError):
            continue
        out.append({"name": c["name"], "designation": c["designation"], "q_au": c["q"], "e": c["e"], **p,
                    "pinned": c["designation"] in PINNED or c["name"].split("/")[0] in PINNED})
    out.sort(key=lambda x: (not x["pinned"], x["mag"] if x["mag"] is not None else 99))
    return out[:limit]


def choose(now: list[dict]) -> list[dict]:
    pinned = {p: None for p in PINNED}
    for c in now:
        if c["designation"] in pinned:
            pinned[c["designation"]] = c
    chosen = [c or {"name": d, "designation": d, "pinned": True} for d, c in pinned.items()]
    for c in now:
        if len(chosen) >= MAX_WATCH:
            break
        if c["designation"] not in pinned and c.get("mag") is not None and c["mag"] <= MAG_LIMIT:
            chosen.append(c)
    return chosen[:max(MAX_WATCH, len(PINNED))]


# ---------------------------------------------------------------- activity summary and outbursts
def activity(comet_result: dict | None) -> dict:
    """Per visit r_h, Afrho (rho 1e4 km, first reflected-light detector) and CO2 from a comet analysis result."""
    if not comet_result:
        return {"visits": []}
    rows = []
    for ep in comet_result.get("epochs") or []:
        af = (ep.get("afrho") or {}).get(RHO_KEY) or {}
        b = next((af[k] for k in ("D1", "D2", "D3") if k in af), None)
        co2 = next((x for x in (ep.get("bands") or {}).get(RHO_KEY, []) if x.get("id") == "co2"), None)
        rows.append({"epoch": ep["epoch"], "mjd": ep.get("mjd_mean"), "dates": [ep.get("date_min"), ep.get("date_max")],
                     "r_au": ep.get("r_au"), "delta_au": ep.get("delta_au"), "phase_deg": ep.get("phase_deg"),
                     "afrho_cm": (b or {}).get("afrho_cm"), "afrho_err_cm": (b or {}).get("err_cm"),
                     "co2_snr": (co2 or {}).get("snr"), "co2_w_m2": (co2 or {}).get("value_w_m2"),
                     "q_co2": (co2 or {}).get("q_proxy"), "q_co2_upper": (co2 or {}).get("q_upper_3sigma")})
    return {"visits": rows, "trend": comet_result.get("activity_trend"), "outbursts": outbursts(rows)}


def outbursts(rows: list[dict]) -> list[dict]:
    """Visits whose Afrho stands >= OUTBURST_RATIO above the others' power law in r_h at >= OUTBURST_SIGMA."""
    pts = [r for r in rows if r.get("afrho_cm") and r["afrho_cm"] > 0 and r.get("r_au") and r.get("afrho_err_cm")]
    out = []
    if len(pts) < 2:
        return out
    for k, p in enumerate(pts):
        others = [q for j, q in enumerate(pts) if j != k]
        x = np.log10([q["r_au"] for q in others])
        y = np.log10([q["afrho_cm"] for q in others])
        if len(others) >= 2 and np.ptp(x) > 0.02:
            slope, icpt = np.polyfit(x, y, 1)
        else:
            slope, icpt = -2.0, float(np.mean(y + 2.0 * x))
        expect = 10 ** (icpt + slope * math.log10(p["r_au"]))
        ratio = p["afrho_cm"] / expect
        sig = (p["afrho_cm"] - expect) / p["afrho_err_cm"]
        if ratio >= OUTBURST_RATIO and sig >= OUTBURST_SIGMA:
            out.append({"epoch": p["epoch"], "mjd": p.get("mjd"), "ratio": round(float(ratio), 2),
                        "delta_mag": round(-2.5 * math.log10(ratio), 2), "sigma": round(float(sig), 1)})
    return out


# ---------------------------------------------------------------- state and refresh
_TASK: dict = {"status": "idle"}
_HANDLE: asyncio.Task | None = None


def _state_path():
    return _dir() / "watch.json"


def load_state() -> dict:
    p = _state_path()
    if p.exists():
        try:
            return json.loads(p.read_text())
        except ValueError:
            pass
    return {"comets": {}, "updated": None}


def _save(state: dict) -> None:
    from ..pipeline.results import clean_json

    p = _state_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(clean_json(state)))
    tmp.replace(p)


def task_status() -> dict:
    return dict(_TASK)


def start_refresh() -> dict:
    """Start the watch refresh in the background unless it runs (in this worker or another). Event loop only."""
    global _HANDLE
    if _HANDLE is not None and not _HANDLE.done():
        return task_status()
    from ..tasks import claim

    lock = _dir() / "watch.lock"
    if not claim(lock, max_age_s=4 * 3600):
        return {"status": "running", "stage": "running in another worker"}
    _TASK.clear()
    _TASK.update(status="running", stage="comet list", done=0, total=0, started=time.time(), error=None)

    async def run():
        from ..tasks import release

        try:
            await refresh()
            _TASK.update(status="done", stage="done", finished=time.time())
        except Exception as e:  # noqa: BLE001
            log.warning("comet watch refresh failed: %r", e)
            _TASK.update(status="error", error=str(e)[:300], finished=time.time())
        finally:
            release(lock)

    _HANDLE = asyncio.get_running_loop().create_task(run())
    return task_status()


async def refresh(names: list[str] | None = None) -> dict:
    now = await asyncio.to_thread(comets_now, None, 200)
    chosen = [c for c in now if c["designation"] in names] if names else choose(now)
    state = load_state()
    state["watched"] = [c["designation"] for c in chosen]
    for k, c in enumerate(chosen):
        _TASK.update(stage=f"following {c['designation']}", done=k, total=len(chosen))
        entry = dict(state["comets"].get(c["designation"]) or {})
        entry.update(name=c["name"], designation=c["designation"], now=c, error=None)
        try:
            entry.update(await follow(c["designation"]))
        except Exception as e:  # noqa: BLE001 - one comet's failure does not stop the others
            log.warning("comet watch: %s failed: %r", c["designation"], e)
            entry["error"] = f"{e.__class__.__name__}: {e}"[:300]
        entry["checked"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        state["comets"][c["designation"]] = entry
        state["updated"] = entry["checked"]
        await asyncio.to_thread(_save, state)
    _TASK.update(done=len(chosen), total=len(chosen))
    return state


async def _wait(poll, limit_s: float):
    t0 = time.monotonic()
    while True:
        st, payload = await poll()
        if st not in ("running", "queued") or time.monotonic() - t0 > limit_s:
            return st, payload
        await asyncio.sleep(POLL_S)


async def follow(name: str) -> dict:
    """MOST search -> co-moving sequence (prewarm, pinned) -> comet analysis + object check, for one comet."""
    from .. import services
    from ..jobs import PRIORITY_PREWARM
    from ..pipeline.params import SequenceParams

    st, res = await _wait(lambda: services.moving_search(name), MOST_WAIT_S)
    if st != "done":
        return {"stage": f"MOST search {st}", "error": (res or {}).get("error")}
    req = res.get("suggested_request")
    out = {"most": {"object": res.get("object"), "n_exposures": res.get("n_exposures"),
                    "dates": [res.get("date_min"), res.get("date_max")]}}
    if not req:
        return {**out, "stage": "not observed by SPHEREx"}
    params = SequenceParams(ra=req["ra"], dec=req["dec"], name=req.get("name"), fov_arcmin=WATCH_FOV,
                            object_name=req.get("object_name"), object_type=req.get("object_type"),
                            naif_id=req.get("naif_id"), obs_begin=req["obs_begin"], obs_end=req["obs_end"])
    seq = params.key()
    out["sequence"] = seq
    st, _ = await services.poll_sequence(seq, client=CLIENT)
    if st != "done":
        await services.submit_sequence(params, client=CLIENT, priority=PRIORITY_PREWARM, pinned=True)
        st, job = await _wait(lambda: services.poll_sequence(seq, client=CLIENT), BUILD_WAIT_S)
        if st != "done":
            return {**out, "stage": f"sequence {st}", "error": (job or {}).get("error")}

    async def job(submit):
        j = await submit
        jid = j["id"]

        async def poll():
            s, jj = await services.field_status(jid, client=CLIENT)
            return s, jj

        s, jj = await _wait(poll, JOB_WAIT_S)
        if s != "done":
            return jid, None, (jj or {}).get("error") or s
        return jid, json.loads(services.field_result_path(jid).read_text()), None

    cid, comet, cerr = await job(services.comet_submit(seq, None, CLIENT))
    hid, check, herr = await job(services.comet_search_submit(seq, {"identify": False}, CLIENT))
    act = activity(comet)
    visits = (check or {}).get("candidates", {}).get("comets") or []
    latest = max(act["visits"], key=lambda v: v.get("mjd") or 0) if act["visits"] else None
    score = visits[-1]["comet_likeness"]["score"] if visits else None
    return {**out, "stage": "done", "comet_job": cid, "search_job": hid, "errors": [e for e in (cerr, herr) if e],
            "activity": act, "latest": latest, "comet_likeness": score,
            "coma": [{"epoch": v["epoch"], "verdict": v["coma"].get("verdict"), "sigma": v["coma"].get("extent_sigma"),
                      "tail_pa": (v["coma"].get("tail") or {}).get("pa_deg") if (v["coma"].get("tail") or {}).get("detected") else None}
                     for v in visits]}


def watch(now_limit: int = 30) -> dict:
    """What the API serves: the watched comets with their latest state, the bright comets now, the refresh task."""
    state = load_state()
    try:
        now = comets_now(None, max(now_limit, 60))
        error = None
    except Exception as e:  # noqa: BLE001 - JPL unreachable and no cached list
        now, error = [], f"{e.__class__.__name__}: {e}"[:200]
    by = {c["designation"]: c for c in now}
    watched = []
    for d in state.get("watched") or list(PINNED):
        e = dict(state["comets"].get(d) or {"designation": d, "name": d, "stage": "not checked yet"})
        if d in by:
            e["now"] = by[d]
        watched.append(e)
    return {"updated": state.get("updated"), "watched": watched, "now": now[:now_limit], "refresh": task_status(),
            "pinned": list(PINNED), "max_watch": MAX_WATCH, "error": error,
            "note": "Positions are two-body propagations of JPL SBDB elements (about a degree); magnitudes are the "
                    "total M1/K1 predictions. Activity comes from co-moving SPHEREx sequences (IRSA MOST)."}
