"""Find older-survey images around a position with one TAP query on IRSA's CAOM tables.

2MASS scan images (8.5' x 17') and AllWISE Atlas tiles (1.56 deg, overlapping by a few arcmin) are taken
when they intersect the field, so wide fields near tile edges are mosaicked; NEOWISE-R frames (47') only
when they contain the centre (each visit has ~15 of them, enough to fill the field). Results are cached on
disk under <cache>/archival/tap/.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

from .. import cache
from ..archive import tap_csv
from ..config import settings
from .surveys import BANDS, band_for

TAP_CACHE_HOURS = 24 * 7


@dataclass
class ArchivalImage:
    survey: str
    band: str
    url: str  # IRSA IBE url of the science file
    mjd_start: float | None
    mjd_end: float | None

    @property
    def mjd(self) -> float | None:
        if self.mjd_start is None:
            return None
        return (self.mjd_start + (self.mjd_end if self.mjd_end is not None else self.mjd_start)) / 2.0


def _f(v: str) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def find_images(ra: float, dec: float, radius_deg: float, surveys: tuple[str, ...]) -> list[ArchivalImage]:
    """Science images of the requested surveys around (ra, dec), oldest first (blocking, cached)."""
    out: list[ArchivalImage] = []
    for survey in surveys:
        out += _query(survey, ra, dec, radius_deg)
    return sorted(out, key=lambda im: (im.mjd if im.mjd is not None else 0.0, im.survey, im.band))


def _query(survey: str, ra: float, dec: float, radius_deg: float) -> list[ArchivalImage]:
    collections = sorted({b.collection for b in BANDS if b.survey == survey})
    if not collections:
        return []
    # IRSA accepts INTERSECTS only with the column first: INTERSECTS(p.poly, CIRCLE(...)).
    geometry = (f"1 = INTERSECTS(p.poly, CIRCLE('ICRS', {ra:.7f}, {dec:.7f}, {radius_deg:.5f}))"
                if survey in ("2mass", "allwise") else f"1 = CONTAINS(POINT('ICRS', {ra:.7f}, {dec:.7f}), p.poly)")
    key = hashlib.sha1(f"v2|{survey}|{ra:.5f}|{dec:.5f}|{radius_deg:.4f}".encode()).hexdigest()[:16]
    path = cache.layout().root / "archival" / "tap" / f"{survey}_{key}.json"
    if cache.is_fresh(path, TAP_CACHE_HOURS):
        return [ArchivalImage(**d) for d in json.loads(path.read_text())]
    coll = ",".join(f"'{c}'" for c in collections)
    rows = tap_csv(f"""
        SELECT o.collection, p.energy_bandpassname AS band, p.time_bounds_lower AS t0, p.time_bounds_upper AS t1,
               a.uri
        FROM caom.artifact a JOIN caom.plane p ON a.planeid = p.planeid JOIN caom.observation o ON p.obsid = o.obsid
        WHERE {geometry} AND o.collection IN ({coll}) AND a.producttype = 'science'
    """, url=settings.tap_url, timeout=300)
    images, seen = [], set()
    for r in rows:
        uri = r["uri"].strip()
        b = band_for(r["collection"], r["band"].strip(), uri)
        if b is None or uri in seen:
            continue
        seen.add(uri)
        images.append(ArchivalImage(b.survey, b.band, uri, _f(r["t0"]), _f(r["t1"])))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps([asdict(im) for im in images]))
    tmp.replace(path)
    return images
