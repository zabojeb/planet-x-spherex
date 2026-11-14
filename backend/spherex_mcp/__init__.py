"""MCP server that lets an AI agent search SPHEREx for new moving and variable objects.

Transports: stdio (`python -m spherex_mcp`) and streamable HTTP mounted at /mcp in the FastAPI app.
"""

from .server import build_server

__all__ = ["build_server"]
