from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Dict, List, Optional

import numpy as np

from backend.core.config import settings
from backend.embeddings.embeddings_manager import EmbeddingsManager
from backend.llm.llm_client import get_model_info
from backend.retrieval.config_loader import get_model_config, get_model_config_by_key
from backend.retrieval.compound_reranking import (
    candidates_for_subquery,
    fuse_ranked_subquery_lists,
)
from backend.retrieval.coverage import select_with_subquery_coverage
from backend.retrieval.fusion import reciprocal_rank_fusion


@lru_cache(maxsize=2)
def _get_colbert_model(model_name: str, cache_dir: str):
    from fastembed import LateInteractionTextEmbedding

    return LateInteractionTextEmbedding(model_name=model_name, cache_dir=cache_dir)


_cross_encoder_cache = {}

def _get_cross_encoder_model(model_name: str, cache_dir: str):
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    cache_key = (model_name, cache_dir)
    if cache_key not in _cross_encoder_cache:
        _cross_encoder_cache[cache_key] = TextCrossEncoder(model_name=model_name, cache_dir=cache_dir)
    return _cross_encoder_cache[cache_key]


def _extract_text(item: Dict[str, Any]) -> str:
    payload = item.get("payload") or {}
    return (
        payload.get("text")
        or payload.get("snippet")
        or payload.get("content")
        or ""
    )


class RetrievalEvalService:
    def __init__(self, active_domain: Optional[str] = None):
        available_domains = getattr(settings, "DOMAIN_EMBEDDING_CONFIG", {}) or {}
        configured_default = str(getattr(settings, "active_domain", "") or "").strip() or "default"
        requested = str(active_domain or configured_default).strip()
        effective = requested if requested in available_domains else configured_default

        self.domain_config = dict(available_domains.get(effective) or available_domains.get(configured_default) or {})
        self.domain_meta = {
            "requested_domain": requested,
            "effective_domain": effective,
            "collection_name": str(self.domain_config.get("collection_name") or settings.collection_name),
            "embedding_model_key": str(self.domain_config.get("embedding_model_key") or settings.embedding_model_key),
            "model_type": str(self.domain_config.get("model_type") or "hosted"),
            "vector_type": self.domain_config.get("vector_type"),
        }

        embeddings_manager = EmbeddingsManager(active_domain=effective)
        self.qdrant_db = embeddings_manager.qdrant_db

    def _embedding_dimensions(self) -> Optional[int]:
        model_key = self.domain_meta["embedding_model_key"]
        model_type = self.domain_meta["model_type"].lower()

        if model_type == "local":
            try:
                if str(model_key).startswith("local:"):
                    local_cfg = get_model_config_by_key(model_key)
                else:
                    local_cfg = get_model_config("dense")
                dims = local_cfg.get("dimensions")
                return int(dims) if isinstance(dims, int) and dims > 0 else None
            except Exception:
                return None

        try:
            info = get_model_info(model_key=model_key)
            dims = (getattr(info, "capabilities", {}) or {}).get("dimensions")
            return int(dims) if isinstance(dims, int) and dims > 0 else None
        except Exception:
            return None

    def retrieve(
        self,
        *,
        query: str,
        search_mode: str,
        top_k: int,
        score_threshold: Optional[float],
        query_filter: Optional[Dict[str, Any]],
        with_payload: bool,
        exact: bool,
    ) -> Dict[str, Any]:
        mode = str(search_mode or "dense").strip().lower()
        if mode not in {"dense", "sparse", "hybrid"}:
            raise ValueError("search_mode must be one of: dense, sparse, hybrid")

        caps = self.qdrant_db._get_collection_vector_capabilities()
        effective_mode = mode
        fallback_reason = None

        if mode == "hybrid" and not (caps.get("has_dense") and caps.get("has_sparse")):
            effective_mode = "dense"
            fallback_reason = "collection_missing_dense_or_sparse"
        elif mode == "sparse" and not caps.get("has_sparse"):
            effective_mode = "dense"
            fallback_reason = "collection_missing_sparse"

        effective_score_threshold = score_threshold if effective_mode == "dense" else None

        if effective_mode == "hybrid":
            results = self.qdrant_db.search_similar_hybrid(
                query=query,
                limit=top_k,
                score_threshold=effective_score_threshold,
                query_filter=query_filter,
                with_payload=with_payload,
                exact=exact,
            )
        elif effective_mode == "sparse":
            results = self.qdrant_db.search_similar_sparse(
                query=query,
                limit=top_k,
                score_threshold=effective_score_threshold,
                query_filter=query_filter,
                with_payload=with_payload,
                exact=exact,
            )
        else:
            results = self.qdrant_db.search_similar(
                query=query,
                limit=top_k,
                score_threshold=effective_score_threshold,
                query_filter=query_filter,
                with_payload=with_payload,
                exact=exact,
            )

        return {
            "results": results,
            "requested_search_mode": mode,
            "effective_search_mode": effective_mode,
            "fallback_reason": fallback_reason,
            "vector_capabilities": caps,
        }

    def score_with_colbert(
        self,
        *,
        query: str,
        retrieval_results: List[Dict[str, Any]],
        colbert_top_n: int,
    ) -> Dict[str, Any]:
        model_cfg = get_model_config("late_interaction")

        model_name = str(model_cfg.get("name") or "").strip()
        if not model_name:
            raise ValueError("late_interaction model name is missing")

        from backend.retrieval.config_loader import resolve_local_model_cache_dir

        cache_dir = resolve_local_model_cache_dir(model_cfg)
        model = _get_colbert_model(model_name, cache_dir)

        query_embedding = list(model.query_embed(query))[0]

        docs: List[str] = []
        doc_rows: List[Dict[str, Any]] = []
        for row in retrieval_results:
            text = _extract_text(row)
            docs.append(text)
            doc_rows.append(row)

        doc_embeddings = list(model.embed(docs)) if docs else []

        scored: List[Dict[str, Any]] = []
        for idx, doc_matrix in enumerate(doc_embeddings):
            sim_matrix = np.dot(query_embedding, doc_matrix.T)
            max_sim_per_query_token = np.max(sim_matrix, axis=1)
            score = float(np.sum(max_sim_per_query_token))

            scored.append(
                {
                    "original_index": idx,
                    "colbert_score": score,
                    "item": doc_rows[idx],
                }
            )

        scored.sort(key=lambda x: x["colbert_score"], reverse=True)
        top_n = max(1, int(colbert_top_n))
        limited_for_rerank = scored[:top_n]

        return {
            "all_scored": scored,
            "for_rerank": limited_for_rerank,
            "model": model_name,
            "top_n": top_n,
            "count_after_top_n": len(limited_for_rerank),
        }

    def rerank_with_cross_encoder(
        self,
        *,
        query: str,
        items: List[Dict[str, Any]],
        reranked_top_n: int,
    ) -> Dict[str, Any]:
        import logging
        logger = logging.getLogger(__name__)
        
        # Debug: check what we're receiving
        if items:
            logger.info("[RERANK] received items count=%d sample_keys=%s sample_payload_keys=%s",
                len(items),
                list(items[0].keys()) if items else [],
                list((items[0].get("payload") or {}).keys()) if items else [],
            )
        
        model_cfg = get_model_config("reranker")

        model_name = str(model_cfg.get("name") or "").strip()
        if not model_name:
            raise ValueError("reranker model name is missing")

        from backend.retrieval.config_loader import resolve_local_model_cache_dir

        cache_dir = resolve_local_model_cache_dir(model_cfg)
        model = _get_cross_encoder_model(model_name, cache_dir)

        documents = [_extract_text(row) for row in items]
        if not documents:
            return {"items": [], "model": model_name}

        scores = list(model.rerank(query, documents))

        scored_items = []
        for idx, score in enumerate(scores):
            scored_items.append(
                {
                    "original_index": idx,
                    "cross_encoder_score": float(score),
                    "item": items[idx],
                }
            )

        scored_items.sort(key=lambda x: x["cross_encoder_score"], reverse=True)
        top_n = max(1, int(reranked_top_n))

        return {
            "items": scored_items[:top_n],
            "all_scored": scored_items,
            "model": model_name,
            "requested_top_n": top_n,
        }

    def retrieve_queries(
        self,
        *,
        original_query: str,
        queries: List[str],
        search_mode: str,
        top_k: int,
        score_threshold: Optional[float],
        query_filter: Optional[Dict[str, Any]],
        with_payload: bool,
        exact: bool,
    ) -> Dict[str, Any]:
        """Retrieve each query and fuse the candidate lists for downstream stages."""
        normalized_queries = []
        seen = set()
        for query in queries or [original_query]:
            normalized = " ".join(str(query or "").split())
            key = normalized.casefold()
            if normalized and key not in seen:
                seen.add(key)
                normalized_queries.append(normalized)
        if not normalized_queries:
            normalized_queries = [original_query]

        query_results = []
        for query in normalized_queries:
            result = self.retrieve(
                query=query,
                search_mode=search_mode,
                top_k=top_k,
                score_threshold=score_threshold,
                query_filter=query_filter,
                with_payload=with_payload,
                exact=exact,
            )
            query_results.append({"query": query, **result})

        first_result = query_results[0]
        if len(query_results) == 1:
            return {
                **first_result,
                "queries": normalized_queries,
                "is_compound": False,
                "query_results": query_results,
                "fusion_method": None,
            }

        return {
            "results": reciprocal_rank_fusion(
                query_results,
                limit=max(1, int(top_k)) * len(query_results),
            ),
            "requested_search_mode": first_result.get("requested_search_mode"),
            "effective_search_mode": first_result.get("effective_search_mode"),
            "fallback_reason": first_result.get("fallback_reason"),
            "vector_capabilities": first_result.get("vector_capabilities") or {},
            "queries": normalized_queries,
            "is_compound": True,
            "query_results": query_results,
            "fusion_method": "reciprocal_rank_fusion",
        }

    def run_pipeline(
        self,
        *,
        query: str,
        queries: Optional[List[str]] = None,
        search_mode: str,
        top_k: int,
        score_threshold: Optional[float],
        query_filter: Optional[Dict[str, Any]],
        with_payload: bool,
        exact: bool,
        use_colbert: bool,
        colbert_top_n: int,
        enable_cross_encoder_rerank: bool,
        cross_encoder_top_n: int,
        ensure_subquery_coverage: bool = False,
        min_results_per_subquery: int = 1,
        coverage_max_reserved: int = 4,
    ) -> Dict[str, Any]:
        retrieval = self.retrieve_queries(
            original_query=query,
            queries=queries or [query],
            search_mode=search_mode,
            top_k=top_k,
            score_threshold=score_threshold,
            query_filter=query_filter,
            with_payload=with_payload,
            exact=exact,
        )

        retrieval_results = retrieval["results"]
        retrieval_queries = list(retrieval.get("queries") or [query])
        colbert_results = None

        if len(retrieval_queries) > 1 and (use_colbert or enable_cross_encoder_rerank):
            ranked_lists: List[Dict[str, Any]] = []
            colbert_query_results: List[Dict[str, Any]] = []
            for subquery in retrieval_queries:
                subquery_items = candidates_for_subquery(subquery, retrieval_results)
                if use_colbert:
                    scored_colbert = self.score_with_colbert(
                        query=subquery,
                        retrieval_results=subquery_items,
                        colbert_top_n=colbert_top_n,
                    )
                    subquery_items = [
                        row["item"] for row in scored_colbert.get("for_rerank", [])
                    ]
                    colbert_query_results.append(
                        {"query": subquery, **scored_colbert}
                    )
                if enable_cross_encoder_rerank:
                    scored_cross_encoder = self.rerank_with_cross_encoder(
                        query=subquery,
                        items=subquery_items,
                        reranked_top_n=max(cross_encoder_top_n, len(subquery_items)),
                    )
                    scored_rows = (
                        scored_cross_encoder.get("all_scored")
                        or scored_cross_encoder.get("items")
                        or []
                    )
                    subquery_items = [
                        row["item"] for row in scored_rows if row.get("item")
                    ]
                ranked_lists.append(
                    {"query": subquery, "results": subquery_items}
                )

            fused_reranked = fuse_ranked_subquery_lists(
                ranked_lists,
                limit=max(len(retrieval_results), cross_encoder_top_n),
            )
            ranked_rows = [
                {
                    "original_index": index,
                    "cross_encoder_score": None,
                    "item": item,
                }
                for index, item in enumerate(fused_reranked)
            ]
            coverage_selection = select_with_subquery_coverage(
                queries=retrieval_queries,
                candidates=fused_reranked,
                ranked_rows=ranked_rows,
                final_top_n=cross_encoder_top_n,
                enabled=ensure_subquery_coverage,
                min_results_per_subquery=min_results_per_subquery,
                max_reserved=coverage_max_reserved,
            )
            return {
                "domain": {
                    **self.domain_meta,
                    "embedding_dimensions": self._embedding_dimensions(),
                },
                "retrieval": retrieval,
                "colbert": (
                    {"query_results": colbert_query_results}
                    if use_colbert
                    else None
                ),
                "reranked": {
                    "items": coverage_selection["items"],
                    "mode": "pairwise_compound",
                    "fusion_method": "reciprocal_rank_fusion",
                },
                "coverage": coverage_selection["coverage"],
            }

        rerank_source_items = retrieval_results
        if use_colbert:
            colbert_results = self.score_with_colbert(
                query=query,
                retrieval_results=retrieval_results,
                colbert_top_n=colbert_top_n,
            )
            rerank_source_items = [x["item"] for x in colbert_results["for_rerank"]]

        if enable_cross_encoder_rerank:
            reranked = self.rerank_with_cross_encoder(
                query=query,
                items=rerank_source_items,
                reranked_top_n=cross_encoder_top_n,
            )
            ranked_rows = reranked.get("all_scored") or reranked.get("items") or []
        else:
            passthrough_top_n = max(1, int(cross_encoder_top_n))
            ranked_rows = [
                {
                    "original_index": idx,
                    "cross_encoder_score": None,
                    "item": row,
                }
                for idx, row in enumerate(rerank_source_items)
            ]
            reranked = {
                "items": ranked_rows[:passthrough_top_n],
                "model": None,
                "requested_top_n": passthrough_top_n,
                "cross_encoder_enabled": False,
            }

        coverage_selection = select_with_subquery_coverage(
            queries=retrieval.get("queries") or [query],
            candidates=retrieval_results,
            ranked_rows=ranked_rows,
            final_top_n=cross_encoder_top_n,
            enabled=ensure_subquery_coverage,
            min_results_per_subquery=min_results_per_subquery,
            max_reserved=coverage_max_reserved,
        )
        reranked["items"] = coverage_selection["items"]

        return {
            "domain": {
                **self.domain_meta,
                "embedding_dimensions": self._embedding_dimensions(),
            },
            "retrieval": retrieval,
            "colbert": colbert_results,
            "reranked": reranked,
            "coverage": coverage_selection["coverage"],
        }
