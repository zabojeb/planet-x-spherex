"""Request models of the HTTP API (pydantic)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from spherex_core.pipeline.params import SequenceParams


class SequenceRequest(BaseModel):
    ra: float = Field(ge=0, lt=360, description="Right ascension, degrees (ICRS) at ref_epoch")
    dec: float = Field(ge=-90, le=90, description="Declination, degrees")
    pmra: float = Field(0.0, description="Proper motion in RA (includes cos dec), mas/yr")
    pmdec: float = Field(0.0, description="Proper motion in Dec, mas/yr")
    ref_epoch: float = Field(2000.0, description="Julian year of the given position")
    name: str | None = None
    fov_arcmin: float = Field(6.0, ge=1.0, le=30.0, description="Field of view, arcmin")
    gap_days: float = Field(20.0, gt=0, le=365, description="Start a new epoch after a gap this long")
    max_epoch_days: float = Field(45.0, gt=0, le=400, description="Maximum length of one epoch")
    force: bool = False

    def params(self) -> SequenceParams:
        return SequenceParams(ra=self.ra, dec=self.dec, pmra=self.pmra, pmdec=self.pmdec, ref_epoch=self.ref_epoch,
                              name=self.name, fov_arcmin=self.fov_arcmin, gap_days=self.gap_days,
                              max_epoch_days=self.max_epoch_days)
