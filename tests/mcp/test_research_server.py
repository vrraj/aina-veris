"""Contract tests for the public Aina-Veris MCP research tools."""

from __future__ import annotations

import os

os.environ.setdefault("OPENAI_API_KEY", "test")

import httpx
import pytest

from backend.integrations.mcp.http_transport import lifespan as mcp_lifespan
from backend.integrations.mcp.research_server import call_research_tool, list_research_tools


def test_mcp_research_tools_match_the_published_a2a_domains():
    tools = list_research_tools()

    assert [tool.name for tool in tools] == [
        "research_mountains",
        "research_finance",
        "research_semiconductor",
    ]
    for tool in tools:
        assert tool.input_schema["required"] == ["prompt"]
        assert tool.input_schema["additionalProperties"] is False


@pytest.mark.asyncio
async def test_mcp_research_tool_uses_the_agent_fixed_domain_and_returns_sources(monkeypatch):
    captured = {}

    def fake_research(prompt, *, request_id, limits, domain):
        captured.update(prompt=prompt, request_id=request_id, limits=limits, domain=domain)
        return {
            "answer": "Grounded semiconductor answer.",
            "sources": [{"url": "https://example.com/datasheet"}],
        }

    monkeypatch.setattr(
        "backend.integrations.mcp.research_server.run_veris_research", fake_research
    )

    result = await call_research_tool(
        "research_semiconductor", {"prompt": "Compare standby current."}
    )

    assert captured["prompt"] == "Compare standby current."
    assert captured["domain"] == "semiconductor_memory"
    assert captured["request_id"].startswith("mcp:")
    assert result.is_error is False
    assert result.content[0].text == "Grounded semiconductor answer."
    assert "https://example.com/datasheet" in result.content[1].text


@pytest.mark.asyncio
async def test_mcp_research_tool_rejects_unknown_tools_and_empty_prompts():
    unknown = await call_research_tool("research_unknown", {"prompt": "Question"})
    empty_prompt = await call_research_tool("research_finance", {"prompt": "  "})

    assert unknown.is_error is True
    assert unknown.content[0].text == "Unknown MCP tool: research_unknown"
    assert empty_prompt.is_error is True
    assert empty_prompt.content[0].text == "prompt is required"


@pytest.mark.asyncio
async def test_streamable_http_mcp_endpoint_accepts_initialization():
    from backend.main import app

    async with mcp_lifespan(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.post(
                "/mcp",
                headers={"Accept": "application/json, text/event-stream"},
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "aina-veris-test", "version": "1.0"},
                    },
                },
            )

    assert response.status_code == 200
    assert response.headers["mcp-session-id"]
    assert '"name":"aina-veris"' in response.text
