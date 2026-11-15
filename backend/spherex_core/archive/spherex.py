"""Find SPHEREx Level 2 spectral images that cover a sky position.

Sources, best first: the local mirror of the file catalogue (archive/mirror.py; milliseconds, works while IRSA
is down), then IRSA TAP (no ingestion lag) and IRSA SIA, ordered by `upstreams` from their recent latency and
failures. A live answer is cached on disk (cache.CacheLayout.tap). New releases need no code change: they appear
in the bucket and in the spherex.* tables, and the searched collections come from settings (SPHEREX_COLLECTIONS).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass

from .. import cache, endpoints, upstreams
from ..config import settings
from . import mirror as mirror_mod
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


def dedupe(frames: list[Frame], collections: tuple[str, ...]) -> list[Frame]:
    """One frame per exposure and detector (the collection listed first wins), oldest first."""
    rank = {c: i for i, c in enumerate(collections)}
    best: dict[tuple[str, str], Frame] = {}
    for f in frames:
        k = (f.obs_id, f.band)
        if k not in best or rank.get(f.collection, 99) < rank.get(best[k].collection, 99):
            best[k] = f
    return sorted(best.values(), key=lambda f: f.mjd)


def _tap_frames(ra: float, dec: float, collections: tuple[str, ...]) -> list[Frame]:
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
    frames = []
    for r in rows:
        t0, t1 = float(r["t0"]), float(r["t1"])
        frames.append(Frame(uri=r["uri"], band=r["band"].replace("SPHEREx-", ""), mjd=(t0 + t1) / 2, mjd_start=t0,
                            mjd_end=t1, collection=r["collection"], obs_id=r["obs"],
                            wl_min_um=float(r["wl0"]) * 1e6, wl_max_um=float(r["wl1"]) * 1e6))
    return dedupe(frames, collections)


def query_frames(ra: float, dec: float, collections: tuple[str, ...] | None = None,
                 use_cache: bool = True) -> list[Frame]:
    """All Level 2 images whose footprint contains (ra, dec), one per exposure and detector, oldest first.

    When an exposure is in several collections, the one listed first in `collections` wins. use_cache=False
    skips the per-position answer cache (the mirror is still used when it is complete and fresh).
    """
    from . import sia

    collections = tuple(collections or settings.collections)
    path = cache.layout().tap(ra, dec, collections)
    if use_cache and cache.is_fresh(path, settings.tap_cache_hours):
        frames = [Frame(**d) for d in json.loads(path.read_text())]
        log.info("image search from cache frames=%d (cached for %.0f h)", len(frames), settings.tap_cache_hours)
        return frames

    m = mirror_mod.get()
    if m is not None and m.ready(collections):
        t0 = time.perf_counter()
        frames = m.query(ra, dec, collections)
        upstreams.record(endpoints.META_MIRROR, time.perf_counter() - t0, True)
        log.info("image search from mirror frames=%d s=%.3f", len(frames), time.perf_counter() - t0)
        return frames

    def ask(ep):
        if ep is endpoints.META_TAP:
            return _tap_frames(ra, dec, collections)
        return sia.query_frames(ra, dec, collections)

    try:
        frames, ep = upstreams.call("spherex-meta", ask, only=lambda e: e is not endpoints.META_MIRROR)
    except upstreams.AllFailed as e:
        if m is not None and m.usable(collections):  # stale or partial beats nothing while IRSA is down
            frames = m.query(ra, dec, collections)
            log.warning("IRSA image search failed (%s); answered from the mirror (synced %.1f h ago) frames=%d", e,
                        m.age_hours() or -1, len(frames))
            return frames
        raise RuntimeError(f"image search failed: {e}") from e
    log.info("image search via %s frames=%d", ep.name, len(frames))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{time.time_ns()}.tmp")
    tmp.write_text(json.dumps([asdict(f) for f in frames]))
    tmp.replace(path)
    return frames
