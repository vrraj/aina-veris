"""Shared MCP research-tool definitions backed by the Veris A2A service."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import mcp.types as types

from backend.a2a.config import VERIS_A2A_AGENTS, VerisA2AAgent, veris_a2a_limits
from backend.a2a.service import run_veris_research


def _tool_name(agent: VerisA2AAgent) -> str:
    """Create a stable MCP tool name from a Veris A2A agent name."""
    name = agent.name.removeprefix("veris-").removesuffix("-research-agent")
    return f"research_{name.replace('-', '_')}"


def _agents_by_tool_name() -> dict[str, VerisA2AAgent]:
    return {_tool_name(agent): agent for agent in VERIS_A2A_AGENTS}


def list_research_tools() -> list[types.Tool]:
    """Return one MCP research tool for each configured Veris A2A agent."""
    return [
        types.Tool(
            name=_tool_name(agent),
            description=agent.description
            or f"Run grounded research against Veris's {agent.domain} domain.",
            input_schema={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Research question for this knowledge domain.",
                    }
                },
                "required": ["prompt"],
                "additionalProperties": False,
            },
        )
        for agent in VERIS_A2A_AGENTS
    ]


async def call_research_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    """Run the server-owned Veris research pipeline for an MCP tool call."""
    agent = _agents_by_tool_name().get(name)
    if agent is None:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"Unknown MCP tool: {name}")],
            is_error=True,
        )

    prompt = str(arguments.get("prompt") or "").strip()
    if not prompt:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text="prompt is required")],
            is_error=True,
        )

    result = await asyncio.to_thread(
        run_veris_research,
        prompt,
        request_id=f"mcp:{uuid.uuid4()}",
        limits=veris_a2a_limits(),
        domain=agent.domain,
    )
    content = [types.TextContent(type="text", text=str(result["answer"]))]
    sources = result.get("sources") or []
    if sources:
        content.append(
            types.TextContent(
                type="text",
                text=f"Sources metadata:\n{json.dumps(sources, ensure_ascii=False)}",
            )
        )
    return types.CallToolResult(content=content)


async def handle_list_tools(_ctx: Any, _params: Any) -> types.ListToolsResult:
    """MCP ``tools/list`` handler shared by HTTP and stdio transports."""
    return types.ListToolsResult(tools=list_research_tools())


async def handle_call_tool(_ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
    """MCP ``tools/call`` handler shared by HTTP and stdio transports."""
    return await call_research_tool(params.name, dict(params.arguments or {}))
