"""Shared early-exit and quota error responses for chat pipeline stages."""

from typing import Any, Dict

from backend.stream_emit import close_stream, emit_stage


def build_rate_limit_message(
    *,
    stage_label: str,
    provider: str,
    model: str,
    action: str,
) -> str:
    """Build a user-facing provider quota message."""
    return (
        f"Our {stage_label} model (provider={provider}, model={model}) "
        f"is currently over its rate-limit or quota. I couldn't {action}, "
        "so this turn has been stopped. Please try again later or contact "
        "the administrator to increase the quota."
    )


def build_timeout_message(
    *,
    stage_label: str,
    provider: str,
    model: str,
    action: str,
) -> str:
    """Build a user-facing timeout message."""
    return (
        f"Our {stage_label} model (provider={provider}, model={model}) "
        f"took too long to respond. I couldn't {action}. "
        "This might be due to network issues or high load. Please try again."
    )


def build_early_exit_response(
    *,
    req_id: str,
    answer: str,
    metrics: Any,
    rewrite_display: Dict[str, Any],
    tools_used: list[str] | None = None,
) -> Dict[str, Any]:
    """Emit and return the standard pipeline early-exit response."""
    try:
        emit_stage(req_id, "Final Answer", final=True, finalContent=answer)
    except Exception:
        pass
    try:
        close_stream(req_id)
    except Exception:
        pass
    try:
        metrics.finalize_turn()
        turn_metrics, convo_snapshot = metrics.snapshot()
    except Exception:
        turn_metrics = metrics.turn
        convo_snapshot = {
            "tokens": {
                "embedding": 0,
                "llm_input": 0,
                "llm_output": 0,
                "conversation_total": 0,
            },
            "costs": {"conversation_total": 0.0},
        }

    return {
        "answer": answer,
        "sources": [],
        "turn_metrics": turn_metrics,
        "conversation_totals": convo_snapshot,
        "metrics": {"vectors_retrieved": 0},
        "tools_used": tools_used or [],
        "rewrite_display": rewrite_display,
    }


def build_rate_limit_response(
    *,
    req_id: str,
    metrics: Any,
    rewrite_display: Dict[str, Any],
    stage_label: str,
    provider: str,
    model: str,
    action: str,
    tools_used: list[str] | None = None,
) -> Dict[str, Any]:
    """Build, emit, and return a standard provider quota response."""
    message = build_rate_limit_message(
        stage_label=stage_label,
        provider=provider,
        model=model,
        action=action,
    )
    return build_early_exit_response(
        req_id=req_id,
        answer=message,
        metrics=metrics,
        rewrite_display=rewrite_display,
        tools_used=tools_used,
    )


def build_timeout_response(
    *,
    req_id: str,
    metrics: Any,
    rewrite_display: Dict[str, Any],
    stage_label: str,
    provider: str,
    model: str,
    action: str,
    tools_used: list[str] | None = None,
) -> Dict[str, Any]:
    """Build, emit, and return a standard timeout response."""
    message = build_timeout_message(
        stage_label=stage_label,
        provider=provider,
        model=model,
        action=action,
    )
    return build_early_exit_response(
        req_id=req_id,
        answer=message,
        metrics=metrics,
        rewrite_display=rewrite_display,
        tools_used=tools_used,
    )
