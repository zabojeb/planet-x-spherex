"""Reference catalog overlays (Gaia DR3, CatWISE2020, 2MASS)."""

from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from spherex_core import services

from ..http import SHORT, cached, run_until_disconnect

router = APIRouter(prefix="/api")


@router.get("/catalogs")
def list_catalogs() -> dict:
    return services.list_catalogs()


@router.get("/catalogs/{name}")
async def catalog_cone(name: str, request: Request, ra: float = Query(..., ge=0, lt=360),
                       dec: float = Query(..., ge=-90, le=90), radius_arcmin: float = Query(5.0, gt=0, le=30)) -> Response:
    """One catalog layer as a JSON list (fetched per layer by the client), cached 1 h with an ETag; the remote
    query is abandoned if the client disconnects."""
    labels = services.list_catalogs()
    if name not in labels:
        raise HTTPException(404, "unknown catalog")
    try:
        rows = await run_until_disconnect(request, services.catalog_cone, name, ra, dec, radius_arcmin)
    except services.UpstreamError as e:  # message already names the catalog
        raise HTTPException(502, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(502, f"{labels[name]} is unavailable: {e}") from e
    body = json.dumps(rows).encode()
    return cached(request, '"' + hashlib.sha1(body).hexdigest()[:20] + '"', body, cache=SHORT)
