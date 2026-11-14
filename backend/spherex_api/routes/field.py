"""Automated field analysis: 'Analyze this field', Moving Object Detection, known objects, Track & Stack,
candidate verification and ADES export.

Jobs (field analysis, moving objects, known objects, stack search) share one lifecycle: POST starts or joins
the job and returns its JSON (status queued/running/done); GET /api/field/{job} answers 200 with the result,
202 while it runs (every GET is a heartbeat), 500 if it failed, 410 if cancelled (POST again); DELETE withdraws
this client, and a job nobody polls for 60 s is cancelled. Finished results never change for a given job id.
"""

from __future__ import annotations

import json
from typing import Literal

import numpy as np
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field

from spherex_core import services
from spherex_core.bands import BANDS
from spherex_core.errors import NotFound, UpstreamError

from ..http import IMMUTABLE, client_id

router = APIRouter(prefix="/api")


class FieldRequest(BaseModel):
    snr_min: float = Field(5.0, ge=3, le=50, description="Detection threshold on the co-adds")
    max_sources: int = Field(300, ge=10, le=1000)
    catalogs: bool = True
    motion: bool = True
    shift_stack: bool = True
    transients: bool = True
    known_objects: bool = True
    variability: bool = True
    colours: bool = True
    vmag_lim: float = Field(21.0, ge=10, le=25, description="Faintest known object (V) to identify")
    force: bool = False


class MovingRequest(BaseModel):
    vmag_lim: float = Field(21.0, ge=10, le=25)
    force: bool = False


class KnownRequest(BaseModel):
    times: Literal["epochs", "exposures"] = "epochs"
    epoch: int | None = Field(None, ge=0)
    vmag_lim: float = Field(21.0, ge=10, le=25)


class StackSearchRequest(BaseModel):
    epoch: int = Field(..., ge=0)
    rate_min: float = Field(0.3, ge=0, description="arcsec/h")
    rate_max: float = Field(10.0, gt=0, le=200, description="arcsec/h")
    window_hours: float = Field(48.0, gt=1, le=720, description="Densest window of the visit searched")
    bands: list[str] | None = None
    snr_min: float = Field(6.0, ge=3)


class VerifyRequest(BaseModel):
    ra: float = Field(..., ge=0, lt=360)
    dec: float = Field(..., ge=-90, le=90)
    mjd_ref: float | None = None
    rate: float = Field(0.0, ge=0, description="arcsec/h")
    pa: float = Field(0.0, description="deg east of north")
    epoch: int | None = Field(None, ge=0)
    max_exposures: int = Field(40, ge=1, le=200)


def _bands(text: str | list[str] | None) -> list[str] | None:
    if not text:
        return None
    vals = text.split(",") if isinstance(text, str) else text
    bad = [b for b in vals if b not in BANDS]
    if bad:
        raise HTTPException(422, f"unknown detector(s): {', '.join(bad)}")
    return list(vals)


async def _start(coro) -> dict:
    try:
        return await coro
    except NotFound as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.post("/sequences/{seq_id}/field")
async def analyze_field(seq_id: str, req: FieldRequest, request: Request) -> dict:
    """Start or join 'Analyze this field' over a finished sequence."""
    opts = req.model_dump(exclude={"force"})
    return await _start(services.field_submit(seq_id, opts, client_id(request), req.force))


@router.post("/sequences/{seq_id}/moving-objects")
async def moving_objects(seq_id: str, req: MovingRequest, request: Request) -> dict:
    """Moving Object Detection only: tracklets inside each visit, identified against known objects."""
    opts = {"catalogs": False, "motion": False, "shift_stack": False, "variability": False, "colours": False,
            "transients": True, "known_objects": True, "vmag_lim": req.vmag_lim}
    return await _start(services.field_submit(seq_id, opts, client_id(request), req.force))


@router.post("/sequences/{seq_id}/known-objects")
async def known_objects(seq_id: str, req: KnownRequest, request: Request) -> dict:
    """Known asteroids and comets in the field as seen from SPHEREx (overlay), per epoch or per exposure."""
    return await _start(services.known_objects_submit(seq_id, req.times, req.epoch, req.vmag_lim,
                                                      client_id(request)))


@router.post("/sequences/{seq_id}/trackstack/search")
async def trackstack_search(seq_id: str, req: StackSearchRequest, request: Request) -> dict:
    """Synthetic tracking over trial motions within one visit (a job)."""
    return await _start(services.trackstack_search_submit(seq_id, req.epoch, req.rate_min, req.rate_max,
                                                          req.window_hours, _bands(req.bands), req.snr_min,
                                                          client_id(request)))


@router.get("/field/{job_id}")
async def field_result(job_id: str, request: Request) -> Response:
    try:
        status, job = await services.field_status(job_id, client_id(request))
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    if status == "done":
        return FileResponse(services.field_result_path(job_id), media_type="application/json",
                            headers={"Cache-Control": IMMUTABLE, "ETag": f'"{job_id}"'})
    if job is None:
        raise HTTPException(404, "unknown field job")
    code = {"error": 500, "cancelled": 410}.get(status, 202)
    return Response(json.dumps(job), media_type="application/json", status_code=code,
                    headers={"Cache-Control": "no-store"})


@router.delete("/field/{job_id}")
async def field_withdraw(job_id: str, request: Request) -> Response:
    job = await services.field_withdraw(job_id, client_id(request))
    return Response(json.dumps(job), media_type="application/json") if job else Response(status_code=204)


@router.get("/field/{job_id}/ades.psv")
def field_ades(job_id: str, all: bool = Query(False, description="include low-quality tracklets")) -> PlainTextResponse:
    """Tracklets as ADES PSV for station 288 with SPHEREx positions (follow-up data, not a submission)."""
    try:
        text = services.field_ades(job_id, all)
    except NotFound as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    except UpstreamError as e:
        raise HTTPException(502, str(e)) from e
    return PlainTextResponse(text, headers={"Content-Disposition": f'attachment; filename="{job_id}_ades.psv"'})


@router.get("/sequences/{seq_id}/trackstack.bin")
def trackstack(seq_id: str, epoch: int = Query(..., ge=0), rate: float = Query(..., ge=0, description="arcsec/h"),
               pa: float = Query(0.0, description="deg east of north"),
               combine: Literal["mean", "median", "sum"] = "mean", bands: str | None = None,
               subtract_static: bool = False, parts: int = Query(1, ge=1, le=3),
               part: int = Query(0, ge=0, le=2)) -> Response:
    """Track & Stack of one visit at a motion: one float32 LE [n, n] plane (part `part` of `parts`)."""
    try:
        res = services.trackstack_image(seq_id, epoch, rate, pa, _bands(bands), combine, subtract_static, parts)
    except NotFound as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    if part >= len(res["parts"]):
        raise HTTPException(422, "part must be below parts")
    p = res["parts"][part]
    img = np.ascontiguousarray(p["image"], dtype="<f4")
    headers = {"X-Array-Shape": f"{img.shape[0]},{img.shape[1]}", "X-Array-Dtype": "float32",
               "X-Track-Tref": f"{res['t_ref']:.6f}", "X-Track-Frames": str(p["frames"]),
               "X-Track-Bands": ",".join(res["bands"]), "Cache-Control": IMMUTABLE,
               "Access-Control-Expose-Headers": "X-Array-Shape, X-Array-Dtype, X-Track-Tref, X-Track-Frames, "
                                                "X-Track-Bands"}
    return Response(img.tobytes(), media_type="application/octet-stream", headers=headers)


@router.post("/sequences/{seq_id}/verify")
async def verify(seq_id: str, req: VerifyRequest) -> dict:
    """Free-position ePSF fits on the native exposures near a candidate, plus stare/track cutout strips."""
    try:
        return await services.verify_candidate(seq_id, req.ra, req.dec, req.mjd_ref, req.rate, req.pa, req.epoch,
                                               req.max_exposures)
    except NotFound as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(502, f"could not read the sequence's files: {e}") from e
