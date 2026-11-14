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
    # Display hints for the web client: detector ("all" or "D1".."D6") and mode ("blink", "diff", "stack",
    # "rgb", "exp") a preset opens with.
    default_band: str | None = None
    default_mode: str | None = None
    # Solar-system objects: [[mjd, ra, dec], ...] from IRSA MOST; when set it replaces proper motion.
    ephemeris: list[list[float]] | None = None

    def position_at(self, mjd):
        """(ra, dec) in degrees at the given MJD(s): ephemeris interpolation for moving objects,
        otherwise linear proper-motion propagation."""
        if self.ephemeris:
            from ..moving.ephemeris import interpolate  # lazy: spherex_core.moving imports Target

            return interpolate(self.ephemeris, mjd)
        dt = mjd_to_year(mjd) - self.ref_epoch
        dec = self.dec + self.pmdec * dt / 3.6e6
        ra = (self.ra + self.pmra * dt / 3.6e6 / np.cos(np.radians(dec))) % 360.0
        return ra, dec

    @property
    def total_pm(self) -> float:
        return float(np.hypot(self.pmra, self.pmdec))

    def to_dict(self) -> dict:
        d = asdict(self)
        if d["ephemeris"] is None:  # keep payloads of ordinary targets unchanged
            del d["ephemeris"]
        return d
