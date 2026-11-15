"""stdio transport: `python -m spherex_mcp` (Claude Code: `claude mcp add spherex -- python -m spherex_mcp`).

`python -m spherex_mcp --http [--port 8765]` serves streamable HTTP on its own instead.
"""

import argparse

from spherex_core.runtime import setup_logging

from .server import build_server


def main() -> None:
    ap = argparse.ArgumentParser(description="SPHEREx discovery MCP server")
    ap.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    mcp = build_server()
    if a.http:
        import uvicorn

        from .http import standalone_app

        setup_logging()
        uvicorn.run(standalone_app(mcp), host=a.host, port=a.port)
    else:
        mcp.run("stdio")  # logs go to stderr; stdout carries the protocol


if __name__ == "__main__":
    main()
