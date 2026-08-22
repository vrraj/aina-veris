"""SDK-backed FastAPI route registration for the Veris A2A agent."""

from __future__ import annotations

from fastapi import FastAPI

from a2a.server.events import InMemoryQueueManager
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes.agent_card_routes import create_agent_card_routes
from a2a.server.routes.fastapi_routes import add_a2a_routes_to_fastapi
from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore

from backend.a2a.agent_card import build_veris_agent_card
from backend.a2a.config import VERIS_A2A_AGENTS, veris_agent_card_path
from backend.a2a.executor import VerisResearchExecutor


def register_veris_a2a_routes(app: FastAPI) -> list[DefaultRequestHandler]:
    """Register discovery and JSON-RPC routes for every scoped Veris agent."""
    handlers: list[DefaultRequestHandler] = []
    for agent in VERIS_A2A_AGENTS:
        agent_card = build_veris_agent_card(agent)
        handler = DefaultRequestHandler(
            agent_executor=VerisResearchExecutor(agent),
            task_store=InMemoryTaskStore(),
            agent_card=agent_card,
            queue_manager=InMemoryQueueManager(),
        )
        add_a2a_routes_to_fastapi(
            app,
            agent_card_routes=create_agent_card_routes(agent_card, card_url=veris_agent_card_path(agent)),
            jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url=f"{agent.path}/"),
        )
        app.add_event_handler("shutdown", handler.aclose)
        handlers.append(handler)
    return handlers
