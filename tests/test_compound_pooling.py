from backend.retrieval.compound_pooling import build_coverage_aware_pool
from backend.retrieval.compound_reranking import (
    format_listwise_candidates,
    format_listwise_query,
)
from backend.retrieval.fusion import reciprocal_rank_fusion


def _item(point_id):
    """Build a minimal retrieval item for pooling tests."""
    return {"id": point_id, "score": 0.5, "payload": {"text": point_id}}


def test_coverage_aware_pool_reserves_late_subquery_anchors_before_rrf_fill():
    query_results = [
        {"query": "q1", "results": [_item("shared"), _item("q1-only")]},
        {"query": "q2", "results": [_item("shared"), _item("q2-only")]},
        {"query": "q3", "results": [_item("shared"), _item("q3-only")]},
        {"query": "q4", "results": [_item("q4-only"), _item("q4-second")]},
    ]
    fused = reciprocal_rank_fusion(query_results, limit=20)

    result = build_coverage_aware_pool(
        queries=["q1", "q2", "q3", "q4"],
        query_results=query_results,
        fused_results=fused,
        min_anchors_per_subquery=1,
        pool_cap=4,
    )

    selected_ids = [item["id"] for item in result["items"]]
    assert "q4-only" in selected_ids
    assert len(selected_ids) == 4
    assert result["diagnostics"]["strategy"] == "subquery_anchors_then_rrf"


def test_coverage_aware_pool_clamps_anchor_quota_to_pool_cap():
    query_results = [
        {"query": "q1", "results": [_item("a"), _item("b")]},
        {"query": "q2", "results": [_item("c"), _item("d")]},
    ]
    fused = reciprocal_rank_fusion(query_results, limit=4)

    result = build_coverage_aware_pool(
        queries=["q1", "q2"],
        query_results=query_results,
        fused_results=fused,
        min_anchors_per_subquery=5,
        pool_cap=2,
    )

    assert result["diagnostics"]["effective_anchors_per_subquery"] == 1
    assert len(result["items"]) == 2


def test_listwise_payload_includes_parent_subqueries_and_candidate_matches():
    candidate = _item("shared")
    candidate["compound_retrieval"] = {
        "matched_queries": ["q1", "q2"],
        "query_ranks": {"q1": 1, "q2": 3},
    }

    query_text = format_listwise_query("parent", ["q1", "q2"])
    candidate_text = format_listwise_candidates(
        [candidate],
        ["q1", "q2"],
        chunk_size=100,
    )

    assert "Parent normalized query: parent" in query_text
    assert "1. q1" in query_text
    assert "Matched subqueries: 1 (rank 1), 2 (rank 3)" in candidate_text
