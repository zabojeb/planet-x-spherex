"""ASGI entry point: `uvicorn spherex_api.main:app` (used by scripts/deploy.sh)."""

from .app import create_app

app = create_app()
