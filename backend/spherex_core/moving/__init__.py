"""Solar-system objects: MOST search, ephemeris positions and the co-moving sequence mode.

In co-moving mode every exposure is cut out and resampled on a grid centred on the object's predicted
position at that exposure's time, so the object stays on the central pixel and co-adds stack its light
while the stars trail (pipeline.sequence calls frames_for_object and patch_comoving_meta).
"""

from __future__ import annotations

import numpy as np

from ..archive import Frame
from ..bands import BAND_INFO
from ..config import settings
from ..targets import Target
from ..timeutil import mjd_to_iso, mjd_to_year
from .ephemeris import interpolate, normalise
from .most import MISSION_START, AmbiguousObject, MostResult, MostRow, build_query, guess_type, query_most

__all__ = ["AmbiguousObject", "MISSION_START", "MostResult", "MostRow", "build_query", "frames_for_object",
           "guess_type", "interpolate", "normalise", "object_target", "patch_comoving_meta", "query_most",
           "search_summary"]

DEFAULT_FOV = 6.0


def rows_to_frames(rows: list[MostRow], collections: tuple[str, ...] | None = None) -> list[Frame]:
    """Level 2 frames from MOST rows, one per exposure and detector, preferring earlier collections."""
    collections = tuple(collections or settings.collections)
    rank = {c: i for i, c in enumerate(collections)}
    best: dict[tuple[str, str], Frame] = {}
    for r in rows:
        coll = r.collection
        if coll not in rank:
            continue
        info = BAND_INFO.get(r.band, {})
        f = Frame(uri=r.uri, band=r.band, mjd=r.mjd, mjd_start=r.mjd_start, mjd_end=r.mjd_end, collection=coll,
                  obs_id=r.obs_id, wl_min_um=info.get("wl_min", 0.0), wl_max_um=info.get("wl_max", 0.0))
        k = (f.obs_id, f.band)
        if k not in best or rank[coll] < rank[best[k].collection]:
            best[k] = f
    return sorted(best.values(), key=lambda f: (f.mjd, f.band))


def object_target(result: MostResult) -> Target:
    """A Target that follows the MOST ephemeris; its (ra, dec, ref_epoch) is the median exposure."""
    eph = result.ephemeris()
    if not eph:
        raise LookupError(f"SPHEREx has not observed {result.object_name} in the requested dates.")
    arr = normalise(eph)
    mid = float(np.median(arr[:, 0]))
    ra, dec = interpolate(eph, mid)
    return Target(ra=float(ra), dec=float(dec), name=result.object_name, ref_epoch=float(mjd_to_year(mid)),
                  source="IRSA MOST (JPL Horizons ephemeris)", ephemeris=[list(map(float, r)) for r in eph],
                  description=f"Solar-system object seen in {len(eph)} SPHEREx exposures "
                              f"({mjd_to_iso(arr[0, 0])[:10]} to {mjd_to_iso(arr[-1, 0])[:10]}).")


def frames_for_object(name: str | None, obj_type: str | None, begin: str | None, end: str | None,
                      naif_id: int | None, collections: tuple[str, ...] | None) -> tuple[Target, list[Frame], MostResult]:
    """Blocking (network): MOST search -> target with ephemeris and the matched Level 2 frames."""
    result = query_most(name, obj_type, begin, end, naif_id)
    target = object_target(result)
    frames = rows_to_frames(result.rows, collections)
    if not frames:
        raise LookupError(f"No usable SPHEREx exposures of {result.object_name} in the selected releases.")
    return target, frames, result


def search_summary(result: MostResult) -> dict:
    """Payload of GET /api/moving/search: object, exposures and the sequence request to post."""
    rows = result.rows
    eph = result.ephemeris()
    q = result.query
    out = {"object": result.object_name, "query": q, "elements": result.elements, "n_files": len(rows),
           "n_exposures": len(eph), "date_min": None, "date_max": None, "exposures": [], "ephemeris": eph,
           "suggested_request": None}
    if not rows:
        return out
    out["date_min"], out["date_max"] = mjd_to_iso(rows[0].mjd_start), mjd_to_iso(rows[-1].mjd_start)
    out["exposures"] = [{"mjd": r.mjd, "date": mjd_to_iso(r.mjd), "ra": r.ra, "dec": r.dec, "band": r.band,
                         "obs_id": r.obs_id, "collection": r.collection, "uri": r.uri, "vmag": r.vmag,
                         "sun_dist": r.sun_dist, "geo_dist": r.geo_dist, "phase": r.phase} for r in rows]
    target = object_target(result)
    out["suggested_request"] = {
        "ra": target.ra, "dec": target.dec, "name": result.object_name, "fov_arcmin": DEFAULT_FOV,
        "object_name": q.get("obj_name"), "object_type": q.get("obj_type"),
        "naif_id": int(q["obj_naifid"]) if "obj_naifid" in q else None,
        "obs_begin": q["obs_begin"], "obs_end": q["obs_end"],
    }
    return out


def patch_comoving_meta(meta: dict, target: Target, result: MostResult) -> dict:
    """Fix the target positions assemble() computed on the median grid: in co-moving mode the object sits
    on the central pixel of every exposure and epoch. Adds per-exposure/epoch grid centres."""
    n = int(meta["grid"]["n"])
    c = (n - 1) / 2.0
    for e in meta["exposures"]:
        ra, dec = target.position_at(e["mjd"])
        e["center"] = [float(ra), float(dec)]
        e["target_px"] = [c, c]
    for ep in meta["epochs"]:
        ra, dec = target.position_at(ep["mjd_mean"])
        ep["center"] = [float(ra), float(dec)]
    for t in meta["target"]["track"]:
        t["x"] = t["y"] = c
    meta["grid"]["frame"] = "comoving"
    meta["grid"]["note"] = ("each exposure is resampled on a grid centred on the object at its own time, "
                            "so the object stays on the central pixel and the stars trail")
    meta["moving"] = {"object": result.object_name, "elements": result.elements, "query": result.query,
                      "source": "IRSA MOST, JPL Horizons ephemeris", "n_files": len(result.rows)}
    return meta
