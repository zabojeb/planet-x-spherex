"""Presets, the local object index for search suggestions, and name/coordinate resolution."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from spherex_core import services

router = APIRouter(prefix="/api")


@router.get("/targets")
def targets() -> list[dict]:
    return services.list_presets()


@router.get("/objects")
def objects(request: Request) -> Response:
    """The object index the web client searches locally (one download, then revalidated by ETag)."""
    body, etag = services.object_index()
    headers = {"ETag": etag, "Cache-Control": "public, max-age=3600"}
    if etag in request.headers.get("if-none-match", ""):
        return Response(status_code=304, headers=headers)
    return Response(body, media_type="application/json", headers=headers)


@router.get("/resolve")
async def resolve_target(q: str = Query(..., min_length=1, max_length=200)) -> dict:
    try:
        return await asyncio.to_thread(services.resolve_target, q)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
