"""HTTP adapter for invoking MCP servers via JSON-RPC."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

logger = logging.getLogger(__name__)
_JSONRPC_VERSION = "2.0"
_DEFAULT_TIMEOUT = 30.0


def _redact_url(url: str) -> str:
    """Keep credential-bearing query-string values out of application logs."""
    parsed = urlsplit(url)
    if not parsed.query:
        return url
    query = [(key, "[redacted]") for key, _ in parse_qsl(parsed.query, keep_blank_values=True)]
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


class MCPAdapterError(RuntimeError):
    """Raised when an MCP HTTP call fails."""


async def _post_jsonrpc(
    url: str,
    payload: Dict[str, Any],
    *,
    timeout: float = _DEFAULT_TIMEOUT,
) -> Dict[str, Any] | List[Any] | Any:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, json=payload, headers=headers)
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        logger.error("[MCP_ADAPTER] HTTP %s calling %s", status_code, _redact_url(url))
        raise MCPAdapterError(f"MCP server returned HTTP {status_code}") from None

    return _decode_response(response, url)


def _unwrap_result(payload: Any) -> Any:
    if isinstance(payload, dict):
        if payload.get("error") is not None:
            raise MCPAdapterError(str(payload.get("error")))
        if payload.get("result") is not None:
            return payload.get("result")
    return payload


def _build_jsonrpc_payload(method: str, params: Dict[str, Any] | None, *, request_id: str | None = None) -> Dict[str, Any]:
    return {
        "jsonrpc": _JSONRPC_VERSION,
        "id": request_id or uuid.uuid4().hex,
        "method": method,
        "params": params or {},
    }


def _decode_response(response: httpx.Response, url: str) -> Dict[str, Any] | List[Any] | Any:
    """Decode either a JSON response or a JSON-RPC message carried over SSE."""
    try:
        if "text/event-stream" in response.headers.get("content-type", "").lower():
            for line in response.text.splitlines():
                if line.startswith("data:"):
                    return json.loads(line.removeprefix("data:").strip())
            raise json.JSONDecodeError("SSE response did not include a data event", response.text, 0)
        return response.json()
    except json.JSONDecodeError as exc:
        logger.error("[MCP_ADAPTER] Failed to decode MCP response from %s: %s", _redact_url(url), exc)
        raise MCPAdapterError("Invalid MCP response") from None


async def call_tool(
    url: str,
    tool_name: str,
    arguments: Dict[str, Any] | None,
    *,
    timeout: float = _DEFAULT_TIMEOUT,
    request_id: str | None = None,
) -> Dict[str, Any] | Any:
    payload = _build_jsonrpc_payload(
        "tools/call",
        {
            "name": tool_name,
            "arguments": arguments or {},
        },
        request_id=request_id,
    )
    response = await _post_jsonrpc(url, payload, timeout=timeout)
    data = _unwrap_result(response)
    logger.debug(
        "[MCP_ADAPTER] call_tool tool=%s url=%s response_keys=%s",
        tool_name,
        _redact_url(url),
        list(data.keys()) if isinstance(data, dict) else type(data).__name__,
    )
    return data


async def list_tools(
    url: str,
    *,
    timeout: float = _DEFAULT_TIMEOUT,
    request_id: str | None = None,
) -> Dict[str, Any] | List[Any] | Any:
    payload = _build_jsonrpc_payload("tools/list", {}, request_id=request_id)
    response = await _post_jsonrpc(url, payload, timeout=timeout)
    data = _unwrap_result(response)
    logger.debug(
        "[MCP_ADAPTER] list_tools url=%s keys=%s",
        _redact_url(url),
        list(data.keys()) if isinstance(data, dict) else type(data).__name__,
    )
    return data


__all__ = [
    "MCPAdapterError",
    "call_tool",
    "list_tools",
]
