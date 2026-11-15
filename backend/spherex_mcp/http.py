"""Streamable-HTTP transport mounted inside the FastAPI app at /mcp (stateless sessions)."""

from __future__ import annotations

import contextlib
import logging

log = logging.getLogger(__name__)
PATH = "/mcp"


def _http_app(mcp, path: str):
    try:  # mcp 2.x: transport settings are arguments; drop the localhost-only Host check for a public site
        from mcp.server.transport_security import TransportSecuritySettings

        return mcp.streamable_http_app(
            streamable_http_path=path, stateless_http=True,
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    except TypeError:  # mcp 1.x: settings live on the server object
        mcp.settings.streamable_http_path = path
        mcp.settings.stateless_http = True
        return mcp.streamable_http_app()


def attach(app) -> bool:
    """Add the MCP endpoint to a FastAPI app and run its session manager in the app's lifespan.
    Returns False (and logs) when the `mcp` package is missing, so the API still starts."""
    try:
        from .server import build_server
    except ImportError as e:
        log.warning("MCP endpoint disabled: %s", e)
        return False
    mcp = build_server()
    sub = _http_app(mcp, PATH)
    app.router.routes.extend(sub.routes)  # the route itself, so /mcp works without a trailing-slash redirect
    previous = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(a):
        async with mcp.session_manager.run():
            async with previous(a) as state:
                yield state

    app.router.lifespan_context = lifespan
    return True


def standalone_app(mcp):
    return _http_app(mcp, PATH)
