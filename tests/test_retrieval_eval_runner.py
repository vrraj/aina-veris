import os

os.environ.setdefault("OPENAI_API_KEY", "test")

from backend.retrieval.eval_runner import run_retrieval_eval
from backend.retrieval.orchestration import resolve_coverage_options
from backend.retrieval.eval_schemas import RetrievalEvalRequest


class FakeRetrievalService:
    def __init__(self):
        self.calls = []

    def run_pipeline(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "domain": {},
            "retrieval": {"results": []},
            "colbert": None,
            "reranked": {"items": []},
        }


def test_eval_runner_skips_generator_when_compound_splitting_disabled():
    service = FakeRetrievalService()

    def fail_if_called(_prompt):
        raise AssertionError("generator should not be called")

    request = RetrievalEvalRequest(query="single query")
    result = run_retrieval_eval(
        request,
        service=service,
        decomposition_generator=fail_if_called,
    )

    assert service.calls[0]["queries"] == ["single query"]
    assert service.calls[0]["query"] == "single query"
    assert result["decomposition"]["reason"] == "disabled"
    assert result["payload_echo"]["split_compound_queries"] is False


def test_eval_runner_passes_decomposed_queries_to_retrieval_pipeline():
    service = FakeRetrievalService()
    prompts = []
    request = RetrievalEvalRequest(
        query="Compare permits and weather",
        split_compound_queries=True,
        max_compound_queries=3,
    )
    result = run_retrieval_eval(
        request,
        service=service,
        decomposition_generator=lambda prompt: prompts.append(prompt) or {
            "normalized_query": "Compare permit requirements and weather conditions",
            "is_compound": True,
            "queries": ["permit requirements", "weather conditions"],
            "reason": "two retrieval needs",
        },
    )

    assert "multiple independently searchable information needs" in prompts[0]
    assert "Return at most 3 queries" in prompts[0]
    assert "Compare permits and weather" in prompts[0]
    assert service.calls[0]["query"] == "Compare permit requirements and weather conditions"
    assert service.calls[0]["queries"] == [
        "permit requirements",
        "weather conditions",
    ]
    assert result["decomposition"]["is_compound"] is True
    assert result["decomposition"]["normalized_query"] == "Compare permit requirements and weather conditions"
    assert result["decomposition"]["reason"] == "two retrieval needs"


def test_eval_runner_coverage_request_values_override_config_defaults():
    request = RetrievalEvalRequest(
        query="query",
        ensure_subquery_coverage=False,
        min_results_per_subquery=2,
        coverage_max_reserved=3,
    )

    assert resolve_coverage_options(
        active_domain="",
        enabled=request.ensure_subquery_coverage,
        min_results_per_subquery=request.min_results_per_subquery,
        max_reserved=request.coverage_max_reserved,
    ) == {
        "enabled": False,
        "min_results_per_subquery": 2,
        "max_reserved": 3,
    }
