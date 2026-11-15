"""The sky map's data (ADR-0059): a caching HiPS proxy, SPHEREx coverage at a point, exposure footprints near one."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response

from spherex_core import hips, services, upstreams

router = APIRouter()

IMMUTABLE = "public, max-age=31536000, immutable"
META = "public, max-age=3600"


@router.get("/hips/{survey}/{path:path}")
async def hips_file(survey: str, path: str, request: Request) -> Response:
    """A file of a proxied HiPS (tile, Allsky image, properties, Moc.fits), served from our disk cache and fetched
    from the source (IRSA, or a configured mirror) the first time. Tiles are immutable within a release."""
    if survey not in hips.SURVEYS:
        raise HTTPException(404, "unknown HiPS survey")
    try:
        hips.check_path(path)
    except hips.NotHipsPath:
        raise HTTPException(404, "not a HiPS file") from None
    try:
        p = await services.hips_file(survey, path)
    except hips.Missing:
        return Response(status_code=404, headers={"Cache-Control": "public, max-age=86400"})
    except (upstreams.AllFailed, RuntimeError, OSError) as e:
        raise HTTPException(502, f"HiPS source unavailable: {str(e)[:160]}") from e
    st = p.stat()
    etag = f'"{int(st.st_mtime)}-{st.st_size}"'
    cc = IMMUTABLE if hips.is_immutable(path) else META
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": cc})
    return FileResponse(p, media_type=hips.content_type(path), headers={"ETag": etag, "Cache-Control": cc})


@router.get("/api/coverage")
async def coverage(ra: float = Query(..., ge=0, lt=360), dec: float = Query(..., ge=-90, le=90)) -> dict:
    """SPHEREx exposures covering a position: counts per detector and release, visits with dates, deep field or not.
    Answered from the local file catalogue when it exists (milliseconds), else by an image search."""
    try:
        return await services.coverage_at(ra, dec)
    except RuntimeError as e:
        raise HTTPException(502, f"image search unavailable: {str(e)[:160]}") from e


@router.get("/api/footprints")
async def footprints(ra: float = Query(..., ge=0, lt=360), dec: float = Query(..., ge=-90, le=90),
                     radius: float = Query(3.0, gt=0, le=90), limit: int = Query(600, ge=1, le=2000)) -> Response:
    """Outlines of SPHEREx exposures within `radius` degrees (capped at 12°), newest first, one detector of each
    dichroic pair; `{available: false}` when this server has no local file catalogue."""
    out = await services.footprints_near(ra, dec, radius, limit)
    import json

    return Response(json.dumps(out), media_type="application/json", headers={"Cache-Control": "public, max-age=600"})
