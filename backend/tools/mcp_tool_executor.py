"""Generic MCP tool executor for tools discovered from MCP servers."""

from __future__ import annotations

from typing import Any, Dict, List
import logging
import asyncio

from backend.chat.pipeline.mcp_client import call_mcp_tool

logger = logging.getLogger(__name__)


def tool_definition(tool_name: str, tool_schema: Dict[str, Any]) -> Dict[str, Any]:
    """Generate tool definition from MCP tool schema."""
    return {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": tool_schema.get("description", ""),
            "parameters": tool_schema.get("input_schema", {}),
        },
    }


def run(
    args: Dict[str, Any] | None,
    chat_context: List[Dict[str, str]] | None = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Execute an MCP tool."""
    _ = chat_context
    args = args or {}

    tool_runtime = kwargs.get("tool_runtime", {})
    if not isinstance(tool_runtime, dict):
        tool_runtime = {}

    mcp_server = tool_runtime.get("mcp_server", "")
    mcp_url = tool_runtime.get("mcp_url", "")
    tool_name = tool_runtime.get("tool_name", "")

    if not mcp_server or not mcp_url or not tool_name:
        return {
            "error": "MCP tool runtime configuration missing mcp_server, mcp_url, or tool_name",
        }

    try:
        # Run async MCP call in event loop
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If loop is running, we need to use asyncio.create_task
            # This is a simplification - in production you'd want proper async handling
            result = asyncio.run_coroutine_threadsafe(
                call_mcp_tool(mcp_server, mcp_url, tool_name, args),
                loop,
            ).result(timeout=30)
        else:
            result = asyncio.run(call_mcp_tool(mcp_server, mcp_url, tool_name, args))

        logger.info(
            "[MCP_TOOL] executed tool=%s server=%s result_type=%s",
            tool_name,
            mcp_server,
            type(result).__name__,
        )

        return result
    except Exception as ex:
        logger.error(
            "[MCP_TOOL] failed tool=%s server=%s error=%s",
            tool_name,
            mcp_server,
            ex,
            exc_info=True,
        )
        return {
            "error": f"MCP tool execution failed: {ex}",
        }
