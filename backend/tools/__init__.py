"""
Lightweight tool registry for Responses API-style tools.

Each tool module should export two callables:
- tool_definition() -> dict  # Returns a Responses API tool definition
- run(args: dict, chat_context: list[dict], **kwargs) -> str | dict  # Executes the tool

This package exposes helpers to list tool specs and retrieve executors
without wiring anything into chat_manager yet.
"""

from typing import Any, Callable, Dict, List
import logging
import time

# Import tool modules here to register them
from . import get_weather as _get_weather
from . import web_search as _web_search
from . import get_nearby_airports as _get_nearby_airports
# get_stock_price_history removed - using MCP tool instead
from backend.core.config import settings
from backend.chat.pipeline.tools import _load_tool_registry
from backend.integrations.mcp.client import (
    clear_mcp_tool_cache,
    get_cached_mcp_tool_definitions,
    get_mcp_tool_definitions,
    merge_local_parameters,
    register_registry_tool_overlay,
)
from backend.integrations.mcp import executor as _mcp_tool_executor

logger = logging.getLogger(__name__)

_AVAILABLE_TOOL_MODULES: Dict[str, Any] = {
    _get_weather.TOOL_NAME: _get_weather,
    _web_search.TOOL_NAME: _web_search,
    _get_nearby_airports.TOOL_NAME: _get_nearby_airports,
    # _get_stock_price_history removed - using MCP tool instead
}

_TOOL_LIST_CACHE: Dict[str, Any] = {"timestamp": 0.0, "tools": []}


def clear_tool_list_cache() -> int:
    """Clear the assembled model-tool catalog."""
    removed = len(_TOOL_LIST_CACHE.get("tools") or [])
    _TOOL_LIST_CACHE["timestamp"] = 0.0
    _TOOL_LIST_CACHE["tools"] = []
    return removed


def _flatten_tool_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize any tool spec into flattened Responses API format."""
    if not isinstance(spec, dict):
        raise TypeError("tool spec must be a dict")
    if "function" in spec and isinstance(spec["function"], dict):
        fn = spec["function"]
        return {
            "type": spec.get("type", "function"),
            "name": fn.get("name"),
            "description": fn.get("description"),
            "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
        }
    # assume already flattened
    return {
        "type": spec.get("type", "function"),
        "name": spec.get("name"),
        "description": spec.get("description"),
        "parameters": spec.get("parameters") or {"type": "object", "properties": {}},
    }


def list_tools() -> List[Dict[str, Any]]:
    """Return tool definitions ready for `tools=[...]` in the Responses API."""
    cached_tools = _TOOL_LIST_CACHE.get("tools") or []
    if cached_tools:
        return cached_tools

    try:
        registry = _load_tool_registry(settings)
    except Exception as exc:
        logger.error("[TOOLS] Failed to load tool registry: %s", exc)
        registry = {}

    entries: List[Dict[str, Any]] = []
    if isinstance(registry, dict):
        entries = [
            item for item in registry.get("tool_entries") or [] if isinstance(item, dict)
        ]

    tools: List[Dict[str, Any]] = []
    local_tool_names: List[str] = []
    for entry in entries:
        if not entry.get("enabled", True):
            continue
        name = str(entry.get("name") or "").strip()
        runtime_cfg = entry.get("runtime") if isinstance(entry.get("runtime"), dict) else {}
        endpoint_cfg = runtime_cfg.get("endpoint") if isinstance(runtime_cfg.get("endpoint"), dict) else {}
        endpoint_type = str(endpoint_cfg.get("type") or "").strip().lower()

        if endpoint_type == "mcp" or not endpoint_type:
            continue

        module = _AVAILABLE_TOOL_MODULES.get(name)
        if not module:
            logger.warning("[TOOLS] Skipping unregistered tool '%s' in registry", name)
            continue
        try:
            tools.append(_flatten_tool_spec(module.tool_definition()))
            local_tool_names.append(name)
            logger.info(
                "[TOOLS] registered_tool origin=local name=%s description=%s",
                name,
                (entry.get("description") or ""),
            )
        except Exception as exc:
            logger.error("[TOOLS] Failed to load definition for tool '%s': %s", name, exc)

    mcp_servers = registry.get("mcp_servers") if isinstance(registry, dict) else {}
    mcp_defs = (
        get_mcp_tool_definitions(mcp_servers)
        if isinstance(mcp_servers, dict) and mcp_servers
        else []
    )
    mcp_tool_names: List[str] = []
    for mcp_tool in mcp_defs or []:
        if not isinstance(mcp_tool, dict):
            continue
        name = str(mcp_tool.get("name") or "").strip()
        registry_entry = (
            registry.get("tools_by_name", {}).get(name)
            if isinstance(registry.get("tools_by_name"), dict)
            else None
        )
        if isinstance(registry_entry, dict) and registry_entry.get("enabled") is False:
            logger.info("[TOOLS] skipped_tool origin=mcp name=%s reason=disabled_overlay", name)
            continue
        register_registry_tool_overlay(name, registry_entry)
        local_parameters = (
            registry_entry.get("local_parameters")
            if isinstance(registry_entry, dict)
            else None
        )
        parameters = merge_local_parameters(
            mcp_tool.get("input_schema"),
            local_parameters,
        )
        tools.append(
            {
                "type": "function",
                "name": mcp_tool.get("name", ""),
                "description": (
                    registry_entry.get("description")
                    if isinstance(registry_entry, dict) and str(registry_entry.get("description") or "").strip()
                    else mcp_tool.get("description", "")
                ),
                "parameters": parameters,
            }
        )
        if name:
            mcp_tool_names.append(name)
            logger.info(
                "[TOOLS] registered_tool origin=mcp name=%s description=%s",
                name,
                mcp_tool.get("description", ""),
            )

    logger.info(
        "[TOOLS] list_tools refresh local=%s mcp=%s total=%d",
        local_tool_names,
        mcp_tool_names,
        len(tools),
    )
    _TOOL_LIST_CACHE["timestamp"] = time.time()
    _TOOL_LIST_CACHE["tools"] = tools
    return tools


def refresh_tool_catalog() -> Dict[str, Any]:
    """Reload registry, MCP discovery, and assembled tools as one explicit operation."""
    from backend.chat.pipeline.tools import clear_tool_registry_cache

    registry_removed = clear_tool_registry_cache()
    mcp_removed = clear_mcp_tool_cache()
    tool_removed = clear_tool_list_cache()
    tools = list_tools()
    return {
        "tools": tools,
        "registry_cache_entries_cleared": registry_removed,
        "mcp_cache_entries_cleared": mcp_removed,
        "tool_list_entries_cleared": tool_removed,
    }


def get_discovered_mcp_tools() -> List[Dict[str, str]]:
    """Expose cached MCP tools with their source server for administration UI."""
    return [
        {
            "name": str(definition.get("name") or ""),
            "description": str(definition.get("description") or ""),
            "source": str((definition.get("runtime") or {}).get("mcp_server") or ""),
            "source_type": "mcp",
        }
        for definition in get_cached_mcp_tool_definitions()
        if str(definition.get("name") or "").strip()
    ]


def get_executor(name: str, mcp_runtime: Dict[str, Any] | None = None) -> Callable[..., Any] | None:
    """Return the executor function for a tool by name, or None if not found."""
    # Check for MCP tools first
    if mcp_runtime and isinstance(mcp_runtime, dict):
        runtime_payload = dict(mcp_runtime)
        runtime_payload.setdefault("tool_name", name)
        runtime_payload.setdefault("mcp_server", runtime_payload.get("local_tool") or name)
        if runtime_payload.get("mcp_server") and runtime_payload.get("mcp_url"):
            def mcp_executor(
                args: Dict[str, Any] | None = None,
                chat_context: List[Dict[str, str]] | None = None,
                **kwargs: Any,
            ) -> Dict[str, Any] | Any:
                return _mcp_tool_executor.run(
                    args,
                    chat_context,
                    tool_runtime=dict(runtime_payload),
                    **kwargs,
                )

            return mcp_executor
    
    module = _AVAILABLE_TOOL_MODULES.get(name)
    if module:
        return module.run
    return None


__all__ = [
    "list_tools",
    "get_executor",
    "refresh_tool_catalog",
    "get_discovered_mcp_tools",
]
