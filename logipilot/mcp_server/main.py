"""MCP server exposing the LogiPilot tools over streamable HTTP (path /mcp)."""

import os

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from logipilot.common.tools_client import TOOLS

mcp = FastMCP("logipilot-tools", host="0.0.0.0", port=int(os.getenv("PORT", "8091")))

# Register the client functions directly so names, signatures and docstrings stay identical.
for _tool in TOOLS:
    mcp.tool()(_tool)


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    """Report MCP server liveness."""
    return JSONResponse({"ok": True})


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
