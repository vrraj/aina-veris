"""Exact response normalization for the AGIS Markets MCP server."""

from __future__ import annotations

from typing import Any

from backend.integrations.mcp.price_history import (
    build_artifact_output,
    extract_price_history_from_mcp_response,
    synthesize_price_history_svg,
)

from .contracts import NormalizedToolResult


def normalize(tool_name: str, raw_result: Any, *, chart_type: str | None = None, placeholder: str = "") -> NormalizedToolResult | None:
    """Turn documented stock-history structured content into a chart artifact."""
    if tool_name != "get_stock_price_history" or not isinstance(raw_result, dict):
        return None
    structured = raw_result.get("structuredContent") or raw_result.get("structured_content")
    if not isinstance(structured, (dict, list)):
        return None
    extracted = extract_price_history_from_mcp_response(structured)
    items = extracted.get("items") or []
    if not items:
        return NormalizedToolResult(data=structured)

    symbol = str(items[0].get("symbol") or "UNKNOWN")
    svg, metadata = synthesize_price_history_svg(extracted, symbol, chart_type=chart_type)
    if not svg:
        return NormalizedToolResult(data=structured)
    data = build_artifact_output(
        svg,
        metadata,
        tool_name,
        placeholder=placeholder or None,
    )
    data["_svg_artifact"] = svg
    data["structured_payload"] = structured
    return NormalizedToolResult(data=data)

