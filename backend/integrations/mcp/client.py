"""MCP (Model Context Protocol) client integration utilities."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from typing import Any, Awaitable, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from backend.core.config import settings
from backend.integrations.mcp.mcp_adapter import (
    MCPAdapterError,
    call_tool as adapter_call_tool,
    list_tools as adapter_list_tools,
)
from backend.integrations.mcp.adapters import normalize_mcp_result

logger = logging.getLogger(__name__)
_MCP_TOOLS_CACHE: Dict[str, List[Dict[str, Any]]] = {}
_MCP_RUNTIME_BY_TOOL: Dict[str, Dict[str, str]] = {}
_MCP_DEF_CACHE: Dict[str, Any] = {"timestamp": 0.0, "definitions": []}


def clear_mcp_tool_cache() -> int:
    """Clear discovered MCP schemas and runtimes so the catalog can be reloaded."""
    removed = (
        len(_MCP_TOOLS_CACHE)
        + len(_MCP_RUNTIME_BY_TOOL)
        + len(_MCP_DEF_CACHE.get("definitions") or [])
    )
    _MCP_TOOLS_CACHE.clear()
    _MCP_RUNTIME_BY_TOOL.clear()
    _MCP_DEF_CACHE["timestamp"] = 0.0
    _MCP_DEF_CACHE["definitions"] = []
    return removed


def get_cached_mcp_tool_definitions() -> List[Dict[str, Any]]:
    """Return discovered MCP definitions without making a network request."""
    return [dict(item) for item in (_MCP_DEF_CACHE.get("definitions") or []) if isinstance(item, dict)]


def resolve_mcp_server_url(server_name: str, server_config: Dict[str, Any]) -> str:
    """Return an MCP URL with any declaratively configured authentication applied."""
    url = str(server_config.get("url") or "").strip()
    if not url:
        raise ValueError(f"MCP server '{server_name}' has no URL configured")

    auth = server_config.get("auth")
    if auth is None:
        return url
    if not isinstance(auth, dict):
        raise ValueError(f"MCP server '{server_name}' auth must be an object")

    auth_type = str(auth.get("type") or "").strip()
    if auth_type != "query_parameter":
        raise ValueError(
            f"MCP server '{server_name}' has unsupported auth type '{auth_type}'"
        )

    parameter = str(auth.get("parameter") or "").strip()
    env_name = str(auth.get("env") or "").strip()
    if not parameter or not env_name:
        raise ValueError(
            f"MCP server '{server_name}' query_parameter auth requires parameter and env"
        )
    secret = os.getenv(env_name)
    if not secret:
        raise ValueError(
            f"MCP server '{server_name}' requires environment variable '{env_name}'"
        )

    parsed = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key != parameter]
    query.append((parameter, secret))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def merge_local_parameters(
    input_schema: Dict[str, Any] | None,
    local_parameters: Dict[str, Any] | None,
) -> Dict[str, Any]:
    """Add registry-defined local parameters to a discovered MCP schema."""
    schema = deepcopy(input_schema) if isinstance(input_schema, dict) else {"type": "object"}
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        properties = {}
        schema["properties"] = properties

    for parameter_name, parameter_config in (local_parameters or {}).items():
        if not isinstance(parameter_name, str) or not parameter_name.strip():
            continue
        if not isinstance(parameter_config, dict):
            continue
        properties[parameter_name] = {
            key: deepcopy(value)
            for key, value in parameter_config.items()
            if key != "forward_to_mcp"
        }
    return schema


def _mcp_arguments(
    arguments: Dict[str, Any] | None,
    local_parameters: Dict[str, Any] | None,
) -> Dict[str, Any]:
    """Remove declaratively local-only fields before calling the MCP server."""
    args = dict(arguments or {})
    local_only = {
        name
        for name, config in (local_parameters or {}).items()
        if isinstance(name, str)
        and isinstance(config, dict)
        and config.get("forward_to_mcp") is False
    }
    return {name: value for name, value in args.items() if name not in local_only}


def _run_coro_sync(coro: Awaitable[Any], *, timeout: float | None = None) -> Any:
    """Execute a coroutine from sync code, even if a loop is already running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(lambda: asyncio.run(coro))
        return future.result(timeout=timeout)


def _merge_runtime(tool_name: str, data: Dict[str, Any]) -> None:
    if not tool_name or not isinstance(data, dict):
        return
    current = dict(_MCP_RUNTIME_BY_TOOL.get(tool_name) or {})
    for key, value in data.items():
        if value is None:
            continue
        current[key] = value
    current.setdefault("local_tool", tool_name)
    current.setdefault("tool_name", tool_name)
    _MCP_RUNTIME_BY_TOOL[tool_name] = current


def _register_tool_runtime(
    tool_name: str,
    server_name: str,
    server_url: str,
    integration: str = "",
) -> None:
    if not tool_name:
        return
    _merge_runtime(
        tool_name,
        {
            "mcp_server": server_name,
            "mcp_url": server_url,
            "tool_name": tool_name,
            "mcp_integration": integration,
        },
    )


def register_registry_tool_runtime(
    tool_name: str,
    endpoint: Dict[str, Any],
    registry_entry: Dict[str, Any] | None = None,
) -> None:
    if not tool_name or not isinstance(endpoint, dict):
        return
    url = str(endpoint.get("url") or "").strip()
    if not url:
        return
    server_label = str(endpoint.get("server") or endpoint.get("label") or "registry").strip()
    remote_tool_name = str(endpoint.get("tool") or tool_name).strip() or tool_name
    artifact_cfg = None
    if isinstance(registry_entry, dict):
        artifact_cfg = registry_entry.get("artifact") if isinstance(registry_entry.get("artifact"), dict) else None
    _merge_runtime(
        tool_name,
        {
            "mcp_server": server_label,
            "mcp_url": url,
            "tool_name": remote_tool_name,
            "artifact_cfg": artifact_cfg,
            "local_parameters": (
                registry_entry.get("local_parameters")
                if isinstance(registry_entry, dict)
                else None
            ),
            "registry_entry": registry_entry,
        },
    )


def register_registry_tool_overlay(
    tool_name: str,
    registry_entry: Dict[str, Any] | None,
) -> None:
    """Apply YAML metadata to an already-discovered MCP tool without changing its URL."""
    if not tool_name or not isinstance(registry_entry, dict):
        return
    artifact_cfg = registry_entry.get("artifact")
    _merge_runtime(
        tool_name,
        {
            "artifact_cfg": artifact_cfg if isinstance(artifact_cfg, dict) else None,
            "local_parameters": registry_entry.get("local_parameters"),
            "registry_entry": registry_entry,
        },
    )


async def discover_mcp_tools(
    server_name: str,
    server_url: str,
    integration: str = "",
) -> List[Dict[str, Any]]:
    """Discover available tools from an MCP server."""
    if server_name in _MCP_TOOLS_CACHE:
        return _MCP_TOOLS_CACHE[server_name]

    try:
        tools_payload = await adapter_list_tools(server_url)
        raw_tools = []
        if isinstance(tools_payload, dict):
            raw_tools = tools_payload.get("tools") or []
        elif isinstance(tools_payload, list):
            raw_tools = tools_payload

        tools: List[Dict[str, Any]] = []
        for tool in raw_tools or []:
            if not isinstance(tool, dict):
                continue
            name = str(tool.get("name") or "").strip()
            if not name:
                continue
            tool_def = {
                "name": name,
                "description": tool.get("description", ""),
                "input_schema": tool.get("input_schema")
                or tool.get("inputSchema")
                or {"type": "object", "properties": {}},
            }
            tools.append(tool_def)
            _register_tool_runtime(name, server_name, server_url, integration)

        _MCP_TOOLS_CACHE[server_name] = tools
        logger.info("[MCP] Discovered %d tools from server '%s'", len(tools), server_name)
        return tools
    except MCPAdapterError as exc:
        logger.error("[MCP] Failed to discover tools from server '%s': %s", server_name, exc)
        return []
    except Exception as exc:
        logger.error("[MCP] Unexpected error discovering tools from server '%s': %s", server_name, exc)
        return []


async def call_mcp_tool(
    server_name: str,
    server_url: str,
    tool_name: str,
    arguments: Dict[str, Any] | None,
    *,
    tool_runtime: Dict[str, Any] | None = None,
) -> Any:
    """Execute a tool on an MCP server."""
    runtime_meta = tool_runtime or {}
    local_parameters = runtime_meta.get("local_parameters")
    args = _mcp_arguments(arguments, local_parameters)
    chart_type = arguments.get("chart_type") if isinstance(arguments, dict) else None
    adapter_url = str(runtime_meta.get("mcp_url") or server_url or "").strip()
    target_tool_name = str(runtime_meta.get("tool_name") or tool_name or "").strip()
    placeholder_override = ""
    artifact_cfg = runtime_meta.get("artifact_cfg")
    if isinstance(artifact_cfg, dict):
        placeholder_override = str(artifact_cfg.get("placeholder") or "").strip()
    if not adapter_url:
        raise ValueError("MCP runtime missing URL")

    try:
        result = await adapter_call_tool(adapter_url, target_tool_name, args)
    except MCPAdapterError as exc:
        logger.error(
            "[MCP] Failed to call tool '%s' on server '%s': %s",
            target_tool_name,
            runtime_meta.get("mcp_server") or server_name,
            exc,
        )
        raise
    except Exception as exc:
        logger.error(
            "[MCP] Unexpected error calling tool '%s' on server '%s': %s",
            target_tool_name,
            runtime_meta.get("mcp_server") or server_name,
            exc,
        )
        raise

    normalized = normalize_mcp_result(
        str(runtime_meta.get("mcp_integration") or "").strip(),
        target_tool_name,
        result,
        chart_type=chart_type,
        artifact_placeholder=placeholder_override,
    )
    if normalized is not None:
        return normalized

    content = None
    if isinstance(result, dict):
        content = result.get("content")

    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_val = item.get("text")
                if isinstance(text_val, str):
                    return text_val
        return str(content)

    if isinstance(content, str):
        return content

    return str(result)


async def _discover_enabled_servers(mcp_servers: Dict[str, Any]) -> List[Dict[str, Any]]:
    definitions: List[Dict[str, Any]] = []
    for server_name, server_config in (mcp_servers or {}).items():
        if not isinstance(server_config, dict) or not server_config.get("enabled"):
            continue
        transport = str(server_config.get("transport") or "").strip()
        if transport != "streamable_http":
            logger.warning("[MCP] Server '%s' has unsupported transport '%s'", server_name, transport)
            continue
        try:
            server_url = resolve_mcp_server_url(server_name, server_config)
        except ValueError as exc:
            logger.warning("[MCP] %s", exc)
            continue

        integration = str(server_config.get("integration") or "").strip()
        tools = await discover_mcp_tools(server_name, server_url, integration)
        for tool in tools:
            definitions.append(
                {
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "input_schema": tool.get("input_schema", {"type": "object", "properties": {}}),
                    "runtime": {
                        "mcp_server": server_name,
                        "mcp_url": server_url,
                        "mcp_integration": integration,
                    },
                }
            )
    return definitions


def get_mcp_tool_definitions(mcp_servers: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return MCP tool definitions (sync wrapper)."""
    if not isinstance(mcp_servers, dict) or not mcp_servers:
        return []
    ttl = max(1, int(getattr(settings, "mcp_tools_refresh", 300) or 300))
    now = time.time()
    cached_ts = float(_MCP_DEF_CACHE.get("timestamp") or 0.0)
    cached_defs = _MCP_DEF_CACHE.get("definitions") or []
    if cached_defs and (now - cached_ts) < ttl:
        return cached_defs

    try:
        definitions = _run_coro_sync(_discover_enabled_servers(mcp_servers))
        _MCP_DEF_CACHE["definitions"] = definitions
        _MCP_DEF_CACHE["timestamp"] = now
        return definitions
    except Exception as exc:
        logger.error("[MCP] Failed to load MCP tool definitions: %s", exc)
        return []


def get_mcp_runtime_for_tool(tool_name: str) -> Optional[Dict[str, Any]]:
    runtime = _MCP_RUNTIME_BY_TOOL.get(tool_name)
    if runtime:
        return dict(runtime)
    return None


def call_mcp_tool_sync(
    server_name: str,
    server_url: str,
    tool_name: str,
    arguments: Dict[str, Any] | None,
    *,
    timeout: float | None = 30.0,
    tool_runtime: Dict[str, Any] | None = None,
) -> Any:
    """Synchronously execute an MCP tool, handling event loop state."""
    return _run_coro_sync(
        call_mcp_tool(
            server_name,
            server_url,
            tool_name,
            arguments or {},
            tool_runtime=tool_runtime,
        ),
        timeout=timeout,
    )


async def _close_all_sessions() -> None:
    clear_mcp_tool_cache()


def close_mcp_sessions() -> None:
    """Synchronously close all MCP sessions."""
    try:
        _run_coro_sync(_close_all_sessions())
    except Exception as exc:
        logger.error("[MCP] Failed to close MCP sessions: %s", exc)
