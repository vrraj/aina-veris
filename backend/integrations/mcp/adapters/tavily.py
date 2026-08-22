"""Exact response normalization for Tavily's remote MCP server."""

from __future__ import annotations

import json
from typing import Any

from .contracts import NormalizedToolResult, ToolSource


def normalize(tool_name: str, raw_result: Any) -> NormalizedToolResult | None:
    """Normalize Tavily's documented `results` response without name-based routing."""
    if not isinstance(raw_result, dict):
        return None
    content = raw_result.get("content")
    if not isinstance(content, list):
        return None

    text = next(
        (
            item.get("text")
            for item in content
            if isinstance(item, dict)
            and item.get("type") == "text"
            and isinstance(item.get("text"), str)
        ),
        None,
    )
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        return None

    sources = []
    for item in payload["results"]:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        sources.append(
            ToolSource(
                title=str(item.get("title") or url).strip(),
                url=url,
                snippet=str(item.get("content") or "").strip(),
                provider="tavily",
            )
        )
    return NormalizedToolResult(data=json.dumps(payload, ensure_ascii=False), sources=sources)

