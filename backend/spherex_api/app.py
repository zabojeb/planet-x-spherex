"""FastAPI application factory: middleware, routes, and the built web client as static files."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from spherex_core import __version__, services

from .routes import catalogs, sequences, targets

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


def create_app(web_dist: Path | None = WEB_DIST) -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = FastAPI(title="Planet X × SPHEREx API", version=__version__,
                  description="Blink SPHEREx infrared images over time: search, aligned epoch co-adds, spectra.")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.add_middleware(GZipMiddleware, minimum_size=2048)

    @app.get("/api/health")
    def health() -> dict:
        return services.health()

    app.include_router(targets.router)
    app.include_router(sequences.router)
    app.include_router(catalogs.router)
    if web_dist is not None and web_dist.exists():
        app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")
    return app
