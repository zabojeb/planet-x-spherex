"""FastAPI application factory: middleware, routes, and the built web client as static files."""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from spherex_core import __version__, logs, services
from spherex_core.config import settings

from .routes import catalogs, sequences, targets

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"
log = logging.getLogger("spherex_api.http")
_SEQ_PATH = re.compile(r"^/api/sequences/([A-Za-z0-9]+)")


def create_app(web_dist: Path | None = WEB_DIST) -> FastAPI:
    logs.configure()
    app = FastAPI(title="Planet X × SPHEREx API", version=__version__,
                  description="Blink SPHEREx infrared images over time: search, aligned epoch co-adds, spectra.")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.add_middleware(GZipMiddleware, minimum_size=2048)

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        """One line per API request: method, path, status, seconds, response size. Progress polls (202) and
        static files go to DEBUG so a waiting browser does not flood the log."""
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
        return services.health()

    app.include_router(targets.router)
    app.include_router(sequences.router)
    app.include_router(catalogs.router)
    if web_dist is not None and web_dist.exists():
        app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")
    return app
