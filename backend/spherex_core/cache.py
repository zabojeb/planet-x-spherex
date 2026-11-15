"""Every on-disk cache location in one place.

Layout under settings.cache_dir. It is stable on purpose: changing a key formula invalidates the
caches warmed on the server.

    tap/<sha1[:16]>.json               IRSA TAP search results
    chunks/<k[:2]>/<sha1(url)>.npz     per Level 2 file: slim header, byte layout, the rows read so far (ADR-0037)
    cutouts/<k[:2]>/<sha1>.npz         old per-request cutouts, read once and turned into chunks (no longer written)
    wave/D<n>.npz                      per-detector wavelength lookup tables
    epsf/D<n>.npz                      per-detector effective PSF tables (441 zones)
    catalogs/<name>_<sha1[:16]>.json   catalog cone searches
    resolve/<sha1[:16]>.json           name lookups answered by CDS Sesame
    sequences/<id>/                    built sequences, format in pipeline.results
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path

from .config import settings


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()


@dataclass(frozen=True)
class CacheLayout:
    root: Path

    def tap(self, ra: float, dec: float, collections: tuple[str, ...]) -> Path:
        key = _sha1(f"{ra:.5f},{dec:.5f},{','.join(collections)}")[:16]
        return self.root / "tap" / f"{key}.json"

    def chunk(self, url: str) -> Path:
        key = _sha1(url)
        return self.root / "chunks" / key[:2] / f"{key}.npz"

    def cutout(self, url: str, ra: float, dec: float, half: int) -> Path:
        """Old per-request cutout cache: only read, to reuse what servers warmed before ADR-0037."""
        key = _sha1(f"{url}|{ra:.7f}|{dec:.7f}|{half}")
        return self.root / "cutouts" / key[:2] / f"{key}.npz"

    def wave(self, detector: int) -> Path:
        return self.root / "wave" / f"D{detector}.npz"

    def epsf(self, detector: int) -> Path:
        return self.root / "epsf" / f"D{detector}.npz"

    def catalog(self, catalog: str, ra: float, dec: float, radius_deg: float) -> Path:
        key = _sha1(f"{catalog}|{ra:.5f}|{dec:.5f}|{radius_deg:.4f}")[:16]
        return self.root / "catalogs" / f"{catalog}_{key}.json"

    def resolve(self, key: str) -> Path:
        return self.root / "resolve" / f"{_sha1(key)[:16]}.json"

    @property
    def sequences(self) -> Path:
        return self.root / "sequences"

    def sequence(self, seq_id: str) -> Path:
        return self.sequences / seq_id


_override: CacheLayout | None = None


def layout() -> CacheLayout:
    """The active layout: settings.cache_dir unless set_root() overrode it (tests, embedded use)."""
    return _override or CacheLayout(settings.cache_dir)


def set_root(root: Path | None) -> None:
    global _override
    _override = CacheLayout(Path(root)) if root is not None else None


def is_fresh(path: Path, max_age_hours: float) -> bool:
    return path.exists() and time.time() - path.stat().st_mtime < max_age_hours * 3600
