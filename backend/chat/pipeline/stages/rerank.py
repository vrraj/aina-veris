"""Rerank stage for chat retrieval results."""

from dataclasses import replace
from typing import Any, Callable, Dict, List
import logging

from backend.llm.llm_client import LLMError
from backend.stream_emit import emit_stage
from backend.chat.pipeline.errors import build_rate_limit_response
from backend.chat.pipeline.contracts import PipelineExecutionContext, RerankStageResult
from backend.chat.pipeline.llm_io import (
    extract_text_from_responses,
    extract_usage_from_responses,
    responses_create,
)
from backend.chat.pipeline.retrieval import candidate_texts, parse_json_array_in_text
from backend.chat.prompt_registry import resolve_rerank_prompt, render_full_payload
from backend.retrieval.coverage import select_with_subquery_coverage
from backend.retrieval.compound_pooling import (
    build_coverage_aware_pool,
    query_results_from_ranked_lists,
)
from backend.retrieval.compound_reranking import (
    candidates_for_subquery,
    format_listwise_candidates,
    format_listwise_query,
    fuse_ranked_subquery_lists,
)
from backend.retrieval.orchestration import resolve_coverage_options
from backend.retrieval.retrieval_eval_service import RetrievalEvalService

logger = logging.getLogger(__name__)


def run_rerank_stage(
    *,
    context: PipelineExecutionContext,
    results: List[Dict[str, Any]],
    skip_rerank: bool,
    effective_query: str,
    ranking_context: Dict[str, Any] | None = None,
    debug_log: Callable[[str, str], None] | None = None,
) -> RerankStageResult:
    """Apply the configured rerank policy and return reranked results."""
    settings_obj = context.settings
    stage_specs = context.stage_specs
    params = context.params
    log_origin = context.log_origin
    show_processing_steps = context.show_processing_steps
    req_id = context.req_id
    metrics = context.metrics
    stage_model_keys = context.stage_model_keys
    rewrite_display = context.rewrite_display
    n = len(results) if results else 0
    kept = min(int(getattr(settings_obj, "re_ranker_input_rows", 5)), n)
    if bool(params.get("_force_hosted_listwise_rerank", False)):
        pool_size = n
        output_size = max(
            1,
            min(int(params.get("_hosted_rerank_output_top_n") or kept or 1), n),
        )
    else:
        pool_size = kept
        output_size = kept

    if ranking_context is not None:
        return _run_local_ranking_stages(
            context=context,
            results=results,
            effective_query=effective_query,
            ranking_context=ranking_context,
        )

    if skip_rerank:
        logger.debug("[RERANK] (%s) skipping rerank stage (already handled by retrieval service)", log_origin)
        return RerankStageResult(reranked=results, kept=n)

    rerank_spec = (stage_specs or {}).get("rerank") or {}
    rerank_kwargs = dict(rerank_spec.get("kwargs") or {})
    reranked = results
    rerank_enabled = bool(rerank_kwargs.get("enabled", True))
    skip_reason = ""
    need_rerank = False

    if not rerank_enabled:
        skip_reason = "disabled by retrieval config"
    elif bool(params.get("_force_hosted_listwise_rerank", False)):
        need_rerank = True
    elif n <= 1:
        skip_reason = "<=1 candidate"
    elif n < int(getattr(settings_obj, "re_ranker_input_rows", 5)):
        skip_reason = f"fewer than re_ranker_input_rows ({n} < {getattr(settings_obj, 're_ranker_input_rows', 5)})"
    else:
        try:
            scores = [float(r.get("score", 0.0) or 0.0) for r in results]
            top1 = scores[0]
            top5 = scores[4] if n >= 5 else scores[-1]
            margin = top1 - top5
            min_top1 = float(getattr(settings_obj, "rerank_clear_winner_min_top1", 0.65))
            min_delta = float(getattr(settings_obj, "rerank_clear_winner_min_delta", 0.15))

            has_exact = False
            try:
                for result in results[:5]:
                    payload = result.get("payload") or {}
                    if payload.get("exact_match") or payload.get("is_exact_match") or payload.get("id_match"):
                        if float(result.get("score", 0.0) or 0.0) >= float(getattr(settings_obj, "rerank_exact_match_min_score", 0.80)):
                            has_exact = True
                            break
            except Exception:
                has_exact = False

            if has_exact:
                skip_reason = "exact-match fast path"
            elif (top1 >= min_top1) and (margin >= min_delta):
                skip_reason = f"clear winner (top1={top1:.2f}, delta={margin:.2f})"
            else:
                need_rerank = True
        except Exception as e:
            logger.warning("[RERANK] (%s) score analysis failed; defaulting to rerank: %s", log_origin, e, exc_info=True)
            need_rerank = True

    if need_rerank:
        try:
            logger.info("[PIPELINE] emit stage: Hosted LLM Reranking")
            if show_processing_steps:
                emit_stage(req_id, "Hosted LLM Reranking")
        except Exception:
            pass

    if not need_rerank:
        if debug_log:
            debug_log(f"[RERANK] {log_origin}", f"skipping rerank: {skip_reason}")
        if show_processing_steps:
            emit_stage(req_id, "Skipping Rerank")
        return RerankStageResult(reranked=results[:kept], kept=kept)

    if debug_log:
        debug_log(f"[RERANK] {log_origin}", f"applying rerank over {n} candidates; pool capped to {kept}")
    logger.debug("Rerank pool stats", extra={"candidates": n, "kept": kept})
    pool = results[:pool_size]
    pool_n = len(pool)
    logger.debug("[RERANK] (%s) Pool size=%d of %d", log_origin, pool_n, n)

    try:
        candidates = candidate_texts(pool)
        prompt_domain = str((params or {}).get("prompt_domain") or "").strip()
        if not prompt_domain:
            prompt_domain = str(getattr(settings_obj, "prompt_domain_default", "") or "").strip()

        chunk_size = int(getattr(settings_obj, "reranker_chunk_size", 600))
        hosted_subqueries = list(params.get("_hosted_rerank_subqueries") or [])
        if hosted_subqueries:
            candidates_block = format_listwise_candidates(
                pool,
                hosted_subqueries,
                chunk_size=chunk_size,
            )
            query_for_prompt = format_listwise_query(
                effective_query,
                hosted_subqueries,
            )
        else:
            candidates_block = "\n".join(
                [f"[{i}] {text[:chunk_size]}" for i, text in enumerate(candidates or [])]
            )
            query_for_prompt = effective_query

        registry_path = str(getattr(settings_obj, "inference_prompt_registry_path", "") or "").strip()
        prompt_spec = resolve_rerank_prompt(registry_path=registry_path, domain=prompt_domain)
        payload = render_full_payload(
            prompt_spec.full_payload_template,
            variables={
                "query": query_for_prompt,
                "candidates_block": candidates_block,
            },
        )
        prompt_text = prompt_spec.system_instruction + "\n\n" + payload
        if debug_log:
            debug_log(f"[RERANK] {log_origin} prompt:", prompt_text)

        provider = str(rerank_spec.get("provider") or "openai")
        model = str(rerank_spec.get("model") or getattr(settings_obj, "re_ranker_model", settings_obj.inference_model))
        runtime = str(rerank_spec.get("runtime") or "llm").strip() or "llm"
        kwargs = dict(rerank_spec.get("kwargs") or {})
        logger.debug("[RERANK] (%s) runtime=%s provider=%s model=%s kwargs=%r", log_origin, runtime, provider, model, kwargs)

        if runtime != "llm":
            raise ValueError(f"Unsupported rerank runtime: {runtime}")

        llm_kwargs = {
            k: v
            for k, v in kwargs.items()
            if k not in {"enabled", "top_n", "batch_size", "device"}
        }
        response = responses_create(
            provider=provider,
            model=model,
            input=prompt_text.strip(),
            **llm_kwargs,
        )
        content = extract_text_from_responses(response).strip()
        if debug_log:
            debug_log(f"[RERANK] {log_origin} raw:", content)
        order = parse_json_array_in_text(content, pool_n)
        reranked = ([pool[i] for i in order] or pool)[:output_size]
        usage = extract_usage_from_responses(response, provider=provider) or {}
        metrics.record_stage(
            "rerank",
            model=model,
            usage=usage,
            extra={"candidates_reranked": n},
            model_key=(stage_model_keys or {}).get("rerank"),
        )
    except LLMError as e:
        if (getattr(e, "kind", "") or "") == "rate_limit":
            early_response = build_rate_limit_response(
                req_id=req_id,
                metrics=metrics,
                rewrite_display=rewrite_display,
                stage_label="rerank",
                provider=str(getattr(e, "provider", "") or "").strip() or "the rerank provider",
                model=str(getattr(e, "model", "") or "").strip() or "(unspecified model)",
                action="rerank your results safely",
            )
            return RerankStageResult(
                reranked=results[:output_size],
                kept=output_size,
                early_response=early_response,
            )
        logger.error("[RERANK] (%s) failed; falling back: %s", log_origin, e, exc_info=True)
        reranked = results[:output_size]
    except Exception as e:
        logger.error("[RERANK] (%s) failed; falling back: %s", log_origin, e, exc_info=True)
        reranked = results[:output_size]

    return RerankStageResult(reranked=reranked, kept=output_size)


def _run_local_ranking_stages(
    *,
    context: PipelineExecutionContext,
    results: List[Dict[str, Any]],
    effective_query: str,
    ranking_context: Dict[str, Any],
) -> RerankStageResult:
    """Run explicit local ranking stages for the chat pipeline."""
    params = context.params
    show_processing_steps = context.show_processing_steps
    req_id = context.req_id
    log_origin = context.log_origin
    candidates = results or []
    ranked_candidates = candidates
    ranked_rows: List[Dict[str, Any]] = []
    final_top_n = max(1, int(ranking_context.get("cross_encoder_top_n") or 5))
    active_domain = str(ranking_context.get("active_domain") or "").strip()
    queries = list(ranking_context.get("queries") or [effective_query])

    if len(queries) > 1 and ranking_context.get("enable_cross_encoder_rerank"):
        return _run_pairwise_compound_rerank(
            context=context,
            candidates=candidates,
            effective_query=effective_query,
            ranking_context=ranking_context,
        )

    try:
        service = None
        if ranking_context.get("use_colbert"):
            service = RetrievalEvalService(active_domain=active_domain)
            logger.info("[PIPELINE] emit stage: ColBERT Reranking")
            if show_processing_steps:
                emit_stage(req_id, "ColBERT Reranking")
            colbert = service.score_with_colbert(
                query=effective_query,
                retrieval_results=candidates,
                colbert_top_n=int(ranking_context.get("colbert_top_n") or 8),
            )
            ranked_candidates = [row["item"] for row in colbert.get("for_rerank", [])]

        if ranking_context.get("enable_cross_encoder_rerank"):
            service = service or RetrievalEvalService(active_domain=active_domain)
            logger.info("[PIPELINE] emit stage: Cross-Encoder Reranking")
            if show_processing_steps:
                emit_stage(req_id, "Cross-Encoder Reranking")
            cross_encoder = service.rerank_with_cross_encoder(
                query=effective_query,
                items=ranked_candidates,
                reranked_top_n=final_top_n,
            )
            ranked_rows = cross_encoder.get("all_scored") or cross_encoder.get("items") or []
        else:
            return _run_hosted_rerank_with_coverage(
                context=context,
                results=ranked_candidates,
                effective_query=effective_query,
                candidates=candidates,
                ranking_context=ranking_context,
            )
    except Exception as exc:
        logger.error("[RERANK] (%s) local ranking failed; falling back: %s", log_origin, exc, exc_info=True)
        ranked_rows = [
            {"original_index": index, "cross_encoder_score": None, "item": item}
            for index, item in enumerate(ranked_candidates)
        ]

    coverage = resolve_coverage_options(
        active_domain=active_domain,
        enabled=params.get("ensure_subquery_coverage"),
        min_results_per_subquery=params.get("min_results_per_subquery"),
        max_reserved=params.get("coverage_max_reserved"),
    )
    selection = select_with_subquery_coverage(
        queries=ranking_context.get("queries") or [effective_query],
        candidates=candidates,
        ranked_rows=ranked_rows,
        final_top_n=final_top_n,
        enabled=coverage["enabled"],
        min_results_per_subquery=coverage["min_results_per_subquery"],
        max_reserved=coverage["max_reserved"],
    )
    reranked = [row["item"] for row in selection["items"]]
    return RerankStageResult(reranked=reranked, kept=len(reranked))


def _run_pairwise_compound_rerank(
    *,
    context: PipelineExecutionContext,
    candidates: List[Dict[str, Any]],
    effective_query: str,
    ranking_context: Dict[str, Any],
) -> RerankStageResult:
    """Rerank each compound subquery independently and fuse ranks with RRF."""
    queries = list(ranking_context.get("queries") or [effective_query])
    active_domain = str(ranking_context.get("active_domain") or "").strip()
    final_top_n = max(1, int(ranking_context.get("cross_encoder_top_n") or 5))
    use_colbert = bool(ranking_context.get("use_colbert"))
    service = RetrievalEvalService(active_domain=active_domain)
    ranked_lists: List[Dict[str, Any]] = []

    if context.show_processing_steps and use_colbert:
        emit_stage(context.req_id, "ColBERT Reranking")
    query_groups = [
        (query, candidates_for_subquery(query, candidates)) for query in queries
    ]
    query_groups = [group for group in query_groups if group[1]]

    for pair_index, (query, query_candidates) in enumerate(query_groups, start=1):
        if use_colbert:
            colbert = service.score_with_colbert(
                query=query,
                retrieval_results=query_candidates,
                colbert_top_n=int(ranking_context.get("colbert_top_n") or 8),
            )
            query_candidates = [
                row["item"] for row in colbert.get("for_rerank", [])
            ]
        if context.show_processing_steps:
            emit_stage(
                context.req_id,
                f"Cross-Encoder Rerank - Pair {pair_index}/{len(query_groups)}",
                pair_index=pair_index,
                pair_count=len(query_groups),
                subquery=query,
            )
        cross_encoder = service.rerank_with_cross_encoder(
            query=query,
            items=query_candidates,
            reranked_top_n=max(final_top_n, len(query_candidates)),
        )
        scored_rows = cross_encoder.get("all_scored") or cross_encoder.get("items") or []
        ranked_lists.append(
            {
                "query": query,
                "results": [row["item"] for row in scored_rows if row.get("item")],
            }
        )

    fused = fuse_ranked_subquery_lists(ranked_lists, limit=max(len(candidates), final_top_n))
    ranked_rows = [
        {"original_index": index, "cross_encoder_score": None, "item": item}
        for index, item in enumerate(fused)
    ]
    coverage = resolve_coverage_options(
        active_domain=active_domain,
        enabled=context.params.get("ensure_subquery_coverage"),
        min_results_per_subquery=context.params.get("min_results_per_subquery"),
        max_reserved=context.params.get("coverage_max_reserved"),
    )
    selection = select_with_subquery_coverage(
        queries=queries,
        candidates=fused,
        ranked_rows=ranked_rows,
        final_top_n=final_top_n,
        enabled=coverage["enabled"],
        min_results_per_subquery=coverage["min_results_per_subquery"],
        max_reserved=coverage["max_reserved"],
    )
    context.query_plan_display.update(
        {
            "rerank_mode": "pairwise_local",
            "rerank_label": "Local Vector/Cross-Encoder Math (Pairwise)",
            "post_rerank_fusion": "reciprocal_rank_fusion",
            "rerank_candidate_count": len(fused),
        }
    )
    reranked = [row["item"] for row in selection["items"]]
    return RerankStageResult(reranked=reranked, kept=len(reranked))


def _run_hosted_rerank_with_coverage(
    *,
    context: PipelineExecutionContext,
    results: List[Dict[str, Any]],
    effective_query: str,
    candidates: List[Dict[str, Any]],
    ranking_context: Dict[str, Any],
) -> RerankStageResult:
    """Use the hosted LLM reranker, then apply compound-query coverage."""
    queries = list(ranking_context.get("queries") or [effective_query])
    source_query_results = list(ranking_context.get("query_results") or [])
    pool_source = results

    if len(queries) > 1 and ranking_context.get("use_colbert"):
        service = RetrievalEvalService(
            active_domain=str(ranking_context.get("active_domain") or "").strip()
        )
        colbert_lists: List[Dict[str, Any]] = []
        if context.show_processing_steps:
            emit_stage(context.req_id, "ColBERT Reranking")
        for query in queries:
            query_candidates = candidates_for_subquery(query, candidates)
            if not query_candidates:
                continue
            scored = service.score_with_colbert(
                query=query,
                retrieval_results=query_candidates,
                colbert_top_n=int(ranking_context.get("colbert_top_n") or 8),
            )
            colbert_lists.append(
                {
                    "query": query,
                    "results": [row["item"] for row in scored.get("all_scored", [])],
                }
            )
        if colbert_lists:
            source_query_results = query_results_from_ranked_lists(colbert_lists)
            pool_source = fuse_ranked_subquery_lists(
                colbert_lists,
                limit=max(len(candidates), 1),
            )

    if len(queries) > 1:
        pooled = build_coverage_aware_pool(
            queries=queries,
            query_results=source_query_results,
            fused_results=pool_source,
            min_anchors_per_subquery=int(
                ranking_context.get("compound_min_anchors_per_subquery") or 5
            ),
            pool_cap=int(ranking_context.get("compound_rerank_pool_cap") or 40),
        )
        results = pooled["items"]
        context.query_plan_display.update(
            {
                "rerank_mode": "listwise_hosted",
                "rerank_label": "Hosted LLM Reasoning (Listwise)",
                "pooling": pooled["diagnostics"],
                "rerank_candidate_count": len(results),
            }
        )

    stage_specs = dict(context.stage_specs or {})
    hosted_spec = dict(stage_specs.get("rerank") or {})
    hosted_spec["runtime"] = "llm"
    hosted_kwargs = dict(hosted_spec.get("kwargs") or {})
    hosted_kwargs["max_output_tokens"] = max(
        int(hosted_kwargs.get("max_output_tokens") or 64),
        min(400, max(64, len(results) * 6)),
    )
    hosted_spec["kwargs"] = hosted_kwargs
    stage_specs["rerank"] = hosted_spec
    hosted_params = dict(context.params or {})
    hosted_params["_force_hosted_listwise_rerank"] = True
    hosted_params["_hosted_rerank_subqueries"] = queries if len(queries) > 1 else []
    hosted_params["_hosted_rerank_output_top_n"] = max(
        1, int(ranking_context.get("cross_encoder_top_n") or 5)
    )
    hosted_context = replace(
        context,
        stage_specs=stage_specs,
        params=hosted_params,
    )
    hosted_result = run_rerank_stage(
        context=hosted_context,
        results=results,
        skip_rerank=False,
        effective_query=effective_query,
        ranking_context=None,
    )
    if hosted_result.early_response is not None:
        return hosted_result

    reranked = hosted_result.reranked
    ranked_rows = [
        {"original_index": index, "cross_encoder_score": None, "item": item}
        for index, item in enumerate(reranked)
    ]
    active_domain = str(ranking_context.get("active_domain") or "").strip()
    coverage = resolve_coverage_options(
        active_domain=active_domain,
        enabled=context.params.get("ensure_subquery_coverage"),
        min_results_per_subquery=context.params.get("min_results_per_subquery"),
        max_reserved=context.params.get("coverage_max_reserved"),
    )
    selection = select_with_subquery_coverage(
        queries=ranking_context.get("queries") or [effective_query],
        candidates=results,
        ranked_rows=ranked_rows,
        final_top_n=max(1, int(ranking_context.get("cross_encoder_top_n") or hosted_result.kept)),
        enabled=coverage["enabled"],
        min_results_per_subquery=coverage["min_results_per_subquery"],
        max_reserved=coverage["max_reserved"],
    )
    final_items = [row["item"] for row in selection["items"]]
    return RerankStageResult(reranked=final_items, kept=len(final_items))
