import os

os.environ.setdefault("OPENAI_API_KEY", "test")

from backend.retrieval.fusion import reciprocal_rank_fusion, result_identity
from backend.retrieval.retrieval_eval_service import RetrievalEvalService


def item(point_id, score, text=None):
    row = {"score": score, "payload": {"text": text or str(point_id)}}
    if point_id is not None:
        row["id"] = point_id
    return row


def test_result_identity_prefers_point_id_and_has_payload_fallback():
    assert result_identity(item(42, 0.9)) == "id:42"
    assert result_identity(item(None, 0.9, "same")) == result_identity(
        item(None, 0.2, "same")
    )


def test_reciprocal_rank_fusion_deduplicates_and_records_matches():
    fused = reciprocal_rank_fusion(
        [
            {"query": "permits", "results": [item("shared", 0.8), item("permit", 0.7)]},
            {"query": "weather", "results": [item("weather", 0.9), item("shared", 0.6)]},
        ],
        limit=3,
    )

    assert [row["id"] for row in fused] == ["shared", "weather", "permit"]
    assert fused[0]["retrieval_score"] == 0.8
    assert fused[0]["compound_retrieval"]["matched_queries"] == ["permits", "weather"]
    assert fused[0]["compound_retrieval"]["query_ranks"] == {
        "permits": 1,
        "weather": 2,
    }


def test_retrieve_queries_runs_independent_retrievals_and_fuses_results():
    service = object.__new__(RetrievalEvalService)
    calls = []

    def retrieve(**kwargs):
        calls.append(kwargs["query"])
        results = {
            "permits": [item("shared", 0.8), item("permit", 0.7)],
            "weather": [item("weather", 0.9), item("shared", 0.6)],
        }
        return {
            "results": results[kwargs["query"]],
            "requested_search_mode": "dense",
            "effective_search_mode": "dense",
            "fallback_reason": None,
            "vector_capabilities": {"has_dense": True},
        }

    service.retrieve = retrieve
    result = service.retrieve_queries(
        original_query="permits and weather",
        queries=["permits", "weather"],
        search_mode="dense",
        top_k=3,
        score_threshold=0.35,
        query_filter=None,
        with_payload=True,
        exact=False,
    )

    assert calls == ["permits", "weather"]
    assert result["is_compound"] is True
    assert result["fusion_method"] == "reciprocal_rank_fusion"
    assert [row["id"] for row in result["results"]] == ["shared", "weather", "permit"]
    assert len(result["query_results"]) == 2


def test_retrieve_queries_preserves_single_query_results_without_fusion():
    service = object.__new__(RetrievalEvalService)
    original_results = [item("one", 0.8)]
    service.retrieve = lambda **_kwargs: {
        "results": original_results,
        "requested_search_mode": "dense",
        "effective_search_mode": "dense",
        "fallback_reason": None,
        "vector_capabilities": {"has_dense": True},
    }

    result = service.retrieve_queries(
        original_query="one query",
        queries=["one query", " ONE   QUERY "],
        search_mode="dense",
        top_k=3,
        score_threshold=0.35,
        query_filter=None,
        with_payload=True,
        exact=False,
    )

    assert result["results"] is original_results
    assert result["is_compound"] is False
    assert result["fusion_method"] is None


def test_run_pipeline_uses_complete_query_for_colbert_and_cross_encoder():
    service = object.__new__(RetrievalEvalService)
    rerank_queries = []
    candidates = [item("one", 0.8)]
    service.domain_meta = {}
    service._embedding_dimensions = lambda: None
    service.retrieve_queries = lambda **_kwargs: {"results": candidates}
    service.score_with_colbert = lambda **kwargs: (
        rerank_queries.append(("colbert", kwargs["query"]))
        or {"for_rerank": [{"item": candidates[0]}]}
    )
    service.rerank_with_cross_encoder = lambda **kwargs: (
        rerank_queries.append(("cross_encoder", kwargs["query"]))
        or {"items": []}
    )

    service.run_pipeline(
        query="Corrected complete query",
        queries=["first subquery", "second subquery"],
        search_mode="dense",
        top_k=3,
        score_threshold=0.35,
        query_filter=None,
        with_payload=True,
        exact=False,
        use_colbert=True,
        colbert_top_n=3,
        enable_cross_encoder_rerank=True,
        cross_encoder_top_n=2,
    )

    assert rerank_queries == [
        ("colbert", "Corrected complete query"),
        ("cross_encoder", "Corrected complete query"),
    ]


def test_run_pipeline_reranks_compound_queries_independently():
    service = object.__new__(RetrievalEvalService)
    queries = ["permits", "weather"]
    permits = item("permits", 0.8)
    permits["compound_retrieval"] = {"matched_queries": ["permits"]}
    weather = item("weather", 0.9)
    weather["compound_retrieval"] = {"matched_queries": ["weather"]}
    service.domain_meta = {}
    service._embedding_dimensions = lambda: None
    service.retrieve_queries = lambda **_kwargs: {
        "results": [permits, weather],
        "queries": queries,
        "query_results": [],
    }
    calls = []

    def rerank(**kwargs):
        calls.append(kwargs["query"])
        return {
            "all_scored": [
                {"item": candidate, "cross_encoder_score": 0.9}
                for candidate in kwargs["items"]
            ]
        }

    service.rerank_with_cross_encoder = rerank

    result = service.run_pipeline(
        query="permits and weather",
        queries=queries,
        search_mode="dense",
        top_k=3,
        score_threshold=0.35,
        query_filter=None,
        with_payload=True,
        exact=False,
        use_colbert=False,
        colbert_top_n=3,
        enable_cross_encoder_rerank=True,
        cross_encoder_top_n=2,
        ensure_subquery_coverage=True,
    )

    assert calls == queries
    assert result["reranked"]["mode"] == "pairwise_compound"
    assert result["reranked"]["fusion_method"] == "reciprocal_rank_fusion"
