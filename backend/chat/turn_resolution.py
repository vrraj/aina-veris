"""Conversation-aware turn resolution helpers."""

from typing import Any, Dict
import re


_CONTEXTUAL_ACK_RE = re.compile(
    r"^(?:yes|yeah|yep|sure|okay|ok|please do|go ahead|sounds good|do that|"
    r"tell me more|both|all of them)[.!]?$",
    re.I,
)
_CONVERSATIONAL_RE = re.compile(
    r"^(?:hi|hello|hey|thanks|thank you|thanks a lot|got it|goodbye|bye)[.!]?$",
    re.I,
)


def is_contextual_acknowledgement(message: str) -> bool:
    """Return whether a message depends on a previous assistant offer."""
    return bool(_CONTEXTUAL_ACK_RE.fullmatch((message or "").strip()))


def is_conversational_message(message: str) -> bool:
    """Return whether a short message can be handled without retrieval."""
    return bool(_CONVERSATIONAL_RE.fullmatch((message or "").strip()))


def normalize_turn_resolution(result: Dict[str, Any], message: str) -> Dict[str, Any]:
    """Normalize resolver output while preserving legacy rewrite fields."""
    normalized = dict(result or {})
    intent = str(normalized.get("intent") or "").strip().lower()
    if intent not in {"retrieve", "clarify", "converse", "unchanged"}:
        if normalized.get("ambiguous") and not normalized.get("changed"):
            intent = "clarify"
        else:
            intent = "retrieve"

    rewritten = str(
        normalized.get("standalone_query")
        or normalized.get("rewritten")
        or message
    )
    normalized.update(
        {
            "intent": intent,
            "standalone_query": rewritten,
            "rewritten": rewritten,
            "changed": bool(normalized.get("changed", rewritten != message)),
            "confidence": float(normalized.get("confidence", 0.0) or 0.0),
            "ambiguous": bool(normalized.get("ambiguous", intent == "clarify")),
            "clarification_question": str(
                normalized.get("clarification_question") or ""
            ).strip(),
            "response": str(normalized.get("response") or "").strip(),
            "options": [
                str(option).strip()
                for option in (normalized.get("options") or [])
                if str(option).strip()
            ][:5],
        }
    )
    return normalized
