"""Sequence jobs and their results: metadata JSON and raw little-endian arrays for the web client."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response

from spherex_core import services
from spherex_core.errors import NotFound

from ..schemas import SequenceRequest

router = APIRouter(prefix="/api")
CACHE_1D = "public, max-age=86400"


@router.post("/sequences")
async def create_sequence(req: SequenceRequest) -> dict:
    # async on purpose: starting a job schedules a task on the running event loop
    return await services.submit_sequence(req.params(), force=req.force)


@router.get("/sequences/{seq_id}")
def get_sequence(seq_id: str) -> Response:
    """200 with meta.json when done, 202 with the job while running, 500 with the job if it failed."""
    status, job = services.sequence_status(seq_id)
    if status == "done":
        return Response(services.sequence_meta_bytes(seq_id), media_type="application/json")
    if job is None:
        raise HTTPException(404, "unknown sequence")
    return Response(json.dumps(job), media_type="application/json", status_code=500 if status == "error" else 202)


@router.get("/sequences/{seq_id}/exposures.bin")
def get_exposures(seq_id: str, epoch: int = Query(..., ge=0), band: str = Query(...)) -> Response:
    """Aligned single exposures of one epoch and detector, as float32 [n, ny, nx]."""
    try:
        data, idx = services.read_exposures(seq_id, epoch, band)
    except NotFound as e:
        raise HTTPException(404, str(e)) from e
    return Response(data.tobytes(), media_type="application/octet-stream",
                    headers={"X-Exposure-Indices": ",".join(map(str, idx)), "Cache-Control": CACHE_1D,
                             "Access-Control-Expose-Headers": "X-Exposure-Indices"})


@router.get("/sequences/{seq_id}/{array}.bin")
def get_array(seq_id: str, array: str) -> FileResponse:
    try:
        path = services.array_file(seq_id, array)
    except NotFound as e:
        raise HTTPException(404, str(e)) from e
    return FileResponse(path, media_type="application/octet-stream", headers={"Cache-Control": CACHE_1D})
