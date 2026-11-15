"""Health of the remote services we depend on, and of the local SPHEREx file-catalogue mirror."""

from __future__ import annotations

from fastapi import APIRouter

from spherex_core import services

router = APIRouter(prefix="/api/status")


@router.get("/upstreams")
def upstreams() -> dict:
    """Per kind of data (spherex-files, spherex-meta, gaia, catwise, 2mass, simbad, solar-system, ...): every source in
    the order it is asked now, with success rate, p50/p90 latency, last error and circuit-breaker state; plus the
    mirror's size and age. Sources that have not been used since the process started show `samples: 0`."""
    return services.upstream_status()
