"""Provider-agnostic compound-query decomposition."""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List

from backend.retrieval.schemas import CompoundQueryPlan

CompoundQueryGenerator = Callable[[str], Any]


def decompose_compound_query(
    query: str,
    *,
    generator: CompoundQueryGenerator,
    prompt: str,
    max_queries: int = 4,
) -> CompoundQueryPlan:
    """Generate and normalize independent retrieval queries.

    Generator failures and invalid output deliberately fall back to a single
    retrieval using the original query.
    """
    original_query = _normalize_query(query)
    if not original_query:
        raise ValueError("query is required")

    limit = max(2, int(max_queries))
    try:
        generated = generator(prompt)
        payload = _parse_generated_payload(generated)
    except Exception:
        return _single_query_plan(original_query, "decomposition_error")

    normalized_query = _normalize_query(payload.get("normalized_query"))
    if not normalized_query:
        normalized_query = original_query

    if not bool(payload.get("is_compound", False)):
        return _single_query_plan(
            original_query,
            str(payload.get("reason") or "not_compound"),
            normalized_query=normalized_query,
        )

    queries = _normalize_queries(payload.get("queries"), limit)
    if len(queries) < 2:
        return _single_query_plan(
            original_query,
            "invalid_compound_queries",
            normalized_query=normalized_query,
        )

    return CompoundQueryPlan(
        original_query=original_query,
        normalized_query=normalized_query,
        queries=queries,
        is_compound=True,
        reason=str(payload.get("reason") or "compound_query"),
    )


def _parse_generated_payload(generated: Any) -> Dict[str, Any]:
    if isinstance(generated, dict):
        text = generated.get("text")
        if isinstance(text, str) and text.strip():
            generated = text
        else:
            return generated

    if not isinstance(generated, str):
        raise ValueError("decomposition generator must return a JSON object or string")

    text = generated.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("decomposition output must be a JSON object")
    return payload


def _normalize_queries(value: Any, limit: int) -> List[str]:
    if not isinstance(value, list):
        return []

    queries: List[str] = []
    seen: set[str] = set()
    for candidate in value:
        if not isinstance(candidate, str):
            continue
        normalized = _normalize_query(candidate)
        key = normalized.casefold()
        if not normalized or key in seen:
            continue
        seen.add(key)
        queries.append(normalized)
        if len(queries) >= limit:
            break
    return queries


def _normalize_query(query: str) -> str:
    return " ".join(str(query or "").split())


def _single_query_plan(
    query: str,
    reason: str,
    *,
    normalized_query: str | None = None,
) -> CompoundQueryPlan:
    normalized = normalized_query or query
    return CompoundQueryPlan(
        original_query=query,
        normalized_query=normalized,
        queries=[normalized],
        is_compound=False,
        reason=reason,
    )
