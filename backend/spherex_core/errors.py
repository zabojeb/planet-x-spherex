"""Exceptions shared by the library and its adapters (HTTP API, MCP server)."""

from __future__ import annotations


class NotFound(LookupError):
    """An unknown or not-yet-available resource. The message is safe to show to users."""


class UpstreamError(RuntimeError):
    """A remote service (IRSA, ESA Gaia, CDS) failed, timed out or answered with something unusable.
    The message is safe to show to users; adapters map it to HTTP 502."""
