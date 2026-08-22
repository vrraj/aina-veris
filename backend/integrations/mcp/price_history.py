"""Price history extraction and SVG synthesis for MCP tools."""

from typing import Any, Dict, List, Tuple
import logging

logger = logging.getLogger(__name__)


def extract_price_history_from_mcp_response(structured_content: Dict[str, Any]) -> Dict[str, Any]:
    """Extract price history data from MCP structuredContent response.
    
    Args:
        structured_content: The structuredContent field from MCP response
        
    Returns:
        Dict with extracted data or empty dict if not found
    """
    if isinstance(structured_content, list):
        candidates = structured_content
    elif isinstance(structured_content, dict):
        candidates = [structured_content]
    else:
        return {}

    price_history = None
    period = "1M"
    symbols: List[str] = []
    items: List[Dict[str, Any]] | None = None
    as_of = None

    for entry in candidates:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") != "json":
            continue
        price_history = entry.get("priceHistory")
        if isinstance(price_history, dict):
            period = price_history.get("period", period)
            symbols = price_history.get("symbols", symbols)
            if isinstance(price_history.get("items"), list):
                items = price_history.get("items")
            as_of = price_history.get("as_of", as_of)
            break

    if not isinstance(price_history, dict):
        return {}
    
    if not price_history.get("success"):
        return {}

    if not isinstance(items, list) or not items:
        return {}
    
    return {
        "period": period,
        "symbols": symbols,
        "items": items,
        "as_of": as_of,
    }


def convert_mcp_history_to_points(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Convert MCP history format to renderer-friendly dicts.
    
    Args:
        item: Single item from priceHistory.items with symbol and history
        
    Returns:
        List of dicts that include normalized date ("d") and close ("c") values
    """
    history = item.get("history")
    if not isinstance(history, list):
        return []
    
    points: List[Dict[str, Any]] = []
    for entry in history:
        if not isinstance(entry, dict):
            continue
        date_str = entry.get("d")
        close_price = entry.get("c")
        if date_str and isinstance(close_price, (int, float)):
            points.append({"d": str(date_str)[:10], "c": round(float(close_price), 2)})
    
    return points


def synthesize_price_history_svg(
    extracted_data: Dict[str, Any],
    symbol: str,
    *,
    chart_type: str | None = None,
) -> Tuple[str, Dict[str, Any]]:
    """Generate SVG from extracted MCP price history data.
    
    Args:
        extracted_data: Data from extract_price_history_from_mcp_response
        symbol: Stock symbol for the chart
        
    Returns:
        Tuple of (svg_string, metadata_dict)
    """
    # Import lazily so MCP discovery does not initialize the complete tool
    # registry while the MCP client itself is still being imported.
    from backend.tools.get_timeseries_sparklines_svg import (
        generate_timeseries_sparklines,
    )

    items = extracted_data.get("items", [])
    if not items:
        return "", {}
    
    item = items[0]
    points = convert_mcp_history_to_points(item)
    
    if not points:
        logger.warning("[MCP_PRICE_HISTORY] No valid points extracted for symbol=%s", symbol)
        return "", {}
    
    period = extracted_data.get("period", "1M")
    title = f"{symbol} {period}"
    
    rendered = generate_timeseries_sparklines(
        data=points,
        period=period,
        title=title,
        width=760,
        height=320,
        margin={"top": 16, "right": 20, "bottom": 44, "left": 58},
        up_color="#16a34a",
        down_color="#dc2626",
        grid_color="rgba(148,163,184,0.35)",
        axis_color="#94a3b8",
        label_color="#64748b",
        chart_type=chart_type,
    )
    
    svg = (rendered or {}).get("svg", "")
    if not svg or "<svg" not in svg.lower():
        logger.warning("[MCP_PRICE_HISTORY] SVG generation failed for symbol=%s", symbol)
        return "", {}
    
    metadata = {
        "symbol": symbol,
        "period": period,
        "data_points": len(points),
        "chart_type": "bar" if str(chart_type or "").strip().lower() == "bar" else "line",
        "summary": (rendered or {}).get("summary"),
    }
    
    return svg, metadata


def build_artifact_output(
    svg: str,
    metadata: Dict[str, Any],
    tool_name: str,
    *,
    placeholder: str | None = None,
) -> Dict[str, Any]:
    """Build tool output with artifact placeholder for synthesis.
    
    Args:
        svg: Generated SVG string
        metadata: Chart metadata
        tool_name: Name of the MCP tool
        
    Returns:
        Dict with artifact placeholder for LLM synthesis
    """
    resolved_placeholder = placeholder or f"{{{{ARTIFACT:{tool_name}_svg}}}}"

    return {
        "summary": f"Stock chart generated for {metadata.get('symbol', 'symbol')} ({metadata.get('period', 'period')} period) with {metadata.get('data_points', 0)} data points.",
        "artifact_placeholder": resolved_placeholder,
        "artifact_payload_omitted": True,
        "symbol": metadata.get("symbol"),
        "period": metadata.get("period"),
    }
