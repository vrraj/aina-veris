"""Application adapter for running retrieval evaluation scenarios."""

from __future__ import annotations

from typing import Any, Callable, Dict

from backend.retrieval.orchestration import run_retrieval_orchestration
from backend.retrieval.retrieval_eval_service import RetrievalEvalService


def run_retrieval_eval(
    eval_request: Any,
    *,
    service: RetrievalEvalService | None = None,
    decomposition_generator: Callable[[str], Any] | None = None,
) -> Dict[str, Any]:
    """Run an eval request through optional decomposition and retrieval."""
    result = run_retrieval_orchestration(
        query=eval_request.query,
        active_domain=eval_request.active_domain,
        split_compound_queries=eval_request.split_compound_queries,
        max_compound_queries=eval_request.max_compound_queries,
        search_mode=eval_request.search_mode,
        top_k=eval_request.top_k,
        score_threshold=eval_request.score_threshold,
        query_filter=eval_request.query_filter,
        with_payload=eval_request.with_payload,
        exact=eval_request.exact,
        use_colbert=eval_request.use_colbert,
        colbert_top_n=eval_request.colbert_top_n,
        enable_cross_encoder_rerank=eval_request.enable_cross_encoder_rerank,
        cross_encoder_top_n=eval_request.cross_encoder_top_n,
        ensure_subquery_coverage=eval_request.ensure_subquery_coverage,
        min_results_per_subquery=eval_request.min_results_per_subquery,
        coverage_max_reserved=eval_request.coverage_max_reserved,
        service=service,
        decomposition_generator=decomposition_generator,
    )
    result["payload_echo"] = eval_request.model_dump()
    return result
