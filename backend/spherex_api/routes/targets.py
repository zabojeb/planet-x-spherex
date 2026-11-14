"""Presets and name/coordinate resolution."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query

from spherex_core import services

router = APIRouter(prefix="/api")


@router.get("/targets")
def targets() -> list[dict]:
    return services.list_presets()


@router.get("/resolve")
async def resolve_target(q: str = Query(..., min_length=1, max_length=200)) -> dict:
    try:
        return await asyncio.to_thread(services.resolve_target, q)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
