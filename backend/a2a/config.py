"""Configuration for the Veris A2A agent."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class VerisA2AAgent:
    name: str
    domain: str
    description: str | None = None
    capability_ids: tuple[str, ...] = ("research",)
    input_modes: tuple[str, ...] = ("text",)

    @property
    def path(self) -> str:
        return f"/agents/{self.name}"


VERIS_A2A_AGENTS = (
    VerisA2AAgent(
        name="veris-mountains-research-agent",
        domain="mountains",
        description=(
            "Grounded research agent for indexed mountain geography, routes, "
            "and reference documents."
        ),
        capability_ids=(
            "geographic_research",
            "mountain_comparison",
            "source_grounded_answers",
        ),
    ),
    VerisA2AAgent(
        name="veris-finance-research-agent",
        domain="finance",
        description=(
            "Grounded research agent for indexed financial documents and "
            "market research sources."
        ),
        capability_ids=(
            "financial_research",
            "financial_document_analysis",
            "source_grounded_answers",
        ),
    ),
    VerisA2AAgent(
        name="veris-semiconductor-research-agent",
        domain="semiconductor_memory",
        description=(
            "Technical document retrieval and analysis agent for indexed "
            "datasheets, application notes, and engineering documents."
        ),
        capability_ids=(
            "grounded_document_search",
            "technical_question_answering",
            "product_comparison",
            "source_grounded_answers",
        ),
        input_modes=("text", "application/json"),
    ),
)


def _bounded_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return min(max(value, minimum), maximum)


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class VerisA2ALimits:
    max_prompt_chars: int
    timeout_seconds: int
    max_concurrent_requests: int
    max_retrieval_results: int
    max_output_tokens: int
    max_tool_calls: int
    enable_tools: bool


def veris_a2a_limits() -> VerisA2ALimits:
    """Return bounded, server-owned limits for every inbound A2A task."""
    return VerisA2ALimits(
        max_prompt_chars=_bounded_int("A2A_VERIS_MAX_PROMPT_CHARS", 8_000, minimum=1, maximum=100_000),
        timeout_seconds=_bounded_int("A2A_VERIS_TIMEOUT_SECONDS", 90, minimum=1, maximum=600),
        max_concurrent_requests=_bounded_int("A2A_VERIS_MAX_CONCURRENT_REQUESTS", 2, minimum=1, maximum=32),
        max_retrieval_results=_bounded_int("A2A_VERIS_MAX_RETRIEVAL_RESULTS", 12, minimum=1, maximum=50),
        max_output_tokens=_bounded_int("A2A_VERIS_MAX_OUTPUT_TOKENS", 600, minimum=32, maximum=4_000),
        max_tool_calls=_bounded_int("A2A_VERIS_MAX_TOOL_CALLS", 2, minimum=0, maximum=10),
        enable_tools=_bool_env("A2A_VERIS_ENABLE_TOOLS", True),
    )


def veris_agent_url(agent: VerisA2AAgent) -> str:
    """Return the public A2A JSON-RPC URL advertised by the AgentCard."""
    base_url = os.getenv("A2A_VERIS_PUBLIC_BASE_URL", "http://localhost:8100")
    return f"{base_url.rstrip('/')}{agent.path}/"


def veris_agent_card_path(agent: VerisA2AAgent) -> str:
    return f"{agent.path}/.well-known/agent-card.json"
