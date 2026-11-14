"""Reference catalogs for overlays behind one interface: cone(name, ra, dec, radius_deg).

Every source carries its reference epoch and proper motion, so a client can place it at the date
of each SPHEREx epoch. Results are cached on disk (cache.CacheLayout.catalog), written atomically.
"""

from __future__ import annotations

import json
import logging
import os
import time

from .. import cache
from ..errors import UpstreamError
from . import catwise, gaia, twomass

log = logging.getLogger(__name__)
_SOURCES = {"gaia": gaia, "catwise": catwise, "2mass": twomass}
CATALOGS = {name: mod.LABEL for name, mod in _SOURCES.items()}


def cone(catalog: str, ra: float, dec: float, radius_deg: float, max_age_hours: float = 24 * 7) -> list[dict]:
    """Sources within radius_deg (clamped to 0.005-0.5 deg) of (ra, dec).

    Raises KeyError for an unknown catalog and UpstreamError (a RuntimeError) for any failure of the remote
    service: errors, timeouts, or a response we cannot parse. A damaged cache file is fetched again.
    """
    if catalog not in _SOURCES:
        raise KeyError(catalog)
    radius_deg = min(max(radius_deg, 0.005), 0.5)
    path = cache.layout().catalog(catalog, ra, dec, radius_deg)
    if cache.is_fresh(path, max_age_hours):
        try:
            rows = json.loads(path.read_text())
            log.info("catalog %s from cache rows=%d radius_deg=%.3f", catalog, len(rows), radius_deg)
            return rows
        except (OSError, ValueError):
            pass  # partially written or corrupted: fetch again
    t0 = time.perf_counter()
    try:
        rows = _SOURCES[catalog].query(ra, dec, radius_deg)
        log.info("catalog %s done s=%.2f rows=%d radius_deg=%.3f", catalog, time.perf_counter() - t0, len(rows),
                 radius_deg)
    except Exception as e:  # noqa: BLE001 - every upstream failure becomes one user-facing error
        log.warning("catalog %s failed s=%.2f error=%s", catalog, time.perf_counter() - t0, e)
        reason = str(e).splitlines()[0][:200] if str(e) else e.__class__.__name__
        raise UpstreamError(f"{CATALOGS[catalog]} did not answer usefully ({reason}). Try again shortly.") from e
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(rows))
    tmp.replace(path)
    return rows


__all__ = ["CATALOGS", "cone"]
