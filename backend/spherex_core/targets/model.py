"""Target model: a position at a reference epoch plus proper motion."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from ..timeutil import mjd_to_year


@dataclass
class Target:
    ra: float  # deg (ICRS) at ref_epoch
    dec: float
    name: str | None = None
    pmra: float = 0.0  # mas/yr, includes cos(dec)
    pmdec: float = 0.0  # mas/yr
    ref_epoch: float = 2000.0  # Julian year of (ra, dec)
    parallax: float | None = None  # mas, informational only
    source: str = "user"
    description: str | None = None
    id: str | None = None
    fov_arcmin: float | None = None  # suggested field of view for presets

    def position_at(self, mjd):
        """(ra, dec) in degrees at the given MJD(s); linear proper-motion propagation."""
        dt = mjd_to_year(mjd) - self.ref_epoch
        dec = self.dec + self.pmdec * dt / 3.6e6
        ra = (self.ra + self.pmra * dt / 3.6e6 / np.cos(np.radians(dec))) % 360.0
        return ra, dec

    @property
    def total_pm(self) -> float:
        return float(np.hypot(self.pmra, self.pmdec))

    def to_dict(self) -> dict:
        return asdict(self)
