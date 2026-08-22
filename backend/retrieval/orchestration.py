"""Shared compound-query retrieval orchestration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Callable, Dict

from backend.chat.prompt_registry import render_full_payload, resolve_query_decomposition_prompt
from backend.core.config import settings
from backend.llm.llm_client import generate
from backend.retrieval.compound_queries import decompose_compound_query
from backend.retrieval.config import resolve_retrieval_specs
from backend.retrieval.retrieval_eval_service import RetrievalEvalService
from backend.retrieval.schemas import CompoundQueryPlan


def run_retrieval_orchestration(
    *,
    query: str,
    active_domain: str,
    split_compound_queries: bool,
    max_compound_queries: int,
    search_mode: str,
    top_k: int,
    score_threshold: float | None,
    query_filter: Dict[str, Any] | None,
    with_payload: bool,
    exact: bool,
    use_colbert: bool,
    colbert_top_n: int,
    enable_cross_encoder_rerank: bool,
    cross_encoder_top_n: int,
    ensure_subquery_coverage: bool | None = None,
    min_results_per_subquery: int | None = None,
    coverage_max_reserved: int | None = None,
    service: RetrievalEvalService | None = None,
    decomposition_generator: Callable[[str], Any] | None = None,
) -> Dict[str, Any]:
    retrieval_service = service or RetrievalEvalService(active_domain=active_domain)
    plan = build_query_plan(
        query=query,
        active_domain=active_domain,
        split_compound_queries=split_compound_queries,
        max_compound_queries=max_compound_queries,
        generator=decomposition_generator or _generate_decomposition,
    )
    coverage = resolve_coverage_options(
        active_domain=active_domain,
        enabled=ensure_subquery_coverage,
        min_results_per_subquery=min_results_per_subquery,
        max_reserved=coverage_max_reserved,
    )
    result = retrieval_service.run_pipeline(
        query=plan.normalized_query,
        queries=plan.queries,
        search_mode=search_mode,
        top_k=top_k,
        score_threshold=score_threshold,
        query_filter=query_filter,
        with_payload=with_payload,
        exact=exact,
        use_colbert=use_colbert,
        colbert_top_n=colbert_top_n,
        enable_cross_encoder_rerank=enable_cross_encoder_rerank,
        cross_encoder_top_n=cross_encoder_top_n,
        ensure_subquery_coverage=coverage["enabled"],
        min_results_per_subquery=coverage["min_results_per_subquery"],
        coverage_max_reserved=coverage["max_reserved"],
    )
    result["decomposition"] = asdict(plan)
    return result


def build_query_plan(
    *,
    query: str,
    active_domain: str,
    split_compound_queries: bool,
    max_compound_queries: int,
    generator: Callable[[str], Any],
) -> CompoundQueryPlan:
    normalized = " ".join(str(query or "").split())
    if not split_compound_queries:
        return CompoundQueryPlan(normalized, normalized, [normalized], False, "disabled")
    return decompose_compound_query(
        normalized,
        generator=generator,
        prompt=_decomposition_prompt(active_domain, normalized, max_compound_queries),
        max_queries=max_compound_queries,
    )


def resolve_coverage_options(
    *,
    active_domain: str,
    enabled: bool | None,
    min_results_per_subquery: int | None,
    max_reserved: int | None,
) -> Dict[str, Any]:
    specs = resolve_retrieval_specs(
        domain=active_domain or getattr(settings, "active_domain", ""),
        config_path=str(getattr(settings, "retrieval_config_path", "") or "").strip() or None,
    )
    configured = specs.get("coverage") or {}
    return {
        "enabled": bool(enabled) if enabled is not None else bool(configured.get("enabled", False)),
        "min_results_per_subquery": int(min_results_per_subquery or configured.get("min_results_per_subquery", 1)),
        "max_reserved": int(max_reserved or configured.get("max_reserved", 4)),
    }


def _decomposition_prompt(active_domain: str, query: str, max_queries: int) -> str:
    prompt_spec = resolve_query_decomposition_prompt(
        registry_path=str(getattr(settings, "inference_prompt_registry_path", "") or "").strip(),
        domain=active_domain or getattr(settings, "active_domain", ""),
    )
    payload = render_full_payload(
        prompt_spec.full_payload_template,
        variables={"query": query, "max_queries": max_queries},
    )
    return prompt_spec.system_instruction + "\n\n" + payload


def _generate_decomposition(prompt: str) -> Any:
    return generate(
        model_key=str(getattr(settings, "rewrite_model_key", "openai:gpt-4o-mini")),
        input=prompt,
        temperature=0.0,
        max_output_tokens=400,
    )
