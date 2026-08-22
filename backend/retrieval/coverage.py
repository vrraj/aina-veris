"""Coverage-aware final selection for compound retrieval."""

from __future__ import annotations

from typing import Any, Dict, List

from backend.retrieval.fusion import result_identity


def select_with_subquery_coverage(
    *,
    queries: List[str],
    candidates: List[Dict[str, Any]],
    ranked_rows: List[Dict[str, Any]],
    final_top_n: int,
    enabled: bool,
    min_results_per_subquery: int,
    max_reserved: int,
) -> Dict[str, Any]:
    """Reserve qualified candidates for subqueries, then fill by reranker order."""
    normalized_queries = [str(query or "").strip() for query in queries or [] if str(query or "").strip()]
    limit = max(1, int(final_top_n))
    minimum = max(1, int(min_results_per_subquery))
    reserve_limit = max(1, min(int(max_reserved), limit))

    ranked_by_id = {
        result_identity(row.get("item") or {}): row
        for row in ranked_rows or []
        if isinstance(row, dict) and isinstance(row.get("item"), dict)
    }
    candidate_by_id = {
        result_identity(item): item
        for item in candidates or []
        if isinstance(item, dict)
    }

    if not enabled or len(normalized_queries) < 2:
        selected = _deduplicated_rows(ranked_rows, limit)
        return {
            "items": selected,
            "coverage": {
                "enabled": bool(enabled),
                "requested_queries": len(normalized_queries),
                "covered_queries": 0 if not enabled else (len(normalized_queries) if selected else 0),
                "uncovered_queries": [] if not enabled or selected else normalized_queries,
                "reserved_items": 0,
                "guarantee_satisfied": True if not enabled else (bool(selected) or not normalized_queries),
            },
        }

    candidate_ids_by_query: Dict[str, List[str]] = {query: [] for query in normalized_queries}
    for identity, item in candidate_by_id.items():
        matched = ((item.get("compound_retrieval") or {}).get("matched_queries") or [])
        for query in normalized_queries:
            if query in matched:
                candidate_ids_by_query[query].append(identity)

    selected_rows: List[Dict[str, Any]] = []
    selected_ids: set[str] = set()
    coverage_counts = {query: 0 for query in normalized_queries}
    reserved_count = 0

    query_order = sorted(
        normalized_queries,
        key=lambda query: (len(candidate_ids_by_query[query]), normalized_queries.index(query)),
    )
    while len(selected_rows) < reserve_limit:
        uncovered = [query for query in query_order if coverage_counts[query] < minimum]
        if not uncovered:
            break

        target_query = next(
            (
                query
                for query in uncovered
                if any(
                    identity not in selected_ids
                    for identity in candidate_ids_by_query[query]
                )
            ),
            None,
        )
        if target_query is None:
            break
        best_identity = None
        best_gain = 0
        best_rank = len(ranked_rows) + len(candidates) + 1
        for identity in candidate_ids_by_query[target_query]:
            if identity in selected_ids:
                continue
            item = candidate_by_id[identity]
            matched = ((item.get("compound_retrieval") or {}).get("matched_queries") or [])
            gain = sum(1 for name in uncovered if name in matched)
            row = ranked_by_id.get(identity)
            rank = ranked_rows.index(row) if row in ranked_rows else len(ranked_rows)
            if gain > best_gain or (gain == best_gain and rank < best_rank):
                best_identity = identity
                best_gain = gain
                best_rank = rank

        if best_identity is None:
            break

        item = candidate_by_id[best_identity]
        row = ranked_by_id.get(best_identity) or {
            "original_index": len(selected_rows),
            "cross_encoder_score": None,
            "item": item,
        }
        selected_rows.append(row)
        selected_ids.add(best_identity)
        reserved_count += 1
        matched = ((item.get("compound_retrieval") or {}).get("matched_queries") or [])
        for query in normalized_queries:
            if query in matched:
                coverage_counts[query] += 1

    for row in ranked_rows or []:
        item = row.get("item") or {}
        identity = result_identity(item)
        if identity in selected_ids:
            continue
        selected_rows.append(row)
        selected_ids.add(identity)
        if len(selected_rows) >= limit:
            break

    covered = [query for query, count in coverage_counts.items() if count >= minimum]
    uncovered = [query for query in normalized_queries if query not in covered]
    return {
        "items": selected_rows[:limit],
        "coverage": {
            "enabled": True,
            "requested_queries": len(normalized_queries),
            "covered_queries": len(covered),
            "uncovered_queries": uncovered,
            "reserved_items": reserved_count,
            "guarantee_satisfied": not uncovered,
        },
    }


def _deduplicated_rows(rows: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    selected = []
    seen = set()
    for row in rows or []:
        identity = result_identity(row.get("item") or {})
        if identity in seen:
            continue
        seen.add(identity)
        selected.append(row)
        if len(selected) >= limit:
            break
    return selected
