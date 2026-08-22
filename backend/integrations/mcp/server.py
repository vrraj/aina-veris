"""Stdio MCP server for Aina-Veris domain-scoped research tools."""

from __future__ import annotations

import asyncio

from mcp.server import NotificationOptions, Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolRequestParams, ListToolsRequest

from backend.integrations.mcp.research_server import handle_call_tool, handle_list_tools

server = Server("aina-veris")
server.add_request_handler("tools/list", ListToolsRequest, handle_list_tools)
server.add_request_handler("tools/call", CallToolRequestParams, handle_call_tool)


async def run() -> None:
    """Serve the same research tools over MCP stdio."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(NotificationOptions()),
        )


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
