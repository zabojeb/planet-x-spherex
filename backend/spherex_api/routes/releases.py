"""Data freshness: SPHEREx collections at IRSA and whether a sequence has newer exposures."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from spherex_core import services
from spherex_core.errors import NotFound, UpstreamError

router = APIRouter(prefix="/api")


@router.get("/releases")
def releases() -> dict:
    """Every SPHEREx collection with image count and observation time span (cached for an hour)."""
    try:
        return services.releases_summary()
    except UpstreamError as e:
        raise HTTPException(502, str(e)) from e


@router.get("/sequences/{seq_id}/freshness")
def freshness(seq_id: str) -> dict:
    """New exposures available since the sequence was built (cached for ten minutes)."""
    try:
        return services.sequence_freshness(seq_id)
    except NotFound as e:
        raise HTTPException(404, str(e)) from e
    except UpstreamError as e:
        raise HTTPException(502, str(e)) from e
