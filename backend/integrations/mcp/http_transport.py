"""Streamable HTTP transport for the public Aina-Veris MCP research tools."""

from __future__ import annotations

import contextlib

from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import CallToolRequestParams, ListToolsRequest

from backend.integrations.mcp.research_server import handle_call_tool, handle_list_tools

_server = Server("aina-veris")
_server.add_request_handler("tools/list", ListToolsRequest, handle_list_tools)
_server.add_request_handler("tools/call", CallToolRequestParams, handle_call_tool)

_session_manager = StreamableHTTPSessionManager(app=_server, stateless=False)


class MCPASGIMiddleware:
    """Delegate ``/mcp`` requests to the Streamable HTTP session manager."""

    def __init__(self, app, *, mcp_handler):
        self.app = app
        self.mcp_handler = mcp_handler

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path") in {"/mcp", "/mcp/"}:
            normalized_scope = dict(scope)
            normalized_scope["path"] = "/mcp"
            normalized_scope["raw_path"] = b"/mcp"
            await self.mcp_handler(normalized_scope, receive, send)
            return
        await self.app(scope, receive, send)


@contextlib.asynccontextmanager
async def lifespan(_app):
    """Run the MCP session manager for the FastAPI application lifetime."""
    async with _session_manager.run():
        yield


def create_mcp_http_app():
    """Return the ASGI handler mounted by the FastAPI application at ``/mcp``."""

    async def asgi_app(scope, receive, send):
        await _session_manager.handle_request(scope, receive, send)

    return asgi_app
