"""Request models of the HTTP API (pydantic)."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

from spherex_core.pipeline.params import SequenceParams


def _mjd(date: str | None, end_of_day: bool = False) -> float | None:
    """'YYYY-MM-DD' (or an ISO timestamp) -> MJD; a bare date ends at 24:00 when end_of_day."""
    if not date:
        return None
    from datetime import datetime

    d = datetime.fromisoformat(date.replace("Z", "+00:00")).replace(tzinfo=None)
    mjd = (d - datetime(1858, 11, 17)).total_seconds() / 86400.0
    return mjd + 1.0 if end_of_day and len(date) <= 10 else mjd


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
    date_min: str | None = Field(None, description="Only exposures from this UTC date on (YYYY-MM-DD)")
    date_max: str | None = Field(None, description="Only exposures up to this UTC date (YYYY-MM-DD, inclusive)")
    bands: list[str] | None = Field(None, description="Only these detectors, e.g. [\"D5\", \"D6\"]")
    # Solar-system object (co-moving sequence). Copy these from GET /api/moving/search -> suggested_request.
    object_name: str | None = Field(None, max_length=120, description="Asteroid or comet name for IRSA MOST")
    object_type: str | None = Field(None, pattern=r"^(Asteroid|Comet)$", description="Asteroid or Comet")
    naif_id: int | None = Field(None, description="JPL NAIF ID instead of object_name")
    obs_begin: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="MOST window start")
    obs_end: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="MOST window end")
    static_sky: bool = Field(False, description="Co-moving only: also subtract the static sky (clean arrays)")
    force: bool = False

    @field_validator("bands")
    @classmethod
    def _known_bands(cls, v: list[str] | None) -> list[str] | None:
        from spherex_core.bands import BANDS

        if v is not None and (not v or any(b not in BANDS for b in v)):
            raise ValueError(f"bands must be a non-empty subset of {list(BANDS)}")
        return v

    @model_validator(mode="after")
    def _static_sky_needs_object(self) -> "SequenceRequest":
        if self.static_sky and not (self.object_name or self.naif_id is not None):
            raise ValueError("static_sky needs a solar-system object (object_name or naif_id)")
        return self

    @field_validator("date_min", "date_max")
    @classmethod
    def _iso_date(cls, v: str | None) -> str | None:
        if v is not None:
            _mjd(v)  # raises ValueError for a bad date
        return v

    def params(self) -> SequenceParams:
        return SequenceParams(ra=self.ra, dec=self.dec, pmra=self.pmra, pmdec=self.pmdec, ref_epoch=self.ref_epoch,
                              name=self.name, fov_arcmin=self.fov_arcmin, gap_days=self.gap_days,
                              max_epoch_days=self.max_epoch_days, date_min=_mjd(self.date_min),
                              date_max=_mjd(self.date_max, end_of_day=True),
                              bands=tuple(sorted(set(self.bands))) if self.bands else None,
                              object_name=self.object_name or None, object_type=self.object_type,
                              naif_id=self.naif_id, obs_begin=self.obs_begin, obs_end=self.obs_end,
                              static_sky=self.static_sky)
