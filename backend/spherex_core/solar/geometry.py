"""Viewing geometry of every exposure of a co-moving sequence: heliocentric distance r (au), distance from SPHEREx
Delta (au) and phase angle (deg).

The MOST answer the sequence was built from has them per matched file (sun_dist, geo_dist, phase; MOST computes
the ephemeris for the SPHEREx observer, its positions match Horizons CENTER='500@-163182' to < 1 mas), so the
cached answer is read first. Exposures it does not cover are filled from JPL Horizons (observer table, quantities
19 r, 20 Delta, 24 S-T-O phase, centre SPHEREx), cached on disk.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import urllib.parse
import urllib.request

import numpy as np

from .. import cache
from ..errors import UpstreamError
from ..moving import most

log = logging.getLogger(__name__)
HORIZONS = "https://ssd.jpl.nasa.gov/api/horizons.api"
SPHEREX_CENTER = "500@-163182"
AU_KM = 149_597_870.7
KM_PER_ARCSEC_AU = 725.27  # km per arcsec at 1 au


def km_to_arcsec(rho_km, delta_au):
    """Angular radius (arcsec) of a projected distance rho (km) at distance Delta (au)."""
    return np.asarray(rho_km, float) / (np.asarray(delta_au, float) * KM_PER_ARCSEC_AU)


def arcsec_to_km(theta, delta_au):
    return np.asarray(theta, float) * np.asarray(delta_au, float) * KM_PER_ARCSEC_AU


def horizons_command(meta: dict) -> str:
    """Horizons COMMAND for the sequence's object: SPK id from the NAIF id, the designation for comets
    (DES=...;CAP;NOFRAG picks the latest apparition's orbit), else the name or number."""
    q = (meta.get("moving") or {}).get("query") or {}
    if q.get("obj_naifid"):
        return f"'{int(q['obj_naifid'])}'"
    name = str((meta.get("moving") or {}).get("object") or q.get("obj_name") or "").strip()
    is_comet = str(q.get("obj_type", "")).lower().startswith("comet")
    m = re.search(r"\(([^)]+)\)", name)
    if is_comet:
        des = m.group(1) if m else (q.get("obj_name") or name)
        return f"'DES={des};CAP;NOFRAG'"
    num = re.match(r"^\s*(\d+)\b", name)
    if num:
        return f"'{num.group(1)};'"
    return f"'{q.get('obj_name') or name}'"


def _most_rows(meta: dict) -> dict[str, most.MostRow]:
    q = (meta.get("moving") or {}).get("query")
    if not q:
        return {}
    path = most.cache_path(q)
    try:
        res = most.MostResult.from_dict(json.loads(path.read_text()))
    except (OSError, ValueError, KeyError):
        try:
            res = most.query_most(q.get("obj_name"), q.get("obj_type"), q.get("obs_begin"), q.get("obs_end"),
                                  int(q["obj_naifid"]) if q.get("obj_naifid") else None)
        except Exception as e:  # noqa: BLE001
            log.warning("MOST answer unavailable for the geometry: %s", e)
            return {}
    return {r.uri.rsplit("/", 1)[-1]: r for r in res.rows}


def _horizons(command: str, mjds: list[float], timeout: float = 60.0) -> dict[float, tuple[float, float, float]]:
    """{mjd: (r, Delta, phase)} from a Horizons observer table, cached per (command, mjd)."""
    path = cache.layout().root / "solar" / f"geom_{hashlib.sha1(command.encode()).hexdigest()[:12]}.json"
    try:
        store = json.loads(path.read_text())
    except (OSError, ValueError):
        store = {}
    want = [m for m in mjds if f"{m:.5f}" not in store]
    for i in range(0, len(want), 40):
        chunk = want[i:i + 40]
        q = {"format": "json", "COMMAND": command, "EPHEM_TYPE": "OBSERVER", "CENTER": f"'{SPHEREX_CENTER}'",
             "TLIST": "'" + " ".join(f"{m + 2400000.5:.6f}" for m in chunk) + "'", "TLIST_TYPE": "JD",
             "QUANTITIES": "'19,20,24'", "CSV_FORMAT": "YES", "TIME_TYPE": "UT", "OBJ_DATA": "NO"}
        try:
            with urllib.request.urlopen(HORIZONS + "?" + urllib.parse.urlencode(q), timeout=timeout) as r:
                text = json.loads(r.read().decode()).get("result", "")
        except Exception as e:  # noqa: BLE001
            raise UpstreamError(f"JPL Horizons is unavailable: {e}") from e
        rows = parse_observer_csv(text)
        if len(rows) != len(chunk):
            raise UpstreamError(f"Horizons answered {len(rows)} rows for {len(chunk)} times: {text[:200]}")
        for m, row in zip(chunk, rows):
            store[f"{m:.5f}"] = row
    if want:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(store))
        tmp.replace(path)
    return {m: tuple(store[f"{m:.5f}"]) for m in mjds}


def parse_observer_csv(text: str) -> list[list[float]]:
    """(r, Delta, phase) rows between $$SOE and $$EOE of a Horizons CSV observer table with quantities 19,20,24
    (r, rdot, delta, deldot, S-T-O, ...)."""
    if "$$SOE" not in text:
        return []
    body = text.split("$$SOE", 1)[1].split("$$EOE", 1)[0]
    out = []
    for line in body.strip().splitlines():
        cols = [c.strip() for c in line.split(",")]
        nums = []
        for c in cols[1:]:
            try:
                nums.append(float(c))
            except ValueError:
                continue
        if len(nums) >= 5:
            out.append([nums[0], nums[2], nums[4]])
    return out


def exposure_geometry(meta: dict, use_horizons: bool = True) -> list[dict]:
    """Per exposure (same order as meta['exposures']): r_au, delta_au, phase_deg and the source."""
    rows = _most_rows(meta)
    out: list[dict] = []
    missing: list[int] = []
    for i, e in enumerate(meta["exposures"]):
        r = rows.get(e["file"])
        if r is not None and r.sun_dist and r.geo_dist:
            out.append({"r_au": r.sun_dist, "delta_au": r.geo_dist, "phase_deg": r.phase, "source": "most"})
        else:
            out.append({"r_au": None, "delta_au": None, "phase_deg": None, "source": None})
            missing.append(i)
    if missing and use_horizons:
        try:
            got = _horizons(horizons_command(meta), [float(meta["exposures"][i]["mjd"]) for i in missing])
            for i in missing:
                r, d, ph = got[float(meta["exposures"][i]["mjd"])]
                out[i] = {"r_au": r, "delta_au": d, "phase_deg": ph, "source": "horizons"}
        except UpstreamError as e:
            log.warning("geometry from Horizons failed: %s", e)
    return out
