"""Sequence jobs and their results.

Job lifecycle: POST starts or joins a build (429 + Retry-After when limits are hit); every GET while it
runs is a heartbeat; DELETE withdraws this client's interest, and a build nobody wants is cancelled.
Finished results are immutable (the id is a hash of the parameters), so every result URL is served with
`Cache-Control: public, max-age=31536000, immutable` and an ETag; repeat visits cost a 304 or nothing.

Lightweight access for the web client (all immutable):
    GET /api/sequences/{id}                       meta.json (precompressed br/gzip)
    GET /api/sequences/{id}/lite                  meta without the per-exposure tables (+ table sizes)
    GET /api/sequences/{id}/columns/{table}       exposures | photometry as columns
    GET /api/sequences/{id}/{array}.bin           whole cube, HTTP Range supported
    GET /api/sequences/{id}/{array}.bin?epoch=&band=   one [n, n] slice of coadds / coverage / flags
"""

from __future__ import annotations

import json
from functools import lru_cache

import numpy as np
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response

from spherex_core import services
from spherex_core.bands import BANDS
from spherex_core.errors import NotFound
from spherex_core.pipeline import results

from ..http import IMMUTABLE, cached, client_id, client_ip, not_modified, too_many
from ..schemas import SequenceRequest

router = APIRouter(prefix="/api")
TABLES = ("exposures", "photometry")


@router.post("/sequences")
async def create_sequence(req: SequenceRequest, request: Request) -> dict:
    try:
        return await services.submit_sequence(req.params(), force=req.force, ip=client_ip(request),
                                              client=client_id(request))
    except services.QueueFull as e:  # TooManyJobs is a QueueFull
        raise too_many(e) from e




@router.get("/sequences/{seq_id}")
async def get_sequence(seq_id: str, request: Request) -> Response:
    """200 with meta.json when done, 202 with the job while queued/running (a heartbeat), 500 if it failed,
    410 if it was cancelled (POST again to restart)."""
    status, job = await services.poll_sequence(seq_id, client=client_id(request))
    if status == "done":
        etag = f'"{seq_id}"'
        if not_modified(request, etag):
            return Response(status_code=304, headers={"ETag": etag, "Cache-Control": IMMUTABLE})
        path, encoding = results.meta_variant(services.result_dir(seq_id), request.headers.get("accept-encoding", ""))
        headers = {"ETag": etag, "Cache-Control": IMMUTABLE, "Vary": "Accept-Encoding"}
        if encoding:
            headers["Content-Encoding"] = encoding
        return FileResponse(path, media_type="application/json", headers=headers)
    if job is None:
        raise HTTPException(404, "unknown sequence")
    code = {"error": 500, "cancelled": 410}.get(status, 202)
    return Response(json.dumps(job), media_type="application/json", status_code=code,
                    headers={"Cache-Control": "no-store"})


@router.delete("/sequences/{seq_id}")
async def withdraw_sequence(seq_id: str, request: Request) -> Response:
    """This client no longer needs the build (navigated away). Idempotent; 204 even for unknown ids."""
    job = await services.withdraw_sequence(seq_id, client=client_id(request))
    return Response(json.dumps(job), media_type="application/json") if job else Response(status_code=204)


@lru_cache(maxsize=256)
def _lite(seq_id: str) -> bytes:
    meta = services.load_sequence(seq_id)
    lite = {k: v for k, v in meta.items() if k not in TABLES}
    lite["tables"] = {t: {"rows": len(meta.get(t) or []), "url": f"/api/sequences/{seq_id}/columns/{t}"}
                      for t in TABLES}
    return json.dumps(lite).encode()


@lru_cache(maxsize=256)
def _columns(seq_id: str, table: str) -> bytes:
    rows = services.load_sequence(seq_id).get(table) or []
    cols: list[str] = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    return json.dumps({"rows": len(rows), "columns": cols,
                       "data": {c: [r.get(c) for r in rows] for c in cols}}).encode()


@router.get("/sequences/{seq_id}/lite")
def get_lite(seq_id: str, request: Request) -> Response:
    try:
        return cached(request, f'"{seq_id}-lite"', _lite(seq_id))
    except NotFound as e:
        raise HTTPException(404, str(e)) from e


@router.get("/sequences/{seq_id}/columns/{table}")
def get_columns(seq_id: str, table: str, request: Request) -> Response:
    if table not in TABLES:
        raise HTTPException(404, "unknown table")
    try:
        return cached(request, f'"{seq_id}-{table}"', _columns(seq_id, table))
    except NotFound as e:
        raise HTTPException(404, str(e)) from e


@router.get("/sequences/{seq_id}/exposures.bin")
def get_exposures(seq_id: str, request: Request, epoch: int = Query(..., ge=0), band: str = Query(...)) -> Response:
    """Aligned single exposures of one epoch and detector, as float32 [n, ny, nx]."""
    etag = f'"{seq_id}-x{epoch}{band}"'
    extra = {"Access-Control-Expose-Headers": "X-Exposure-Indices"}
    try:
        data, idx = services.read_exposures(seq_id, epoch, band)
    except NotFound as e:
        raise HTTPException(404, str(e)) from e
    extra["X-Exposure-Indices"] = ",".join(map(str, idx))
    return cached(request, etag, data.tobytes(), media_type="application/octet-stream", headers=extra)


@router.get("/sequences/{seq_id}/{array}.bin")
def get_array(seq_id: str, array: str, request: Request, epoch: int | None = Query(None, ge=0),
              band: str | None = Query(None)) -> Response:
    """The whole cube (Range requests supported), or with epoch+band one [n, n] slice in the same dtype."""
    try:
        path = services.array_file(seq_id, array)
    except NotFound as e:
        raise HTTPException(404, str(e)) from e
    if epoch is None and band is None:
        if not_modified(request, f'"{seq_id}-{array}"'):
            return Response(status_code=304, headers={"ETag": f'"{seq_id}-{array}"', "Cache-Control": IMMUTABLE})
        return FileResponse(path, media_type="application/octet-stream",
                            headers={"Cache-Control": IMMUTABLE, "ETag": f'"{seq_id}-{array}"'})
    if epoch is None or band is None or band not in BANDS:
        raise HTTPException(422, "give both epoch and band (D1..D6)")
    meta = json.loads(_lite(seq_id))
    shape = meta["arrays"][array]["shape"]  # [epochs, bands, n, n]
    if epoch >= shape[0]:
        raise HTTPException(404, "no such epoch")
    dtype = np.dtype(results.DTYPES[array][0])
    plane = shape[2] * shape[3] * dtype.itemsize
    offset = (epoch * shape[1] + BANDS.index(band)) * plane
    with open(path, "rb") as f:
        f.seek(offset)
        body = f.read(plane)
    return cached(request, f'"{seq_id}-{array}-{epoch}{band}"', body, media_type="application/octet-stream",
                  headers={"X-Array-Shape": f"{shape[2]},{shape[3]}", "X-Array-Dtype": dtype.name,
                           "Access-Control-Expose-Headers": "X-Array-Shape, X-Array-Dtype"})
