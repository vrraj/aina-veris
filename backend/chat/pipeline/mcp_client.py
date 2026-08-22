"""MCP (Model Context Protocol) client integration for tool discovery and execution."""

import asyncio
import logging
from typing import Any, Dict, List, Optional
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

logger = logging.getLogger(__name__)

_MCP_SESSIONS: Dict[str, ClientSession] = {}
_MCP_TOOLS_CACHE: Dict[str, List[Dict[str, Any]]] = {}


async def _get_mcp_session(server_name: str, server_url: str) -> ClientSession:
    """Get or create an MCP session for a given server."""
    if server_name in _MCP_SESSIONS:
        return _MCP_SESSIONS[server_name]
    
    try:
        read, write, _ = await streamable_http_client(server_url).__aenter__()
        session = ClientSession(read, write)
        await session.__aenter__()
        await session.initialize()
        _MCP_SESSIONS[server_name] = session
        logger.info(f"[MCP] Connected to server '{server_name}' at {server_url}")
        return session
    except Exception as e:
        logger.error(f"[MCP] Failed to connect to server '{server_name}': {e}")
        raise


async def discover_mcp_tools(server_name: str, server_url: str) -> List[Dict[str, Any]]:
    """Discover available tools from an MCP server."""
    if server_name in _MCP_TOOLS_CACHE:
        return _MCP_TOOLS_CACHE[server_name]
    
    try:
        session = await _get_mcp_session(server_name, server_url)
        tools_response = await session.list_tools()
        tools = []
        
        for tool in tools_response.tools:
            tools.append({
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": tool.inputSchema or {},
            })
        
        _MCP_TOOLS_CACHE[server_name] = tools
        logger.info(f"[MCP] Discovered {len(tools)} tools from server '{server_name}'")
        return tools
    except Exception as e:
        logger.error(f"[MCP] Failed to discover tools from server '{server_name}': {e}")
        return []


async def call_mcp_tool(
    server_name: str,
    server_url: str,
    tool_name: str,
    arguments: Dict[str, Any],
) -> Any:
    """Execute a tool on an MCP server."""
    try:
        session = await _get_mcp_session(server_name, server_url)
        result = await session.call_tool(tool_name, arguments)
        
        if hasattr(result, 'structuredContent'):
            return result.structuredContent
        elif hasattr(result, 'content'):
            return result.content
        else:
            return str(result)
    except Exception as e:
        logger.error(f"[MCP] Failed to call tool '{tool_name}' on server '{server_name}': {e}")
        raise


def get_mcp_tool_definitions(mcp_servers: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Get tool definitions for all enabled MCP servers (synchronous wrapper)."""
    if not isinstance(mcp_servers, dict):
        return []
    
    definitions = []
    for server_name, server_config in mcp_servers.items():
        if not isinstance(server_config, dict):
            continue
        if not server_config.get("enabled", False):
            continue
        
        server_url = server_config.get("url", "")
        if not server_url:
            logger.warning(f"[MCP] Server '{server_name}' has no URL configured")
            continue
        
        transport = server_config.get("transport", "")
        if transport != "streamable_http":
            logger.warning(f"[MCP] Server '{server_name}' has unsupported transport: {transport}")
            continue
        
        # Run async discovery in event loop
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If loop is running, create a task
                task = asyncio.create_task(discover_mcp_tools(server_name, server_url))
                # For now, return empty and let discovery happen later
                # In production, you'd want proper async handling
                logger.info(f"[MCP] Scheduled tool discovery for server '{server_name}'")
            else:
                tools = asyncio.run(discover_mcp_tools(server_name, server_url))
                for tool in tools:
                    definitions.append({
                        "name": tool["name"],
                        "description": tool["description"],
                        "input_schema": tool["input_schema"],
                        "mcp_server": server_name,
                        "mcp_url": server_url,
                    })
        except Exception as e:
            logger.error(f"[MCP] Failed to get tool definitions for server '{server_name}': {e}")
    
    return definitions


async def execute_mcp_tool_sync(
    server_name: str,
    server_url: str,
    tool_name: str,
    arguments: Dict[str, Any],
) -> Any:
    """Synchronous wrapper for MCP tool execution."""
    return await call_mcp_tool(server_name, server_url, tool_name, arguments)


def close_mcp_sessions():
    """Close all MCP sessions."""
    for server_name, session in _MCP_SESSIONS.items():
        try:
            asyncio.create_task(session.__aexit__(None, None, None))
            logger.info(f"[MCP] Closed session for server '{server_name}'")
        except Exception as e:
            logger.error(f"[MCP] Failed to close session for server '{server_name}': {e}")
    _MCP_SESSIONS.clear()
    _MCP_TOOLS_CACHE.clear()
