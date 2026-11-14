"""Analyses of a built sequence: photometry of any position, proper-motion fit, periodograms."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from spherex_core import services
from spherex_core.errors import NotFound, UpstreamError

router = APIRouter(prefix="/api")


class PositionRequest(BaseModel):
    ra: float = Field(ge=0, lt=360, description="Right ascension, degrees, at ref_epoch")
    dec: float = Field(ge=-90, le=90)
    pmra: float = Field(0.0, description="mas/yr, includes cos dec")
    pmdec: float = Field(0.0, description="mas/yr")
    ref_epoch: float = Field(2000.0, description="Julian year of (ra, dec)")
    name: str | None = None


class PointsRequest(BaseModel):
    photometry: list[dict] = Field(..., description="Records shaped like meta.json photometry "
                                                    "(mjd, wl, band, flux_mjy, err_mjy, flagged, relative)")
    method: Literal["standard", "multiband"] = "standard"
    band: str | None = None
    min_period: float | None = Field(None, gt=0)
    max_period: float | None = Field(None, gt=0)
    include_flagged: bool = False


def _call(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except NotFound as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    except UpstreamError as e:
        raise HTTPException(502, str(e)) from e


@router.post("/sequences/{seq_id}/photometry")
async def position_photometry(seq_id: str, req: PositionRequest) -> dict:
    """Forced photometry of any position (optionally moving) in every exposure, on the cached cutouts."""
    try:
        return await services.position_photometry(seq_id, req.ra, req.dec, req.pmra, req.pmdec, req.ref_epoch,
                                                  req.name)
    except NotFound as e:
        raise HTTPException(404, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(502, f"could not read the sequence's files: {e}") from e


@router.get("/sequences/{seq_id}/motion")
def motion(seq_id: str, x: float | None = None, y: float | None = None,
           ra: float | None = Query(None, ge=0, lt=360), dec: float | None = Query(None, ge=-90, le=90),
           bands: str | None = Query(None, description="comma-separated detectors, default all"),
           pmra: float | None = None, pmdec: float | None = None) -> dict:
    """Proper motion from per-epoch centroids near grid pixel (x, y) or sky position (ra, dec)."""
    if x is None or y is None:
        if ra is None or dec is None:
            raise HTTPException(422, "give x and y (grid pixels) or ra and dec")
        x, y = _call(services.sky_to_grid, seq_id, ra, dec)
    return _call(services.motion_fit, seq_id, x, y, bands.split(",") if bands else None, pmra, pmdec)


@router.get("/sequences/{seq_id}/periodogram")
def periodogram(seq_id: str, method: Literal["standard", "multiband"] = "standard", band: str | None = None,
                min_period: float | None = Query(None, gt=0), max_period: float | None = Query(None, gt=0),
                include_flagged: bool = False) -> dict:
    """Lomb-Scargle of the target's light curve (relative fluxes, normalised per wavelength bin)."""
    return _call(services.sequence_periodogram, seq_id, method=method, band=band, min_period=min_period,
                 max_period=max_period, include_flagged=include_flagged)


@router.post("/periodogram")
def points_periodogram(req: PointsRequest) -> dict:
    """Lomb-Scargle of photometry the client already has (e.g. from POST .../photometry)."""
    return _call(services.points_periodogram, req.photometry, method=req.method, band=req.band,
                 min_period=req.min_period, max_period=req.max_period, include_flagged=req.include_flagged)
