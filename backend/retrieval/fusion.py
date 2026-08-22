"""Deterministic result fusion for multi-query retrieval."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List


def reciprocal_rank_fusion(
    query_results: List[Dict[str, Any]],
    *,
    limit: int,
    rank_constant: int = 60,
) -> List[Dict[str, Any]]:
    """Deduplicate and rank result lists using reciprocal-rank fusion."""
    fused: Dict[str, Dict[str, Any]] = {}
    constant = max(1, int(rank_constant))

    for query_result in query_results or []:
        query = str(query_result.get("query") or "").strip()
        results = query_result.get("results") or []
        for rank, item in enumerate(results, start=1):
            if not isinstance(item, dict):
                continue
            identity = result_identity(item)
            existing = fused.get(identity)
            if existing is None:
                existing = {
                    "item": dict(item),
                    "rrf_score": 0.0,
                    "matched_queries": [],
                    "query_ranks": {},
                    "retrieval_scores": {},
                    "best_rank": rank,
                    "first_seen": len(fused),
                }
                fused[identity] = existing

            existing["rrf_score"] += 1.0 / (constant + rank)
            existing["best_rank"] = min(existing["best_rank"], rank)
            if query and query not in existing["matched_queries"]:
                existing["matched_queries"].append(query)
            if query:
                existing["query_ranks"][query] = rank
                existing["retrieval_scores"][query] = item.get("score")

    ranked = sorted(
        fused.values(),
        key=lambda row: (
            -row["rrf_score"],
            row["best_rank"],
            row["first_seen"],
        ),
    )

    output: List[Dict[str, Any]] = []
    for row in ranked[:max(1, int(limit))]:
        item = row["item"]
        item["retrieval_score"] = item.get("score")
        item["score"] = row["rrf_score"]
        item["compound_retrieval"] = {
            "fusion_method": "reciprocal_rank_fusion",
            "matched_queries": row["matched_queries"],
            "query_ranks": row["query_ranks"],
            "retrieval_scores": row["retrieval_scores"],
        }
        output.append(item)
    return output


def result_identity(item: Dict[str, Any]) -> str:
    """Return a stable identity for deduplicating retrieval candidates."""
    point_id = item.get("id")
    if point_id is not None:
        return f"id:{point_id}"

    payload = item.get("payload") or {}
    identity_payload = {
        "url": payload.get("url_lower") or payload.get("url"),
        "section": payload.get("section"),
        "subsection": payload.get("subsection"),
        "chunk_index": payload.get("chunk_index"),
        "text": payload.get("text") or payload.get("snippet") or payload.get("content"),
    }
    encoded = json.dumps(
        identity_payload,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    ).encode("utf-8")
    return "payload:" + hashlib.sha256(encoded).hexdigest()
