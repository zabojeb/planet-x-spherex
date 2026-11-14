"""Archival epochs: 2MASS, AllWISE and NEOWISE images resampled onto a sequence's grid.

Registered before routes/sequences.py, whose catch-all `/sequences/{id}/{array}.bin` would otherwise
swallow `archival.bin`.
"""

from __future__ import annotations

import json

import numpy as np
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from spherex_core import services
from spherex_core.errors import NotFound

router = APIRouter(prefix="/api")
CACHE_1D = "public, max-age=86400"
FOV = Query(None, ge=1.0, le=60.0, description="Wider concentric field (arcmin); default: the sequence's grid")


@router.get("/sequences/{seq_id}/archival")
async def get_archival(seq_id: str, fov_arcmin: float | None = FOV) -> Response:
    """Older-survey epochs on the sequence grid. 200 with the frame list when built; 202 with progress while
    building (the first call starts the build); 404 if the sequence is not ready or is a solar-system object;
    500 with the task if the build failed."""
    try:
        status, payload = await services.archival_status(seq_id, fov_arcmin)
    except NotFound as e:
        raise HTTPException(404, str(e)) from e
    if status == "done":
        return Response(payload, media_type="application/json")
    return Response(json.dumps(payload), media_type="application/json",
                    status_code=500 if status == "error" else 202)


@router.get("/sequences/{seq_id}/archival.bin")
def get_archival_bin(seq_id: str, fov_arcmin: float | None = FOV,
                     index: int | None = Query(None, ge=0, description="One frame [n, n] instead of all [k, n, n]")
                     ) -> Response:
    """Archival frames as float32 little-endian, MJy/sr, NaN where no data, in the order of `frames`."""
    try:
        data = services.archival_frames(seq_id, fov_arcmin, index)
    except NotFound as e:
        raise HTTPException(404, str(e)) from e
    return Response(np.ascontiguousarray(data, dtype="<f4").tobytes(), media_type="application/octet-stream",
                    headers={"Cache-Control": CACHE_1D})
