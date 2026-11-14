"""Spectral analysis of a built sequence's target and of arbitrary photometry records."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field

from spherex_core import services
from spherex_core.errors import NotFound

router = APIRouter(prefix="/api")
# Results depend only on the immutable sequence and the analysis version (in the payload); a day is safe.
CACHE = {"Cache-Control": "public, max-age=86400"}


class TargetIn(BaseModel):
    ra: float = Field(ge=0, lt=360)
    dec: float = Field(ge=-90, le=90)
    pmra: float = 0.0
    pmdec: float = 0.0
    ref_epoch: float = 2000.0
    name: str | None = None


class AnalyzeRequest(BaseModel):
    photometry: list[dict] = Field(..., description="Records shaped like meta.json photometry (wl, band, "
                                                    "flux_mjy, err_mjy, epoch, flagged, relative)")
    target: TargetIn | None = Field(None, description="Needed only for the catalogue comparison")
    catalogs: bool = False
    reference: int | None = None


async def _run(fn, *args, **kw):
    try:
        return await asyncio.to_thread(fn, *args, **kw)
    except NotFound as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/sequences/{seq_id}/spectrum")
async def spectrum(seq_id: str, response: Response, include_flagged: bool = False) -> dict:
    """Binned spectrum (overall, per epoch, relative ring bins), pseudo-continuum, curated features,
    automatic detections and spectral indices of the sequence's target."""
    out = await _run(services.sequence_spectrum, seq_id, include_flagged)
    response.headers.update(CACHE)
    return out


@router.get("/sequences/{seq_id}/classify")
async def classify(seq_id: str, response: Response,
                   catalogs: bool = Query(False, description="Compare synthetic photometry with 2MASS and "
                                                             "CatWISE (adds the catalogue queries, seconds)")) -> dict:
    """Template, blackbody and IR-index classification with a summary, and synthetic J/H/Ks/W1/W2."""
    out = await _run(services.sequence_classification, seq_id, catalogs)
    response.headers.update(CACHE)
    return out


@router.get("/sequences/{seq_id}/spectral-variability")
async def spectral_variability(seq_id: str, response: Response, reference: int | None = Query(None, ge=0)) -> dict:
    """Ratio of each epoch's spectrum to a reference epoch, per bin and per detector, with significance."""
    out = await _run(services.sequence_spectral_variability, seq_id, reference)
    response.headers.update(CACHE)
    return out


@router.post("/spectrum/analyze")
async def analyze(req: AnalyzeRequest) -> dict:
    """Spectrum, classification and variability of client-supplied photometry (e.g. from
    POST /api/sequences/{id}/photometry for a clicked source)."""
    if not req.photometry:
        raise HTTPException(422, "photometry is empty")
    return await _run(services.analyze_spectrum, req.photometry,
                      req.target.model_dump() if req.target else None, req.catalogs, req.reference)


@router.get("/spectral/reference")
async def reference(response: Response) -> dict:
    """Bin grid, curated features, index definitions and the template library listing."""
    out = await _run(services.spectral_reference)
    response.headers.update(CACHE)
    return out
