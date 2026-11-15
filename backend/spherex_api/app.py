"""FastAPI application factory: middleware, routes, and the built web client as static files."""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from spherex_core import __version__, logs, services
from spherex_core.config import settings
from spherex_core.runtime import setup_logging

from .routes import catalogs, sequences, targets
from .routes import analysis, exports, releases
from .routes import archival, moving
from .routes import spectral
from .routes import field
from .routes import point
from .routes import solar

log = logging.getLogger("spherex_api.http")
_SEQ_PATH = re.compile(r"^/api/sequences/([A-Za-z0-9]+)")
WEB_DIST = Path(os.environ.get("SPHEREX_WEB_DIST") or Path(__file__).resolve().parents[2] / "web" / "dist")


class WebFiles(StaticFiles):
    """The built client: hashed files under assets/ are cached forever, index.html is revalidated."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        if resp.status_code in (200, 304):
            resp.headers["Cache-Control"] = ("public, max-age=31536000, immutable" if path.startswith("assets/")
                                             else "no-cache")
        return resp


def create_app(web_dist: Path | None = WEB_DIST) -> FastAPI:
    setup_logging()
    app = FastAPI(title="Planet X × SPHEREx API", version=__version__,
                  description="Blink SPHEREx infrared images over time: search, aligned epoch co-adds, spectra.")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.add_middleware(GZipMiddleware, minimum_size=2048)

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        """One line per API request: method, path, status, seconds, size, tagged with the sequence id when the
        path has one. Progress polls (202) and static files go to DEBUG so a waiting browser does not flood the
        log; slow requests are WARNING. (By Max0072.)"""
        path = request.url.path
        m = _SEQ_PATH.match(path)
        token = logs.seq_id.set(m.group(1)) if m else None
        t0 = time.perf_counter()
        try:
            try:
                response = await call_next(request)
            except Exception:
                log.exception("%s %s failed after %.2f s", request.method, path, time.perf_counter() - t0)
                raise
            s = time.perf_counter() - t0
            quiet = response.status_code == 202 or not path.startswith("/api/")
            level = logging.WARNING if s > settings.slow_request_s else logging.DEBUG if quiet else logging.INFO
            logs.event(log, level, f"{request.method} {path}", status=response.status_code, s=s,
                       query=request.url.query or None, bytes=response.headers.get("content-length"))
        finally:
            if token is not None:
                logs.seq_id.reset(token)
        return response

    @app.get("/api/health")
    def health() -> dict:
        """Liveness: the process answers."""
        return services.health()

    @app.get("/api/ready")
    async def ready() -> dict:
        """Readiness: queue backend and bucket reachable (Railway health check)."""
        try:
            return await services.readiness()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(503, f"not ready: {e}") from e

    @app.get("/api/queue")
    async def queue() -> dict:
        return await services.queue_stats()

    app.include_router(targets.router)
    app.include_router(archival.router)  # before sequences: its /{array}.bin catch-all would take archival.bin
    app.include_router(field.router)  # before sequences for the same reason (trackstack.bin)
    app.include_router(solar.router)  # before sequences for the same reason (clean_exposures.bin)
    app.include_router(sequences.router)
    app.include_router(moving.router)
    app.include_router(catalogs.router)
    app.include_router(analysis.router)
    app.include_router(exports.router)
    app.include_router(releases.router)
    app.include_router(spectral.router)
    app.include_router(point.router)
    if os.environ.get("SPHEREX_MCP", "1") != "0":  # MCP endpoint for AI agents at /mcp (docs/mcp.md)
        from spherex_mcp.http import attach

        attach(app)
    if web_dist is not None and web_dist.exists():
        app.mount("/", WebFiles(directory=web_dist, html=True), name="web")
    return app
