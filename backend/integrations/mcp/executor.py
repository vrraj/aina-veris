"""Generic executor for MCP-discovered tools."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from backend.integrations.mcp.client import call_mcp_tool_sync

logger = logging.getLogger(__name__)


def tool_definition(tool_name: str, tool_schema: Dict[str, Any]) -> Dict[str, Any]:
    """Generate tool definition from an MCP tool schema."""
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
) -> Dict[str, Any] | Any:
    """Execute an MCP tool synchronously."""
    _ = chat_context
    tool_runtime = kwargs.get("tool_runtime") or {}
    mcp_server = tool_runtime.get("mcp_server") or ""
    mcp_url = tool_runtime.get("mcp_url") or ""
    tool_name = tool_runtime.get("tool_name") or ""

    if not (mcp_server and mcp_url and tool_name):
        return {
            "error": "MCP tool runtime configuration missing mcp_server, mcp_url, or tool_name",
        }

    try:
        result = call_mcp_tool_sync(
            mcp_server,
            mcp_url,
            tool_name,
            args or {},
            tool_runtime=tool_runtime,
        )
        logger.info(
            "[MCP_TOOL] executed tool=%s server=%s result_type=%s",
            tool_name,
            mcp_server,
            type(result).__name__,
        )
        return result
    except Exception as exc:
        logger.error(
            "[MCP_TOOL] failed tool=%s server=%s error=%s",
            tool_name,
            mcp_server,
            exc,
            exc_info=True,
        )
        return {
            "error": f"MCP tool execution failed: {exc}",
        }
