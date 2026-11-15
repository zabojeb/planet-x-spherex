"""Solar-system science on co-moving sequences (ADR-0057): "different background, same comet".

    POST /api/sequences/{id}/static-sky              build the static-sky subtracted version (build queue);
                                                     answers {sequence: <clean id>, status, job}
    GET  /api/sequences/{id}/clean.bin?epoch=&band=  clean co-add (the generic array route; also clean_coverage)
    GET  /api/sequences/{id}/clean_exposures.bin?epoch=&band=   clean single exposures, float32 [k, n, n]
    GET  /api/sequences/{id}/clean-summary           noise and object S/N per epoch/detector, clean vs plain
    POST /api/sequences/{id}/comet                   comet analysis (job)
    POST /api/sequences/{id}/asteroid                asteroid light curve / period / taxonomy (job)
    POST /api/sequences/{id}/unknown-movers          unidentified mover candidates (job; fixed-position sequences)

Jobs follow the field-analysis lifecycle: GET /api/field/{job} answers 202 while running (each GET is a heartbeat),
200 with the result, 410 when cancelled; DELETE /api/field/{job} withdraws; GET /api/field/{job}/ades.psv exports
an unknown-movers result as ADES (station 288, follow-up data, not a submission).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from spherex_core import services
from spherex_core.bands import BANDS
from spherex_core.errors import NotFound

from ..http import cached, client_id, client_ip, too_many

router = APIRouter(prefix="/api")


class StaticSkyRequest(BaseModel):
    force: bool = False


class CometRequest(BaseModel):
    rho_km: list[float] | None = Field(None, description="Aperture radii in km (default 1e4, 2e4, 5e4, 1e5)")
    use_clean: bool = Field(True, description="Use static-sky subtracted exposures when the sequence has them")
    force: bool = False


class AsteroidRequest(BaseModel):
    min_period_h: float = Field(1.5, ge=0.05, le=1000)
    max_period_h: float | None = Field(None, gt=0, le=10000)
    use_clean: bool = True
    force: bool = False


class MoversRequest(BaseModel):
    vmag_lim: float = Field(21.0, ge=10, le=26)
    field_job: str | None = Field(None, description="A finished field analysis job of this sequence to reuse")
    force: bool = False


async def _start(coro) -> dict:
    try:
        return await coro
    except NotFound as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.post("/sequences/{seq_id}/static-sky")
async def static_sky(seq_id: str, req: StaticSkyRequest, request: Request) -> dict:
    """Start or join the static-sky build of a co-moving sequence; poll GET /api/sequences/{sequence}."""
    try:
        return await _start(services.static_sky_submit(seq_id, force=req.force, ip=client_ip(request),
                                                       client=client_id(request)))
    except services.QueueFull as e:
        raise too_many(e) from e


@router.get("/sequences/{seq_id}/clean_exposures.bin")
def clean_exposures(seq_id: str, request: Request, epoch: int = Query(..., ge=0), band: str = Query(...)) -> Response:
    """Static-sky subtracted single exposures of one epoch and detector, float32 [k, n, n] (NaN = masked)."""
    if band not in BANDS:
        raise HTTPException(422, "band must be D1..D6")
    try:
        data, idx = services.clean_exposures(seq_id, epoch, band)
    except NotFound as e:
        raise HTTPException(404, str(e)) from e
    return cached(request, f'"{seq_id}-cx{epoch}{band}"', data.tobytes(), media_type="application/octet-stream",
                  headers={"X-Exposure-Indices": ",".join(map(str, idx)),
                           "Access-Control-Expose-Headers": "X-Exposure-Indices"})


@router.get("/sequences/{seq_id}/clean-summary")
def clean_summary(seq_id: str, epoch: int | None = Query(None, ge=0)) -> dict:
    try:
        return services.clean_summary(seq_id, epoch)
    except NotFound as e:
        raise HTTPException(404, str(e)) from e


@router.post("/sequences/{seq_id}/comet")
async def comet(seq_id: str, req: CometRequest, request: Request) -> dict:
    """Comet analysis: km-aperture photometry, coma profile, Afrho, CO2/CO/H2O bands and Q proxies, activity."""
    opts = {"rho_km": req.rho_km, "use_clean": req.use_clean}
    return await _start(services.comet_submit(seq_id, opts, client_id(request), req.force))


@router.post("/sequences/{seq_id}/asteroid")
async def asteroid(seq_id: str, req: AsteroidRequest, request: Request) -> dict:
    """Asteroid analysis: rotation period, light curve, reflectance spectrum, band depths and rough taxonomy."""
    opts = {"min_period_h": req.min_period_h, "max_period_h": req.max_period_h, "use_clean": req.use_clean}
    return await _start(services.asteroid_submit(seq_id, opts, client_id(request), req.force))


@router.post("/sequences/{seq_id}/unknown-movers")
async def unknown_movers(seq_id: str, req: MoversRequest, request: Request) -> dict:
    """Tracklets no known object explains, with their evidence; ADES via GET /api/field/{job}/ades.psv."""
    return await _start(services.unknown_movers_submit(seq_id, req.vmag_lim, req.field_job, client_id(request),
                                                       req.force))
