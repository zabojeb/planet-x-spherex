"""Comet discovery (ADR-0063): the comet search over a sequence and the known-comet watch.

    POST /api/sequences/{id}/comet-search   comet search (a job, kind h): on a fixed field, moving sources (fast
                                            tracklets, slow links over days, shift-and-stack hits) ranked by
                                            comet-likeness with coma, spectrum, orbit-class and identification
                                            evidence; on a co-moving sequence, the same tests on its own object
    GET  /api/field/{job}                   the result (202 while running; every GET is a heartbeat)
    GET  /api/field/{job}/ades.psv          ADES of the measured candidates (station 288, follow-up data, not a
                                            submission; ?all=true adds the low-quality ones)
    GET  /api/comets/watch                  watched comets with their latest activity, and the bright comets now
    POST /api/comets/watch/refresh          start the watch refresh (MOST, prewarm co-moving builds, analyses)
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from spherex_core import services
from spherex_core.errors import NotFound

from ..http import client_id

router = APIRouter(prefix="/api")


class CometSearchRequest(BaseModel):
    field_job: str | None = Field(None, description="A finished field analysis job of this sequence to reuse")
    slow: bool = Field(True, description="Link slow movers over days (parallax-aware)")
    stack: bool = Field(True, description="Shift-and-stack search for faint slow movers")
    vmag_lim: float = Field(21.0, ge=10, le=26, description="Faintest known object (V) to identify")
    window_days: float = Field(6.0, ge=1, le=30, description="Longest stretch of a visit linked or stacked at once")
    stack_rate_max: float = Field(2.0, ge=0.1, le=5, description="Fastest trial motion of the stack search (arcsec/h)")
    identify: bool = Field(True, description="Identify the best candidates against JPL's small-body database")
    force: bool = False


@router.post("/sequences/{seq_id}/comet-search")
async def comet_search(seq_id: str, req: CometSearchRequest, request: Request) -> dict:
    """Start or join a comet search; poll GET /api/field/{job}."""
    try:
        return await services.comet_search_submit(seq_id, req.model_dump(exclude={"force"}), client_id(request),
                                                  req.force)
    except NotFound as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/comets/watch")
async def comet_watch(limit: int = Query(30, ge=1, le=200, description="Bright comets now to list")) -> dict:
    """Watched comets (latest visit's r_h, Afrho, CO2, comet-likeness, outburst flags) and the comets now."""
    return await services.comet_watch(limit)


@router.post("/comets/watch/refresh")
async def comet_watch_refresh() -> dict:
    """Start the watch refresh in the background (idempotent while it runs); poll GET /api/comets/watch."""
    return services.comet_watch_refresh()
