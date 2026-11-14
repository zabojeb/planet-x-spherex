"""Reference catalogs for overlays behind one interface: cone(name, ra, dec, radius_deg).

Every source carries its reference epoch and proper motion, so a client can place it at the date
of each SPHEREx epoch. Results are cached on disk (cache.CacheLayout.catalog).
"""

from __future__ import annotations

import json

from .. import cache
from . import catwise, gaia, twomass

_SOURCES = {"gaia": gaia, "catwise": catwise, "2mass": twomass}
CATALOGS = {name: mod.LABEL for name, mod in _SOURCES.items()}


def cone(catalog: str, ra: float, dec: float, radius_deg: float, max_age_hours: float = 24 * 7) -> list[dict]:
    """Sources within radius_deg (clamped to 0.005-0.5 deg) of (ra, dec). Raises KeyError for an unknown
    catalog and RuntimeError when the remote service fails."""
    if catalog not in _SOURCES:
        raise KeyError(catalog)
    radius_deg = min(max(radius_deg, 0.005), 0.5)
    path = cache.layout().catalog(catalog, ra, dec, radius_deg)
    if cache.is_fresh(path, max_age_hours):
        return json.loads(path.read_text())
    rows = _SOURCES[catalog].query(ra, dec, radius_deg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows))
    return rows


__all__ = ["CATALOGS", "cone"]
