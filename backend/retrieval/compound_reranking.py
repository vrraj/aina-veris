"""Shared helpers for pairwise compound-query reranking."""

from __future__ import annotations

from typing import Any, Dict, List

from backend.retrieval.fusion import reciprocal_rank_fusion


def candidates_for_subquery(
    query: str,
    candidates: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Return fused candidates whose retrieval metadata matches one subquery."""
    target = str(query or "").strip()
    return [
        item
        for item in candidates or []
        if target
        in list(((item.get("compound_retrieval") or {}).get("matched_queries") or []))
    ]


def fuse_ranked_subquery_lists(
    ranked_lists: List[Dict[str, Any]],
    *,
    limit: int,
) -> List[Dict[str, Any]]:
    """Fuse independently reranked subquery lists using rank-only RRF."""
    query_results = [
        {
            "query": str(row.get("query") or "").strip(),
            "results": list(row.get("results") or []),
        }
        for row in ranked_lists or []
        if isinstance(row, dict) and str(row.get("query") or "").strip()
    ]
    if not query_results:
        return []
    if len(query_results) == 1:
        return query_results[0]["results"][: max(1, int(limit))]
    return reciprocal_rank_fusion(query_results, limit=max(1, int(limit)))


def format_listwise_query(parent_query: str, subqueries: List[str]) -> str:
    """Format the parent and subqueries for one hosted listwise rerank call."""
    lines = [f"Parent normalized query: {str(parent_query or '').strip()}", "Subqueries:"]
    lines.extend(
        f"{index}. {str(query or '').strip()}"
        for index, query in enumerate(subqueries or [], start=1)
        if str(query or "").strip()
    )
    return "\n".join(lines)


def format_listwise_candidates(
    candidates: List[Dict[str, Any]],
    subqueries: List[str],
    *,
    chunk_size: int,
) -> str:
    """Format candidate text with matched-subquery and retrieval-rank metadata."""
    query_numbers = {
        str(query or "").strip(): index
        for index, query in enumerate(subqueries or [], start=1)
        if str(query or "").strip()
    }
    lines: List[str] = []
    for index, item in enumerate(candidates or []):
        payload = item.get("payload") or {}
        text = str(
            payload.get("text")
            or payload.get("snippet")
            or payload.get("content")
            or item.get("text")
            or ""
        )[: max(1, int(chunk_size))]
        metadata = item.get("compound_retrieval") or {}
        matched = list(metadata.get("matched_queries") or [])
        ranks = metadata.get("query_ranks") or {}
        matched_labels = [
            f"{query_numbers.get(query, '?')} (rank {ranks.get(query, '?')})"
            for query in matched
        ]
        suffix = ", ".join(matched_labels) or "parent query"
        lines.append(f"[{index}] {text}\nMatched subqueries: {suffix}")
    return "\n".join(lines)
