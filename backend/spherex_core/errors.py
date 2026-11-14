"""Exceptions shared by the library and its adapters (HTTP API, MCP server)."""

from __future__ import annotations


class NotFound(LookupError):
    """An unknown or not-yet-available resource. The message is safe to show to users."""
