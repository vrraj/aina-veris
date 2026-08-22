import os
from types import SimpleNamespace

os.environ.setdefault("OPENAI_API_KEY", "test")

from backend.chat.pipeline import tools as registry_tools
from backend.integrations.mcp import client as mcp_client
from backend import tools as tool_catalog


def test_registry_loader_accepts_mcp_overlay_without_runtime(tmp_path):
    path = tmp_path / "tool_registry.yaml"
    path.write_text(
        "tools:\n"
        "  - name: remote_tool\n"
        "    enabled: true\n"
        "    artifact:\n"
        "      produces_artifact: false\n",
        encoding="utf-8",
    )
    settings = SimpleNamespace(tool_registry_path=str(path))

    loaded = registry_tools._load_tool_registry(settings)

    assert loaded["tools_by_name"]["remote_tool"]["name"] == "remote_tool"


def test_discovered_mcp_tools_include_source_server(monkeypatch):
    monkeypatch.setattr(
        mcp_client,
        "_MCP_DEF_CACHE",
        {
            "timestamp": 1.0,
            "definitions": [
                {
                    "name": "tavily_search",
                    "description": "Search the web",
                    "runtime": {"mcp_server": "tavily-search"},
                }
            ],
        },
    )

    assert tool_catalog.get_discovered_mcp_tools() == [
        {
            "name": "tavily_search",
            "description": "Search the web",
            "source": "tavily-search",
            "source_type": "mcp",
        }
    ]


def test_registry_overlay_preserves_discovered_mcp_url_and_sets_artifact_metadata():
    mcp_client.clear_mcp_tool_cache()
    mcp_client._register_tool_runtime(
        "get_stock_price_history",
        "agis-markets",
        "http://mcp.test",
    )

    mcp_client.register_registry_tool_overlay(
        "get_stock_price_history",
        {
            "local_parameters": {"chart_type": {"forward_to_mcp": False}},
            "artifact": {
                "produces_artifact": True,
                "placeholder": "{{ARTIFACT:stock_chart_svg}}",
            },
        },
    )

    runtime = mcp_client.get_mcp_runtime_for_tool("get_stock_price_history")
    assert runtime["mcp_url"] == "http://mcp.test"
    assert runtime["artifact_cfg"]["placeholder"] == "{{ARTIFACT:stock_chart_svg}}"
    assert runtime["local_parameters"]["chart_type"]["forward_to_mcp"] is False
