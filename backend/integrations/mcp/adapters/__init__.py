"""Server-level MCP result adapters and their stable output contract."""

from __future__ import annotations

from typing import Any

from . import agis_markets, tavily
from .contracts import NormalizedToolResult, ToolSource


def normalize_mcp_result(
    integration: str,
    tool_name: str,
    raw_result: Any,
    *,
    chart_type: str | None = None,
    artifact_placeholder: str = "",
) -> NormalizedToolResult | None:
    """Normalize only known, configured server integrations."""
    if integration == "tavily":
        return tavily.normalize(tool_name, raw_result)
    if integration == "agis_markets":
        return agis_markets.normalize(
            tool_name,
            raw_result,
            chart_type=chart_type,
            placeholder=artifact_placeholder,
        )
    return None


__all__ = ["NormalizedToolResult", "ToolSource", "normalize_mcp_result"]
