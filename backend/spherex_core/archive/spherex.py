"""Find SPHEREx Level 2 spectral images that cover a sky position (IRSA TAP, no ingestion lag).

New releases need no code change: they appear in the spherex.* tables, and the searched
collections come from settings (SPHEREX_COLLECTIONS).
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass

from .. import cache
from ..config import settings
from .tap import tap_csv

log = logging.getLogger(__name__)


@dataclass
class Frame:
    """One Level 2 file: a single exposure of a single detector."""

    uri: str  # IRSA path, e.g. ibe/data/spherex/dr1/level2/.../level2_..._1D6_spx_....fits
    band: str  # D1..D6
    mjd: float  # mid-exposure
    mjd_start: float
    mjd_end: float
    collection: str
    obs_id: str
    wl_min_um: float
    wl_max_um: float

    @property
    def s3_url(self) -> str:
        return settings.s3_base + self.uri.removeprefix("ibe/data/spherex/")

    @property
    def irsa_url(self) -> str:
        return settings.irsa_base + self.uri

    @property
    def detector(self) -> int:
        return int(self.band[1])

    @property
    def file_name(self) -> str:
        return self.uri.rsplit("/", 1)[-1]


log = logging.getLogger(__name__)

def query_frames(ra: float, dec: float, collections: tuple[str, ...] | None = None,
                 use_cache: bool = True) -> list[Frame]:
    """All Level 2 images whose footprint contains (ra, dec), one per exposure and detector, oldest first.

    When an exposure is in several collections, the one listed first in `collections` wins.
    """
    collections = tuple(collections or settings.collections)
    path = cache.layout().tap(ra, dec, collections)
    if use_cache and cache.is_fresh(path, settings.tap_cache_hours):
        frames = [Frame(**d) for d in json.loads(path.read_text())]
        log.info("image search from cache frames=%d (cached for %.0f h)", len(frames), settings.tap_cache_hours)
        return frames

    coll = ",".join(f"'{c}'" for c in collections)
    rows = tap_csv(f"""
        SELECT a.uri, p.energy_bandpassname AS band, p.time_bounds_lower AS t0, p.time_bounds_upper AS t1,
               p.energy_bounds_lower AS wl0, p.energy_bounds_upper AS wl1, o.collection, o.observationid AS obs
        FROM spherex.artifact a
        JOIN spherex.plane p ON a.planeid = p.planeid
        JOIN spherex.observation o ON p.obsid = o.obsid
        WHERE 1 = CONTAINS(POINT('ICRS', {ra:.7f}, {dec:.7f}), p.poly)
          AND o.collection IN ({coll}) AND a.producttype = 'science'
    """)
    rank = {c: i for i, c in enumerate(collections)}
    best: dict[tuple[str, str], Frame] = {}
    for r in rows:
        band = r["band"].replace("SPHEREx-", "")
        t0, t1 = float(r["t0"]), float(r["t1"])
        f = Frame(uri=r["uri"], band=band, mjd=(t0 + t1) / 2, mjd_start=t0, mjd_end=t1,
                  collection=r["collection"], obs_id=r["obs"],
                  wl_min_um=float(r["wl0"]) * 1e6, wl_max_um=float(r["wl1"]) * 1e6)
        k = (f.obs_id, band)
        if k not in best or rank.get(f.collection, 99) < rank.get(best[k].collection, 99):
            best[k] = f
    frames = sorted(best.values(), key=lambda f: f.mjd)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(f) for f in frames]))
    return frames
