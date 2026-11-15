"""'Click a point -> Analyze': every automatic analysis of one position as one job.

POST /api/sequences/{id}/analyze starts or joins the job (deduplicated per sequence and position rounded to
~0.4"); GET /api/analyses/{job} answers 200 with the result (immutable), 202 while it runs (every GET is a
heartbeat), 500 if it failed, 410 if cancelled (POST again); DELETE withdraws this client and a job nobody
polls for 60 s is cancelled.
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


class AnalyzeRequest(BaseModel):
    ra: float = Field(..., ge=0, lt=360, description="Right ascension, deg (ICRS), at ref_epoch if a motion is given")
    dec: float = Field(..., ge=-90, le=90)
    pmra: float | None = Field(None, description="mas/yr incl. cos dec; omitted = use a matching Gaia motion")
    pmdec: float | None = None
    ref_epoch: float = Field(2000.0, description="Julian year of (ra, dec) when a motion is given")
    name: str | None = None
    force: bool = False


@router.post("/sequences/{seq_id}/analyze")
async def analyze_point(seq_id: str, req: AnalyzeRequest, request: Request) -> dict:
    """Start or join the full automatic analysis of one position of a finished sequence."""
    try:
        return await services.point_analysis_submit(seq_id, req.ra, req.dec, req.pmra, req.pmdec, req.ref_epoch,
                                                    req.name, client_id(request), req.force)
    except NotFound as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


def _check(job_id: str) -> None:
    if "-p" not in job_id:
        raise HTTPException(404, "not a point-analysis job")


@router.get("/analyses/{job_id}")
async def analysis_result(job_id: str, request: Request) -> Response:
    _check(job_id)
    try:
        status, job = await services.field_status(job_id, client_id(request))
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    if status == "done":
        return FileResponse(services.field_result_path(job_id), media_type="application/json",
                            headers={"Cache-Control": IMMUTABLE, "ETag": f'"{job_id}"'})
    if job is None:
        raise HTTPException(404, "unknown analysis")
    code = {"error": 500, "cancelled": 410}.get(status, 202)
    return Response(json.dumps(job), media_type="application/json", status_code=code,
                    headers={"Cache-Control": "no-store"})


@router.delete("/analyses/{job_id}")
async def analysis_withdraw(job_id: str, request: Request) -> Response:
    _check(job_id)
    job = await services.field_withdraw(job_id, client_id(request))
    return Response(json.dumps(job), media_type="application/json") if job else Response(status_code=204)
