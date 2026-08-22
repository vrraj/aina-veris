from types import SimpleNamespace

from backend.chat.pipeline.contracts import PipelineExecutionContext
from backend.chat.pipeline.stages import rerank, retrieval


class MetricsStub:
    def record_stage(self, *_args, **_kwargs):
        pass


def _context(params=None):
    return PipelineExecutionContext(
        settings=SimpleNamespace(re_ranker_input_rows=5),
        params=params or {},
        stage_specs={},
        req_id="query-1",
        log_origin="test",
        show_processing_steps=True,
        metrics=MetricsStub(),
        stage_model_keys={},
        rewrite_display={},
    )


def _item(name):
    return {"id": name, "score": 0.5, "payload": {"text": name}}


def test_local_ranking_emits_colbert_then_cross_encoder(monkeypatch):
    events = []
    calls = []
    candidates = [_item("a"), _item("b")]

    class ServiceStub:
        def __init__(self, active_domain):
            assert active_domain == "mountains"

        def score_with_colbert(self, **kwargs):
            calls.append("colbert")
            return {"for_rerank": [{"item": item} for item in reversed(kwargs["retrieval_results"])]}

        def rerank_with_cross_encoder(self, **kwargs):
            calls.append("cross_encoder")
            assert kwargs["items"] == list(reversed(candidates))
            return {
                "all_scored": [
                    {"original_index": index, "cross_encoder_score": 1.0, "item": item}
                    for index, item in enumerate(kwargs["items"])
                ]
            }

    monkeypatch.setattr(rerank, "RetrievalEvalService", ServiceStub)
    monkeypatch.setattr(rerank, "emit_stage", lambda _req_id, stage: events.append(stage))
    monkeypatch.setattr(
        rerank,
        "resolve_coverage_options",
        lambda **_kwargs: {"enabled": False, "min_results_per_subquery": 1, "max_reserved": 4},
    )

    result = rerank.run_rerank_stage(
        context=_context(),
        results=candidates,
        skip_rerank=False,
        effective_query="query",
        ranking_context={
            "active_domain": "mountains",
            "queries": ["query"],
            "use_colbert": True,
            "colbert_top_n": 2,
            "enable_cross_encoder_rerank": True,
            "cross_encoder_top_n": 2,
        },
    )

    assert calls == ["colbert", "cross_encoder"]
    assert events == ["ColBERT Reranking", "Cross-Encoder Reranking"]
    assert result.reranked == list(reversed(candidates))


def test_chat_retrieval_returns_raw_candidates_for_explicit_ranking(monkeypatch):
    calls = []
    candidates = [_item("a"), _item("b")]

    def run_orchestration(**kwargs):
        calls.append(kwargs)
        return {
            "retrieval": {"results": candidates, "queries": ["query"]},
            "reranked": {"items": [{"item": _item("wrong-stage-result")}]},
            "decomposition": {"normalized_query": "query"},
        }

    monkeypatch.setattr(retrieval, "run_retrieval_orchestration", run_orchestration)
    monkeypatch.setattr(retrieval, "resolve_embedding_spec", lambda _settings: {"model": "embedding"})
    monkeypatch.setattr(retrieval, "emit_stage", lambda *_args, **_kwargs: True)

    context = _context(
        {
            "search_mode": "dense",
            "use_colbert": True,
            "colbert_top_n": 2,
            "enable_cross_encoder_rerank": True,
            "cross_encoder_top_n": 2,
        }
    )
    db = SimpleNamespace(last_embedding_usage={})

    result = retrieval.run_retrieval_stage(
        context=context,
        db=db,
        effective_query="query",
        top_k=10,
        score_threshold=0.1,
    )

    assert calls[0]["use_colbert"] is False
    assert calls[0]["enable_cross_encoder_rerank"] is False
    assert result.results == candidates
    assert result.ranking_context["use_colbert"] is True
    assert result.ranking_context["enable_cross_encoder_rerank"] is True
    assert context.query_plan_display["normalized_query"] == "query"


def test_compound_local_cross_encoder_scores_each_subquery_then_fuses(monkeypatch):
    queries = ["kilimanjaro", "whitney"]
    shared = _item("shared")
    shared["compound_retrieval"] = {"matched_queries": queries}
    first = _item("first")
    first["compound_retrieval"] = {"matched_queries": [queries[0]]}
    second = _item("second")
    second["compound_retrieval"] = {"matched_queries": [queries[1]]}
    calls = []

    class ServiceStub:
        def __init__(self, active_domain):
            assert active_domain == "mountains"

        def rerank_with_cross_encoder(self, **kwargs):
            calls.append(kwargs["query"])
            ordered = list(reversed(kwargs["items"]))
            return {
                "all_scored": [
                    {"item": item, "cross_encoder_score": 1.0 - index / 10}
                    for index, item in enumerate(ordered)
                ]
            }

    monkeypatch.setattr(rerank, "RetrievalEvalService", ServiceStub)
    emitted = []
    monkeypatch.setattr(
        rerank,
        "emit_stage",
        lambda _req_id, stage, **extra: emitted.append((stage, extra)),
    )
    monkeypatch.setattr(
        rerank,
        "resolve_coverage_options",
        lambda **_kwargs: {"enabled": True, "min_results_per_subquery": 1, "max_reserved": 2},
    )
    context = _context()

    result = rerank.run_rerank_stage(
        context=context,
        results=[shared, first, second],
        skip_rerank=False,
        effective_query="compare mountains",
        ranking_context={
            "active_domain": "mountains",
            "queries": queries,
            "use_colbert": False,
            "enable_cross_encoder_rerank": True,
            "cross_encoder_top_n": 2,
        },
    )

    assert calls == queries
    assert len(result.reranked) == 2
    assert context.query_plan_display["rerank_mode"] == "pairwise_local"
    assert context.query_plan_display["post_rerank_fusion"] == "reciprocal_rank_fusion"
    assert [stage for stage, _extra in emitted] == [
        "Cross-Encoder Rerank - Pair 1/2",
        "Cross-Encoder Rerank - Pair 2/2",
    ]
    assert emitted[0][1]["subquery"] == queries[0]


def test_compound_hosted_rerank_receives_structured_capped_pool(monkeypatch):
    queries = ["kilimanjaro", "whitney"]
    first = _item("first")
    first["compound_retrieval"] = {
        "matched_queries": [queries[0]],
        "query_ranks": {queries[0]: 1},
    }
    second = _item("second")
    second["compound_retrieval"] = {
        "matched_queries": [queries[1]],
        "query_ranks": {queries[1]: 1},
    }
    captured = {}

    def responses_create(**kwargs):
        captured.update(kwargs)
        return {"text": "[1,0]", "usage": {}}

    monkeypatch.setattr(rerank, "responses_create", responses_create)
    monkeypatch.setattr(rerank, "emit_stage", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        rerank,
        "resolve_coverage_options",
        lambda **_kwargs: {"enabled": True, "min_results_per_subquery": 1, "max_reserved": 2},
    )
    context = _context()
    object.__setattr__(
        context,
        "settings",
        SimpleNamespace(
            re_ranker_input_rows=5,
            inference_prompt_registry_path="prompts/prompt_registry.yaml",
            prompt_domain_default="",
            reranker_chunk_size=200,
            re_ranker_model="openai:gpt-4o-mini",
            inference_model="openai:gpt-4o-mini",
        ),
    )
    object.__setattr__(
        context,
        "stage_specs",
        {
            "rerank": {
                "runtime": "llm",
                "provider": "openai",
                "model": "openai:gpt-4o-mini",
                "kwargs": {"enabled": True, "max_output_tokens": 64},
            }
        },
    )

    result = rerank.run_rerank_stage(
        context=context,
        results=[first, second],
        skip_rerank=False,
        effective_query="compare mountains",
        ranking_context={
            "active_domain": "mountains",
            "queries": queries,
            "query_results": [
                {"query": queries[0], "results": [first]},
                {"query": queries[1], "results": [second]},
            ],
            "use_colbert": False,
            "enable_cross_encoder_rerank": False,
            "cross_encoder_top_n": 2,
            "compound_min_anchors_per_subquery": 1,
            "compound_rerank_pool_cap": 2,
        },
    )

    assert "Parent normalized query: compare mountains" in captured["input"]
    assert "Matched subqueries: 1 (rank 1)" in captured["input"]
    assert len(result.reranked) == 2
    assert context.query_plan_display["rerank_mode"] == "listwise_hosted"
    assert context.query_plan_display["pooling"]["pool_size"] == 2
