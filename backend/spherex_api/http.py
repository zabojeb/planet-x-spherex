"""HTTP helpers shared by the routes: client identity, immutable caching, 429s, cancel-on-disconnect."""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from fastapi import HTTPException, Request
from fastapi.responses import Response

IMMUTABLE = "public, max-age=31536000, immutable"  # finished results: the URL contains the content hash
SHORT = "public, max-age=3600"  # reference data that changes rarely (catalog cones, presets)


def client_ip(request: Request) -> str | None:
    """First address in X-Forwarded-For (Railway, nginx) or the socket peer."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def client_id(request: Request) -> str | None:
    """Per-tab id from the X-Client-Id header (or ?client=), falling back to the IP."""
    cid = request.headers.get("x-client-id") or request.query_params.get("client")
    return (cid[:64] if cid else None) or client_ip(request)


def not_modified(request: Request, etag: str) -> bool:
    inm = request.headers.get("if-none-match")
    return bool(inm) and (inm == "*" or etag in [t.strip() for t in inm.split(",")])


def cached(request: Request, etag: str, body: bytes | None = None, *, media_type: str = "application/json",
           cache: str = IMMUTABLE, headers: dict | None = None) -> Response:
    """200 with ETag + Cache-Control, or 304 when the client already has this ETag."""
    h = {"ETag": etag, "Cache-Control": cache, **(headers or {})}
    if not_modified(request, etag):
        return Response(status_code=304, headers=h)
    return Response(body or b"", media_type=media_type, headers=h)


def too_many(e: Exception) -> HTTPException:
    return HTTPException(429, str(e), headers={"Retry-After": str(getattr(e, "retry_after", 30))})


async def run_until_disconnect(request: Request, fn: Callable[..., Any], *args: Any, poll: float = 0.5) -> Any:
    """Run a blocking function in a thread; if the client goes away first, stop waiting and answer 499.

    The thread itself cannot be killed, but the worker slot and the response are released immediately and
    the remote call ends on its own timeout."""
    fut = asyncio.ensure_future(asyncio.to_thread(fn, *args))
    while True:
        done, _ = await asyncio.wait({fut}, timeout=poll)
        if done:
            return fut.result()
        if await request.is_disconnected():
            fut.cancel()
            raise HTTPException(499, "client closed request")
