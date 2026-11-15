"""Parameters of a sequence build and the cache key derived from them."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from ..config import settings
from ..targets import Target

# Bump when the output of the pipeline changes for the same parameters; it is part of the cache key.
# 2: ePSF photometry, NaN-aware resampling with filled co-add holes, small-epoch merging, HALO informational.
# 3: ring photometry (relative fluxes) for detectors in which the target saturates.
ALGO_VERSION = 3


@dataclass
class SequenceParams:
    ra: float
    dec: float
    pmra: float = 0.0
    pmdec: float = 0.0
    ref_epoch: float = 2000.0
    name: str | None = None
    fov_arcmin: float = 4.0
    gap_days: float = 20.0
    max_epoch_days: float = 45.0
    collections: tuple[str, ...] | None = None
    # Optional filters; when unset they do not enter the key, so existing sequence ids stay valid.
    date_min: float | None = None  # MJD, keep exposures at or after
    date_max: float | None = None  # MJD, keep exposures at or before
    bands: tuple[str, ...] | None = None  # keep only these detectors
    # Solar-system object (co-moving mode, see spherex_core.moving): frames and positions come from IRSA
    # MOST instead of a fixed-position search. Unset fields do not enter the key.
    object_name: str | None = None
    object_type: str | None = None  # "Asteroid" | "Comet"; guessed from the name when unset
    naif_id: int | None = None  # instead of object_name for names Horizons finds ambiguous
    obs_begin: str | None = None  # YYYY-MM-DD, MOST search window
    obs_end: str | None = None
    # Co-moving mode only: subtract the static sky behind the object (spherex_core.solar.static_sky) and add
    # clean co-adds/exposures to the result. Enters the key only when set.
    static_sky: bool = False

    @property
    def is_moving_object(self) -> bool:
        return bool(self.object_name) or self.naif_id is not None

    def select(self, frames: list) -> list:
        """Apply the optional date and detector filters to search results (archive.Frame)."""
        return [f for f in frames if (self.date_min is None or f.mjd >= self.date_min)
                and (self.date_max is None or f.mjd <= self.date_max)
                and (not self.bands or f.band in self.bands)]

    @property
    def target(self) -> Target:
        return Target(ra=self.ra, dec=self.dec, name=self.name, pmra=self.pmra, pmdec=self.pmdec,
                      ref_epoch=self.ref_epoch)

    def key(self) -> str:
        """Sequence id: a hash of everything that changes the result (the name does not)."""
        d = dict(ra=round(self.ra, 6), dec=round(self.dec, 6), pmra=round(self.pmra, 1),
                 pmdec=round(self.pmdec, 1), ref=round(self.ref_epoch, 3), fov=round(self.fov_arcmin, 3),
                 gap=round(self.gap_days, 3), maxd=round(self.max_epoch_days, 3),
                 coll=list(self.collections or settings.collections), v=ALGO_VERSION)
        if self.date_min is not None:
            d["dmin"] = round(self.date_min, 4)
        if self.date_max is not None:
            d["dmax"] = round(self.date_max, 4)
        if self.bands:
            d["bands"] = sorted(self.bands)
        if self.is_moving_object:
            d["obj"] = [self.object_name, self.object_type, self.naif_id, self.obs_begin, self.obs_end]
        if self.static_sky:
            from ..solar.static_sky import STATIC_SKY_VERSION

            d["ss"] = STATIC_SKY_VERSION
        return hashlib.sha1(json.dumps(d, sort_keys=True).encode()).hexdigest()[:20]
