"""Solar-system objects: which SPHEREx exposures saw them (IRSA MOST) and the request for a co-moving
sequence. Build the sequence itself with POST /api/sequences and the returned `suggested_request`."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from spherex_core import services

router = APIRouter(prefix="/api")
DATE = r"^\d{4}-\d{2}-\d{2}$"
_STATUS = {"AmbiguousObject": 409, "LookupError": 404, "UpstreamError": 502}


@router.get("/moving/search")
async def search(name: str | None = Query(None, min_length=1, max_length=120,
                                          description="Name or designation, e.g. 3I, Eros, C/2025 N1"),
                 type: str | None = Query(None, pattern=r"^(?i:asteroid|comet)$",
                                          description="Asteroid or Comet; guessed from the name if omitted"),
                 begin: str | None = Query(None, pattern=DATE, description="Search from (UTC date), default 2025-04-01"),
                 end: str | None = Query(None, pattern=DATE, description="Search to (UTC date), default today"),
                 naif_id: int | None = Query(None, description="JPL NAIF ID instead of a name (asteroid N: 20000000+N)"),
                 ) -> Response:
    """200 with the object, its SPHEREx exposures, ephemeris and `suggested_request`; 202 while MOST is still
    searching (long windows take minutes; poll the same URL); 409 with `choices` for ambiguous names; 404 for
    unknown objects; 502 when MOST is unavailable."""
    try:
        status, payload = await services.moving_search(name, type, begin, end, naif_id)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    if status == "done":
        return Response(json.dumps(payload), media_type="application/json")
    if status == "error":
        code = _STATUS.get(payload.get("kind", ""), 500)
        return Response(json.dumps(payload), media_type="application/json", status_code=code)
    return Response(json.dumps(payload), media_type="application/json", status_code=202)
