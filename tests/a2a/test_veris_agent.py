from pathlib import Path
import os
import sys

import httpx
import pytest
from fastapi import FastAPI

# Allow this verification file to run directly from the repository root as
# ``python3 tests/a2a/test_veris_agent.py`` as well as through pytest.
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

# The A2A route test stubs the RAG call; this only satisfies application
# settings validation when the file is run outside the normal .env setup.
os.environ.setdefault("OPENAI_API_KEY", "test")

from backend.a2a.agent_card import build_veris_agent_card
from backend.a2a.config import VERIS_A2A_AGENTS, VerisA2ALimits
from backend.a2a.router import register_veris_a2a_routes
from backend.a2a.service import run_veris_research
from backend.core.config import settings


def _app() -> tuple[FastAPI, list[object]]:
    app = FastAPI()
    return app, register_veris_a2a_routes(app)


async def test_veris_agent_cards_are_served():
    app, handlers = _app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        responses = {
            agent.name: await client.get(f"{agent.path}/.well-known/agent-card.json")
            for agent in VERIS_A2A_AGENTS
        }

    for handler in handlers:
        await handler.aclose()
    for agent in VERIS_A2A_AGENTS:
        response = responses[agent.name]
        assert response.status_code == 200
        payload = response.json()
        assert payload["name"] == agent.name
        assert agent.domain in payload["skills"][0]["tags"]
        assert payload["supportedInterfaces"][0]["url"].endswith(f"{agent.path}/")


async def test_veris_agent_returns_completed_task_artifact(monkeypatch):
    monkeypatch.setattr(
        "backend.a2a.executor.run_veris_research",
        lambda prompt, request_id, limits, domain: {
            "answer": f"Research result for {prompt}",
            "sources": [{"source": "https://example.com/diversification"}],
        },
    )
    request = {
        "jsonrpc": "2.0",
        "id": "request-1",
        "method": "SendMessage",
        "params": {
            "message": {
                "messageId": "message-1",
                "role": "ROLE_USER",
                "parts": [{"text": "Explain diversification."}],
            }
        },
    }

    app, handlers = _app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/agents/veris-finance-research-agent/",
            headers={"A2A-Version": "1.0"},
            json=request,
        )

    for handler in handlers:
        await handler.aclose()
    assert response.status_code == 200
    task = response.json()["result"]["task"]
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert task["artifacts"][0]["name"] == "veris-research-response"
    assert task["artifacts"][0]["parts"] == [
        {"text": "Research result for Explain diversification."}
    ]
    assert task["artifacts"][0]["metadata"]["sources"] == [
        {"source": "https://example.com/diversification"}
    ]


async def test_veris_agent_rejects_prompts_over_its_server_limit(monkeypatch):
    monkeypatch.setattr(
        "backend.a2a.executor.veris_a2a_limits",
        lambda: VerisA2ALimits(
            max_prompt_chars=3,
            timeout_seconds=90,
            max_concurrent_requests=2,
            max_retrieval_results=12,
            max_output_tokens=600,
            max_tool_calls=2,
            enable_tools=True,
        ),
    )
    app, handlers = _app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/agents/veris-finance-research-agent/",
            headers={"A2A-Version": "1.0"},
            json={
                "jsonrpc": "2.0",
                "id": "oversize-request",
                "method": "SendMessage",
                "params": {
                    "message": {
                        "messageId": "message-oversize",
                        "role": "ROLE_USER",
                        "parts": [{"text": "four"}],
                    }
                },
            },
        )

    for handler in handlers:
        await handler.aclose()
    task = response.json()["result"]["task"]
    assert task["status"]["state"] == "TASK_STATE_FAILED"
    assert "maximum input size of 3 characters" in task["status"]["message"]["parts"][0]["text"]


def test_veris_agent_cards_have_the_expected_protocol_contract():
    for agent in VERIS_A2A_AGENTS:
        card = build_veris_agent_card(agent)
        assert card.name == agent.name
        assert card.supported_interfaces[0].protocol_binding == "JSONRPC"
        assert card.supported_interfaces[0].protocol_version == "1.0"
        assert list(card.default_input_modes) == list(agent.input_modes)
        assert list(card.default_output_modes) == ["text"]


def test_veris_agent_cards_publish_explicit_descriptions_and_capabilities():
    expected_capabilities = {
        "veris-mountains-research-agent": [
            "geographic_research",
            "mountain_comparison",
            "source_grounded_answers",
        ],
        "veris-finance-research-agent": [
            "financial_research",
            "financial_document_analysis",
            "source_grounded_answers",
        ],
        "veris-semiconductor-research-agent": [
            "grounded_document_search",
            "technical_question_answering",
            "product_comparison",
            "source_grounded_answers",
        ],
    }

    for agent in VERIS_A2A_AGENTS:
        card = build_veris_agent_card(agent)
        assert agent.description
        assert [skill.id for skill in card.skills] == expected_capabilities[agent.name]


def test_semiconductor_research_agent_card_matches_the_external_contract():
    agent = next(
        agent
        for agent in VERIS_A2A_AGENTS
        if agent.name == "veris-semiconductor-research-agent"
    )
    card = build_veris_agent_card(agent)

    assert agent.domain == "semiconductor_memory"
    assert card.name == "veris-semiconductor-research-agent"
    assert card.description == (
        "Technical document retrieval and analysis agent for indexed "
        "datasheets, application notes, and engineering documents."
    )
    assert [skill.id for skill in card.skills] == [
        "grounded_document_search",
        "technical_question_answering",
        "product_comparison",
        "source_grounded_answers",
    ]
    assert list(card.default_input_modes) == ["text", "application/json"]


async def test_semiconductor_research_agent_accepts_structured_query_input(monkeypatch):
    captured = {}

    def fake_research(prompt, request_id, limits, domain):
        captured.update(prompt=prompt, domain=domain)
        return {"answer": "Memory research result"}

    monkeypatch.setattr("backend.a2a.executor.run_veris_research", fake_research)
    app, handlers = _app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/agents/veris-semiconductor-research-agent/",
            headers={"A2A-Version": "1.0"},
            json={
                "jsonrpc": "2.0",
                "id": "memory-request",
                "method": "SendMessage",
                "params": {
                    "message": {
                        "messageId": "memory-message",
                        "role": "ROLE_USER",
                        "parts": [{
                            "data": {
                                "question": "Compare the standby currents.",
                                "product_ids": ["MT48LC16M16"],
                                "datasheet_ids": ["DS-123"],
                            },
                            "mediaType": "application/json",
                        }],
                    }
                },
            },
        )

    for handler in handlers:
        await handler.aclose()
    assert response.status_code == 200
    assert captured["domain"] == "semiconductor_memory"
    assert "Compare the standby currents." in captured["prompt"]
    assert "product_ids: MT48LC16M16" in captured["prompt"]
    assert "datasheet_ids: DS-123" in captured["prompt"]


def test_veris_service_rejects_a_generic_pipeline_failure(monkeypatch):
    monkeypatch.setattr(
        "backend.a2a.service.handle_chat",
        lambda _payload: {"answer": "Sorry, something went wrong."},
    )

    with pytest.raises(RuntimeError, match="pipeline failed"):
        run_veris_research(
            "test",
            request_id="test-request",
            limits=VerisA2ALimits(8000, 90, 2, 12, 600, 2, True),
            domain="finance",
        )


def test_veris_service_uses_server_owned_domain_aware_dense_retrieval(monkeypatch):
    captured = {}

    def fake_handle_chat(payload):
        captured.update(payload)
        return {"answer": "Research result"}

    monkeypatch.setattr("backend.a2a.service.handle_chat", fake_handle_chat)

    result = run_veris_research(
        "Explain diversification.",
        request_id="test-request",
        limits=VerisA2ALimits(8000, 90, 2, 12, 600, 2, True),
        domain="finance",
    )

    assert result["answer"] == "Research result"
    assert captured["params"]["active_domain"] == "finance"
    assert captured["params"]["prompt_domain"] == "finance"
    assert captured["params"]["search_mode"] == "dense"


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
