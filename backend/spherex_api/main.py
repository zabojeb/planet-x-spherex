"""ASGI entry point: `uvicorn spherex_api.main:app` (used by scripts/deploy.sh)."""

from spherex_core.jobs.model import queue_settings
from spherex_core.pipeline.sequence import warm_pool

from .app import create_app

app = create_app()
if queue_settings().backend != "redis":  # builds run in this process: have the workers ready for the first one
    warm_pool()
