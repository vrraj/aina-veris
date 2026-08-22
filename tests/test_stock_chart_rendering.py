import asyncio
import os

os.environ.setdefault("OPENAI_API_KEY", "test")

from backend.integrations.mcp import client as mcp_client
from backend.integrations.mcp.client import merge_local_parameters
from backend.tools.get_timeseries_sparklines_svg import generate_timeseries_sparklines


DATA = [
    {"d": "2026-01-01", "c": 100},
    {"d": "2026-02-01", "c": 110},
]


def test_local_parameters_are_merged_without_mutating_mcp_schema():
    input_schema = {"type": "object", "properties": {"symbols": {"type": "array"}}}
    schema = merge_local_parameters(
        input_schema,
        {
            "chart_type": {
                "type": "string",
                "enum": ["line", "bar"],
                "default": "line",
                "forward_to_mcp": False,
            }
        },
    )

    assert schema["properties"]["chart_type"]["enum"] == ["line", "bar"]
    assert schema["properties"]["chart_type"]["default"] == "line"
    assert "forward_to_mcp" not in schema["properties"]["chart_type"]
    assert "chart_type" not in input_schema["properties"]


def test_default_chart_type_remains_line():
    rendered = generate_timeseries_sparklines(DATA, period="6M")

    assert "<polyline" in rendered["svg"]
    assert "<rect" not in rendered["svg"]


def test_bar_chart_type_uses_bar_renderer():
    rendered = generate_timeseries_sparklines(DATA, period="6M", chart_type="bar")

    assert "<rect" in rendered["svg"]


def test_chart_type_is_not_forwarded_to_mcp(monkeypatch):
    captured = {}

    async def fake_adapter_call(url, name, arguments):
        captured.update(url=url, name=name, arguments=arguments)
        return {
            "structuredContent": {
                "type": "json",
                "priceHistory": {
                    "success": True,
                    "period": "6M",
                    "symbols": ["NVDA"],
                    "items": [{"symbol": "NVDA", "history": DATA}],
                },
            }
        }

    monkeypatch.setattr(mcp_client, "adapter_call_tool", fake_adapter_call)

    result = asyncio.run(
        mcp_client.call_mcp_tool(
                "stocks",
                "http://mcp.test",
                "get_stock_price_history",
            {"symbols": ["NVDA"], "period": "6M", "chart_type": "bar"},
                tool_runtime={
                    "mcp_integration": "agis_markets",
                    "local_parameters": {"chart_type": {"forward_to_mcp": False}},
                },
        )
    )

    assert captured["arguments"] == {"symbols": ["NVDA"], "period": "6M"}
    assert "<rect" in result.data["_svg_artifact"]
