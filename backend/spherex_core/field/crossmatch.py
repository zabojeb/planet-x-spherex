"""Cross-match field sources with Gaia DR3, CatWISE2020 and 2MASS.

Catalog positions are moved with their proper motions to the mean epoch of the sequence (2MASS has no
motions and stays at its own epoch, so fast movers will not match it, which is itself informative), then
each source takes the nearest counterpart within MATCH_RADIUS.
"""

from __future__ import annotations

import numpy as np
from scipy.spatial import cKDTree

from .. import catalogs
from ..errors import UpstreamError
from .common import FieldData

MATCH_RADIUS = 1.5  # grid pixels (~9")
CATALOG_NAMES = ("gaia", "catwise", "2mass")


def _propagate(rows: list[dict], year: float) -> tuple[np.ndarray, np.ndarray]:
    ra = np.array([r["ra"] for r in rows], float)
    dec = np.array([r["dec"] for r in rows], float)
    pmra = np.array([r.get("pmra") or 0.0 for r in rows], float)
    pmdec = np.array([r.get("pmdec") or 0.0 for r in rows], float)
    dt = year - np.array([r.get("epoch") or year for r in rows], float)
    dec_t = dec + pmdec * dt / 3.6e6
    ra_t = ra + pmra * dt / 3.6e6 / np.cos(np.radians(dec_t))
    return ra_t, dec_t


def crossmatch(fd: FieldData, sources: list[dict], names: tuple[str, ...] = CATALOG_NAMES) -> dict:
    """Adds `matches: {catalog: {...} | None}` to every source. Returns per-catalog stats (count or error)."""
    radius = min(0.5, fd.grid.fov_arcmin / 60.0 * 0.75)
    stats: dict[str, dict] = {}
    for s in sources:
        s["matches"] = {}
    if not sources:
        return stats
    tree = cKDTree(np.column_stack([[s["x"] for s in sources], [s["y"] for s in sources]]))
    for name in names:
        try:
            rows = catalogs.cone(name, fd.grid.ra, fd.grid.dec, radius)
        except UpstreamError as e:
            stats[name] = {"error": str(e)}
            continue
        rows = [r for r in rows if r.get("ra") is not None and r.get("dec") is not None]
        stats[name] = {"rows": len(rows)}
        if not rows:
            continue
        ra_t, dec_t = _propagate(rows, fd.t_ref)
        x, y = fd.grid.world_to_pixel(ra_t, dec_t)
        inside = np.isfinite(x) & np.isfinite(y)
        best: dict[int, tuple[float, int]] = {}
        for j in np.nonzero(inside)[0]:
            d, i = tree.query([x[j], y[j]], distance_upper_bound=MATCH_RADIUS)
            if np.isfinite(d) and (i not in best or d < best[i][0]):
                best[int(i)] = (float(d), int(j))
        matched = 0
        for i, s in enumerate(sources):
            if i in best:
                d, j = best[i]
                r = rows[j]
                s["matches"][name] = {"id": r.get("id"), "mag": r.get("mag"), "mag_band": r.get("mag_band"),
                                      "pmra": r.get("pmra"), "pmdec": r.get("pmdec"),
                                      "sep_arcsec": d * fd.grid.pixscale}
                matched += 1
            else:
                s["matches"][name] = None
        stats[name]["matched"] = matched
    return stats


def catalog_motion(source: dict) -> dict | None:
    """The best catalog proper motion of a matched source (Gaia first, then CatWISE)."""
    for name in ("gaia", "catwise"):
        m = (source.get("matches") or {}).get(name)
        if m and m.get("pmra") is not None and m.get("pmdec") is not None:
            return {"catalog": name, "id": m["id"], "pmra": m["pmra"], "pmdec": m["pmdec"]}
    return None
