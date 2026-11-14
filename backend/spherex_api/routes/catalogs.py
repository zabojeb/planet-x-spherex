"""Reference catalog overlays (Gaia DR3, CatWISE2020, 2MASS)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query

from spherex_core import services

router = APIRouter(prefix="/api")


@router.get("/catalogs")
def list_catalogs() -> dict:
    return services.list_catalogs()


@router.get("/catalogs/{name}")
async def catalog_cone(name: str, ra: float = Query(..., ge=0, lt=360), dec: float = Query(..., ge=-90, le=90),
                       radius_arcmin: float = Query(5.0, gt=0, le=30)) -> list[dict]:
    labels = services.list_catalogs()
    if name not in labels:
        raise HTTPException(404, "unknown catalog")
    try:
        return await asyncio.to_thread(services.catalog_cone, name, ra, dec, radius_arcmin)
    except RuntimeError as e:
        raise HTTPException(502, f"{labels[name]} is unavailable: {e}") from e
