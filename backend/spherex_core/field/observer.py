"""SPHEREx's own position: geocentric state vectors from JPL Horizons.

SPHEREx orbits ~7,000 km from the geocentre, so a solar-system object at distance D AU appears up to
~9.7"/D away from its geocentric position (docs/research/astrometrica.md). Known-object positions and
ADES reports therefore use the spacecraft state at each time: Horizons `COMMAND='-163182'` (SPHEREx),
`CENTER='500@399'`, ICRF equatorial frame (`REF_PLANE=FRAME`), km and km/s. States are cached on disk by
time (rounded to 1e-5 d, ~1 s).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request

import numpy as np

from .. import cache, netpolicy
from ..logs import timed

HORIZONS = "https://ssd.jpl.nasa.gov/api/horizons.api"
SPHEREX_ID = "-163182"
BATCH = 40  # times per Horizons request
log = logging.getLogger(__name__)


def _cache_file():
    return cache.layout().root / "horizons" / "spherex_states.json"


def _load() -> dict[str, list[float]]:
    try:
        return json.loads(_cache_file().read_text())
    except (OSError, ValueError):
        return {}


def _save(states: dict[str, list[float]]) -> None:
    path = _cache_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.part")
    tmp.write_text(json.dumps(states))
    tmp.replace(path)


def _key(mjd: float) -> str:
    return f"{float(mjd):.5f}"


def _query(mjds: list[float], timeout: float = 60.0) -> dict[str, list[float]]:
    jds = " ".join(f"{m + 2400000.5:.6f}" for m in mjds)
    q = {"format": "json", "COMMAND": f"'{SPHEREX_ID}'", "EPHEM_TYPE": "VECTORS", "CENTER": "'500@399'",
         "TLIST": f"'{jds}'", "TLIST_TYPE": "JD", "OUT_UNITS": "KM-S", "VEC_TABLE": "2", "REF_PLANE": "FRAME",
         "CSV_FORMAT": "YES", "TIME_TYPE": "UT"}
    url = HORIZONS + "?" + urllib.parse.urlencode(q)
    service = urllib.parse.urlsplit(HORIZONS).netloc
    netpolicy.check(service)
    timeout, attempts = netpolicy.timeouts(timeout, 4)
    for attempt in range(attempts):
        try:
            with timed(log, "horizons spacecraft states", n=len(mjds), attempt=attempt):
                with urllib.request.urlopen(url, timeout=timeout) as r:
                    text = json.loads(r.read())["result"]
            netpolicy.succeeded(service)
            break
        except (OSError, ValueError, KeyError) as e:  # 503s during JPL load are common: back off and retry
            if attempt == attempts - 1:
                netpolicy.failed(service, e)
                raise
            time.sleep(netpolicy.pause(2.0 * 2 ** attempt))
    if "$$SOE" not in text:
        raise RuntimeError("Horizons returned no vectors: " + text[:200])
    body = text.split("$$SOE")[1].split("$$EOE")[0]
    out = {}
    for line in body.strip().splitlines():
        parts = [p.strip() for p in line.split(",") if p.strip()]
        if len(parts) < 8:
            continue
        jd = float(parts[0])
        out[_key(jd - 2400000.5)] = [float(v) for v in parts[2:8]]
    return out


def states(mjds) -> dict[str, list[float]]:
    """Geocentric ICRF state [x, y, z, vx, vy, vz] (km, km/s) for every MJD; keys are _key(mjd).
    Raises RuntimeError when Horizons fails."""
    want = sorted({_key(m) for m in np.atleast_1d(mjds)})
    have = _load()
    missing = [float(k) for k in want if k not in have]
    if missing:
        for i in range(0, len(missing), BATCH):
            have.update(_query(missing[i:i + BATCH]))
        _save(have)
    return {k: have[k] for k in want if k in have}


def state(mjd: float) -> list[float]:
    s = states([mjd]).get(_key(mjd))
    if s is None:
        raise RuntimeError(f"no SPHEREx state for MJD {mjd}")
    return s
