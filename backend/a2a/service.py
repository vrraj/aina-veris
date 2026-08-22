"""Adapter from A2A requests to the existing Veris research pipeline."""

from __future__ import annotations

from typing import Any

from backend.chat.chat_manager import handle_chat
from backend.a2a.config import VerisA2ALimits
from backend.core.config import settings


def run_veris_research(
    prompt: str,
    *,
    request_id: str,
    limits: VerisA2ALimits,
    domain: str,
) -> dict[str, Any]:
    """Run one isolated, stateless Veris research request.

    The A2A boundary deliberately supplies no history or caller-controlled
    session identifier, domain, or retrieval mode, preventing an external task
    from sharing browser-chat conversation state or overriding server policy.
    """
    active_domain = domain.strip()
    result = handle_chat(
        {
            "message": prompt,
            "history": [],
            "params": {
                "query_id": request_id,
                "conversation_id": f"a2a:{request_id}",
                "enable_tools": limits.enable_tools,
                "top_k": limits.max_retrieval_results,
                # Match the domain-aware retrieval path used by the UI and
                # ingestion endpoints. These values are server-owned.
                "active_domain": active_domain,
                "prompt_domain": active_domain,
                "search_mode": "dense",
                "max_output_tokens": limits.max_output_tokens,
                "max_tool_calls": limits.max_tool_calls,
                "stream_answer": False,
                "show_processing_steps": False,
            },
        }
    )
    answer = str(result.get("answer") or "").strip()
    # ``handle_chat`` uses this generic text for unexpected pipeline failures
    # without exposing the exception in its public response. It must not become
    # a successful A2A research artifact.
    if answer == "Sorry, something went wrong.":
        raise RuntimeError("Veris research pipeline failed; inspect the request logs for details")
    return {
        "answer": answer,
        "sources": result.get("sources") or [],
        "tools_used": result.get("tools_used") or [],
    }
