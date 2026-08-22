"""Coverage-aware candidate pooling for compound-query reranking."""

from __future__ import annotations

from typing import Any, Dict, List

from backend.retrieval.fusion import result_identity


def build_coverage_aware_pool(
    *,
    queries: List[str],
    query_results: List[Dict[str, Any]],
    fused_results: List[Dict[str, Any]],
    min_anchors_per_subquery: int,
    pool_cap: int,
) -> Dict[str, Any]:
    """Reserve per-subquery anchors, then fill a strict pool cap by RRF order.

    Anchors are selected round-robin so early subqueries cannot exhaust the
    pool. Duplicate documents occupy one slot while retaining their compound
    retrieval annotations from the fused result.
    """
    normalized_queries = [
        str(query or "").strip() for query in queries or [] if str(query or "").strip()
    ]
    cap = max(1, int(pool_cap))
    requested_anchors = max(1, int(min_anchors_per_subquery))
    query_count = max(1, len(normalized_queries))
    effective_anchors = min(requested_anchors, max(1, cap // query_count))

    fused_by_id = {
        result_identity(item): item
        for item in fused_results or []
        if isinstance(item, dict)
    }
    results_by_query = {
        str(row.get("query") or "").strip(): list(row.get("results") or [])
        for row in query_results or []
        if isinstance(row, dict)
    }

    selected: List[Dict[str, Any]] = []
    selected_ids: set[str] = set()
    anchor_counts = {query: 0 for query in normalized_queries}

    for rank_index in range(effective_anchors):
        for query in normalized_queries:
            rows = results_by_query.get(query) or []
            if rank_index >= len(rows) or len(selected) >= cap:
                continue
            raw_item = rows[rank_index]
            identity = result_identity(raw_item)
            anchor_counts[query] += 1
            if identity in selected_ids:
                continue
            selected.append(fused_by_id.get(identity, raw_item))
            selected_ids.add(identity)

    for item in fused_results or []:
        if len(selected) >= cap:
            break
        identity = result_identity(item)
        if identity in selected_ids:
            continue
        selected.append(item)
        selected_ids.add(identity)

    return {
        "items": selected,
        "diagnostics": {
            "strategy": "subquery_anchors_then_rrf",
            "pool_cap": cap,
            "pool_size": len(selected),
            "requested_anchors_per_subquery": requested_anchors,
            "effective_anchors_per_subquery": effective_anchors,
            "anchor_counts": anchor_counts,
        },
    }


def query_results_from_ranked_lists(
    ranked_lists: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Convert ranked per-query item lists into retrieval-style query results."""
    return [
        {
            "query": str(row.get("query") or "").strip(),
            "results": list(row.get("results") or []),
        }
        for row in ranked_lists or []
        if isinstance(row, dict) and str(row.get("query") or "").strip()
    ]
