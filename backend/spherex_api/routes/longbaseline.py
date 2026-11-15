"""Long-baseline light curves (2MASS, AllWISE, NEOWISE + SPHEREx) and the field-wide long-period search (ADR-0059).

POST starts or joins a job (deduplicated per sequence and request); GET /api/long-baseline/{job} answers 200 with
the result (immutable), 202 while it runs (every GET is a heartbeat), 500 if it failed, 410 if cancelled (POST
again); DELETE withdraws this client, and a job nobody polls for 60 s is cancelled.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from spherex_core import services
from spherex_core.errors import NotFound

from ..http import IMMUTABLE, client_id

router = APIRouter(prefix="/api")


class LongBaselineBody(BaseModel):
    ra: float | None = Field(None, ge=0, lt=360, description="Position (deg, ICRS) at ref_epoch; omit for the "
                                                             "sequence's own target")
    dec: float | None = Field(None, ge=-90, le=90)
    pmra: float | None = Field(None, description="mas/yr incl. cos dec; omitted = a Gaia counterpart's motion, else 0")
    pmdec: float | None = None
    ref_epoch: float = Field(2000.0, description="Julian year of (ra, dec)")
    name: str | None = None
    calibrate: bool = Field(True, description="Cross-calibrate with CatWISE / 2MASS field stars")
    min_period: float | None = Field(None, gt=0, description="days, default 50")
    max_period: float | None = Field(None, gt=0, description="days, default baseline / 1.5")
    field_radius: float = Field(150.0, ge=60, le=360, description="arcsec: frame windows and calibration stars "
                                                                  "around the target (larger: more stars, slower)")
    force: bool = False


class SearchBody(BaseModel):
    w1_max: float = Field(15.0, ge=6, le=17, description="Faintest CatWISE W1 to measure")
    max_sources: int = Field(150, ge=1, le=400)
    min_points: int = Field(8, ge=4, le=100)
    min_period: float = Field(50.0, gt=0)
    max_period: float | None = Field(None, gt=0)
    radius_arcsec: float = Field(150.0, ge=30, le=360, description="Search radius around the field centre")
    force: bool = False


def _errors(e: Exception) -> HTTPException:
    if isinstance(e, NotFound):
        return HTTPException(404, str(e))
    return HTTPException(422, str(e))


@router.post("/sequences/{seq_id}/long-baseline")
async def long_baseline(seq_id: str, body: LongBaselineBody, request: Request) -> dict:
    """Start or join the multi-decade light curve of the target (or of any position inside the field)."""
    if (body.ra is None) != (body.dec is None):
        raise HTTPException(422, "give both ra and dec, or neither for the sequence target")
    try:
        return await services.long_baseline_submit(seq_id, body.ra, body.dec, body.pmra, body.pmdec, body.ref_epoch,
                                                   body.name, body.calibrate, body.min_period, body.max_period,
                                                   client_id(request), body.force, body.field_radius)
    except (NotFound, ValueError) as e:
        raise _errors(e) from e


@router.post("/sequences/{seq_id}/long-period-search")
async def long_period_search(seq_id: str, body: SearchBody, request: Request) -> dict:
    """Start or join the field-wide ranking of sources by long-baseline variability."""
    opts = body.model_dump(exclude={"force"})
    try:
        return await services.long_period_search_submit(seq_id, opts, client_id(request), body.force)
    except (NotFound, ValueError, TypeError) as e:
        raise _errors(e) from e


def _check(job_id: str) -> None:
    if job_id.partition("-")[2][:1] not in ("l", "w"):
        raise HTTPException(404, "not a long-baseline job")


@router.get("/long-baseline/{job_id}")
async def long_baseline_result(job_id: str, request: Request) -> Response:
    _check(job_id)
    try:
        status, job = await services.field_status(job_id, client_id(request))
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    if status == "done":
        return FileResponse(services.field_result_path(job_id), media_type="application/json",
                            headers={"Cache-Control": IMMUTABLE, "ETag": f'"{job_id}"'})
    if job is None:
        raise HTTPException(404, "unknown long-baseline job")
    code = {"error": 500, "cancelled": 410}.get(status, 202)
    return Response(json.dumps(job), media_type="application/json", status_code=code,
                    headers={"Cache-Control": "no-store"})


@router.get("/long-baseline/{job_id}/lightcurve.csv")
def long_baseline_csv(job_id: str) -> Response:
    """The finished light curve as CSV (one row per point)."""
    _check(job_id)
    try:
        text = services.long_baseline_csv(job_id)
    except (NotFound, ValueError) as e:
        raise HTTPException(404, str(e)) from e
    return Response(text, media_type="text/csv", headers={
        "Cache-Control": IMMUTABLE, "Content-Disposition": f'attachment; filename="{job_id}-lightcurve.csv"'})


@router.delete("/long-baseline/{job_id}")
async def long_baseline_withdraw(job_id: str, request: Request) -> Response:
    _check(job_id)
    job = await services.field_withdraw(job_id, client_id(request))
    return Response(json.dumps(job), media_type="application/json") if job else Response(status_code=204)
