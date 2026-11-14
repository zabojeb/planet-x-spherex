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
        return hashlib.sha1(json.dumps(d, sort_keys=True).encode()).hexdigest()[:20]
