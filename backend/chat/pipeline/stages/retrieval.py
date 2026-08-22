"""Retrieval stage for the chat pipeline."""

from typing import Any, Dict, List
import logging
import time

from backend.chat.pipeline.errors import build_rate_limit_response
from backend.chat.pipeline.contracts import PipelineExecutionContext, RetrievalStageResult
from backend.embeddings.specs import resolve_embedding_spec
from backend.llm.llm_client import LLMError
from backend.retrieval.providers.fastembed_embedding_provider import FastEmbedEmbeddingProvider
from backend.retrieval.orchestration import run_retrieval_orchestration
from backend.retrieval.schemas import EmbeddingSpec
from backend.stream_emit import emit_stage

logger = logging.getLogger(__name__)

_FASTEMBED_EMBEDDING_PROVIDER = FastEmbedEmbeddingProvider()


def run_retrieval_stage(
    *,
    context: PipelineExecutionContext,
    db: Any,
    effective_query: str,
    top_k: int,
    score_threshold: float,
) -> RetrievalStageResult:
    """Retrieve context and record embedding metrics."""
    settings_obj = context.settings
    params = context.params
    stage_specs = context.stage_specs
    log_origin = context.log_origin
    show_processing_steps = context.show_processing_steps
    req_id = context.req_id
    metrics = context.metrics
    stage_model_keys = context.stage_model_keys
    rewrite_display = context.rewrite_display
    logger.debug(
        "[RETRIEVE] (%s) query=%s top_k=%s thr=%.3f",
        log_origin,
        effective_query,
        top_k,
        score_threshold,
    )

    active_domain = str(
        params.get("active_domain")
        or params.get("prompt_domain")
        or getattr(settings_obj, "active_domain", "")
        or ""
    ).strip()
    search_mode = str(params.get("search_mode") or "dense").strip().lower()
    use_colbert = bool(params.get("use_colbert", False))
    colbert_top_n = int(params.get("colbert_top_n", 8))
    enable_cross_encoder_rerank = bool(params.get("enable_cross_encoder_rerank", True))
    cross_encoder_top_n = int(params.get("cross_encoder_top_n", 5))
    split_compound_queries = bool(params.get("split_compound_queries", False))
    max_compound_queries = int(params.get("max_compound_queries", 4))
    compound_min_anchors_per_subquery = int(
        params.get(
            "compound_min_anchors_per_subquery",
            getattr(settings_obj, "compound_min_anchors_per_subquery", 5),
        )
    )
    compound_rerank_pool_cap = int(
        params.get(
            "compound_rerank_pool_cap",
            getattr(settings_obj, "compound_rerank_pool_cap", 40),
        )
    )

    use_new_retrieval = bool(
        params.get("search_mode")
        or params.get("use_colbert")
        or params.get("enable_cross_encoder_rerank")
        or split_compound_queries
    )
    skip_rerank = False
    embed_model_for_metrics = None
    emb_runtime = ""
    results: List[Dict[str, Any]] = []
    ranking_context: Dict[str, Any] | None = None

    try:
        resolve_embedding_spec(settings_obj)
        logger.debug(
            "[EMB] (%s) db.last_embedding_usage=%r",
            log_origin,
            getattr(db, "last_embedding_usage", None),
        )
    except Exception:
        pass

    try:
        logger.info("[PIPELINE] emit stage: Retrieve Vectors")
        if show_processing_steps:
            emit_stage(req_id, "Retrieve Vectors")
    except Exception:
        pass

    if use_new_retrieval:
        logger.info(
            "[RETRIEVE - NEW] (%s) using new retrieval service with search_mode=%s, use_colbert=%s, enable_cross_encoder=%s",
            log_origin,
            search_mode,
            use_colbert,
            enable_cross_encoder_rerank,
        )
        try:
            retrieve_start = time.time()
            retrieval_result = run_retrieval_orchestration(
                query=effective_query,
                active_domain=active_domain,
                split_compound_queries=split_compound_queries,
                max_compound_queries=max_compound_queries,
                search_mode=search_mode,
                top_k=int(top_k),
                score_threshold=float(score_threshold) if score_threshold is not None else None,
                query_filter=None,
                with_payload=True,
                exact=False,
                use_colbert=False,
                colbert_top_n=colbert_top_n,
                enable_cross_encoder_rerank=False,
                cross_encoder_top_n=cross_encoder_top_n,
            )
            retrieve_elapsed = time.time() - retrieve_start
            retrieval = retrieval_result.get("retrieval") or {}
            retrieval_results = retrieval.get("results", [])
            logger.info(
                "[TIMING-RETRIEVAL] (%s) retrieved %d results in %.2fs",
                log_origin,
                len(retrieval_results),
                retrieve_elapsed,
            )

            decomposition = retrieval_result.get("decomposition") or {}
            effective_query = str(decomposition.get("normalized_query") or effective_query)
            results = retrieval_results
            context.query_plan_display.update(
                {
                    "enabled": bool(split_compound_queries),
                    "is_compound": bool(decomposition.get("is_compound", False)),
                    "original_query": str(decomposition.get("original_query") or effective_query),
                    "normalized_query": effective_query,
                    "queries": list(decomposition.get("queries") or [effective_query]),
                    "reason": str(decomposition.get("reason") or ""),
                    "fusion_method": retrieval.get("fusion_method"),
                    "retrieved_candidates": len(retrieval_results),
                }
            )
            logger.info(
                "[RETRIEVAL] (%s) candidates (count=%d): %s",
                log_origin,
                len(results),
                [row.get("payload", {}).get("text", "")[:75] for row in results],
            )
            ranking_context = {
                "active_domain": active_domain,
                "queries": retrieval.get("queries") or [effective_query],
                "query_results": retrieval.get("query_results") or [],
                "use_colbert": use_colbert,
                "colbert_top_n": colbert_top_n,
                "enable_cross_encoder_rerank": enable_cross_encoder_rerank,
                "cross_encoder_top_n": cross_encoder_top_n,
                "compound_min_anchors_per_subquery": max(
                    1, compound_min_anchors_per_subquery
                ),
                "compound_rerank_pool_cap": max(1, compound_rerank_pool_cap),
            }
            skip_rerank = False
        except Exception as exc:
            logger.error(
                "[RETRIEVE] (%s) new retrieval service failed, falling back to legacy: %s",
                log_origin,
                exc,
            )
            use_new_retrieval = False
            skip_rerank = False

    if not use_new_retrieval:
        logger.info("[RETRIEVE - LEGACY] (%s) using legacy retrieval path", log_origin)
        embedding_spec = (stage_specs or {}).get("embedding") or {}
        emb_runtime = str(embedding_spec.get("runtime") or "hosted").strip().lower() or "hosted"
        emb_provider = str(embedding_spec.get("provider") or "openai").strip() or "openai"
        emb_model = str(embedding_spec.get("model") or "").strip()
        emb_kwargs = dict(embedding_spec.get("kwargs") or {})
        emb_extra = {
            key: value
            for key, value in emb_kwargs.items()
            if key not in {"dimensions", "normalize", "batch_size", "device"}
        }
        embed_model_for_metrics = emb_model

        try:
            if emb_runtime == "fastembed":
                try:
                    dimensions = emb_kwargs.get("dimensions")
                    dimensions_int = int(dimensions) if dimensions is not None else None
                except Exception:
                    dimensions_int = None
                try:
                    batch_size = int(emb_kwargs.get("batch_size", 32))
                except Exception:
                    batch_size = 32
                device = emb_kwargs.get("device")

                local_embedding_spec = EmbeddingSpec(
                    task="embedding",
                    runtime="fastembed",
                    provider=emb_provider,
                    model=emb_model,
                    dimensions=dimensions_int,
                    normalize=bool(emb_kwargs.get("normalize", True)),
                    batch_size=max(1, batch_size),
                    device=(str(device).strip() if device is not None else None),
                    extra=emb_extra,
                )
                embedding_result = _FASTEMBED_EMBEDDING_PROVIDER.embed(
                    [effective_query],
                    local_embedding_spec,
                )
                query_vector = (embedding_result.vectors or [[]])[0]
                results = db.search_similar_by_embedding(
                    query_embedding=query_vector,
                    limit=int(top_k),
                    score_threshold=float(score_threshold),
                    with_payload=True,
                    exact=True,
                )
            else:
                results = db.search_similar(
                    query=effective_query,
                    limit=int(top_k),
                    score_threshold=float(score_threshold),
                    with_vectors=False,
                    with_payload=True,
                    exact=True,
                )
        except LLMError as exc:
            if (getattr(exc, "kind", "") or "") == "rate_limit":
                early_response = build_rate_limit_response(
                    req_id=req_id,
                    metrics=metrics,
                    rewrite_display=rewrite_display,
                    stage_label="embedding",
                    provider=str(getattr(exc, "provider", "") or "").strip() or "the embedding provider",
                    model=str(getattr(exc, "model", "") or "").strip() or "(unspecified embedding model)",
                    action="retrieve context safely",
                )
                return RetrievalStageResult(
                    results=[],
                    skip_rerank=False,
                    count=0,
                    kept=0,
                    early_response=early_response,
                )
            raise

    n = len(results) if results else 0
    logger.debug("[RETRIEVE] (%s) Qdrant returned %d", log_origin, n)
    if use_new_retrieval:
        kept = n
    else:
        kept = min(int(getattr(settings_obj, "re_ranker_input_rows", 5)), n)

    embed_tokens = 0
    if not use_new_retrieval and emb_runtime == "fastembed":
        embed_tokens = 0
    else:
        try:
            raw_last = getattr(db, "last_embedding_usage", None)
            logger.debug("[EMB] (%s) db.last_embedding_usage=%r", log_origin, raw_last)
            last = raw_last or {}
            embed_tokens = int(last.get("input_tokens") or last.get("total_tokens") or 0)
            logger.debug("[EMB] (%s) parsed embed_tokens=%d", log_origin, embed_tokens)
        except Exception:
            embed_tokens = 0

    if embed_model_for_metrics:
        emb_model_for_cost = embed_model_for_metrics
    else:
        try:
            resolved_spec = resolve_embedding_spec(settings_obj) or {}
            emb_model_for_cost = str(resolved_spec.get("model") or "text-embedding-3-small")
        except Exception:
            emb_model_for_cost = "text-embedding-3-small"

    metrics.record_stage(
        "embedding",
        model=emb_model_for_cost,
        pt=embed_tokens,
        model_key=(stage_model_keys or {}).get("embedding"),
    )

    return RetrievalStageResult(
        results=results,
        skip_rerank=skip_rerank,
        count=n,
        kept=kept,
        effective_query=effective_query,
        ranking_context=ranking_context if use_new_retrieval else None,
    )
