"""Retrieval and rerank utility helpers for the chat pipeline."""

from typing import Any, Dict, List
import json

from backend.chat.pipeline.text_utils import strip_code_fences
from backend.retrieval.retrieval_eval_service import RetrievalEvalService


def candidate_texts(pool: List[Dict[str, Any]]) -> List[str]:
    """Return plain text snippets from a rerank pool."""
    out: List[str] = []
    for res in pool or []:
        pl = res.get("payload") or {}
        txt = pl.get("text") or pl.get("snippet") or pl.get("content") or ""
        out.append(txt)
    return out


def make_rerank_prompt(query: str, cand_texts: List[str], chunk_size: int) -> str:
    """Build a compact rerank prompt identical to existing inline versions."""
    return (
        "You are a reranker. Given a user query and N candidate snippets, return the indices "
        "of the snippets in strictly decreasing relevance order. Crucially, ensure the top results cover distinct facets of the topic and minimize redundancy. Return ONLY a JSON array of integers. "
        "No prose, no code fences, no extra text. Example: [3,0,1].\n\n"
        f"Query: {query}\n\nCandidates (index: text excerpt):\n"
        + "\n".join([f"[{i}] {t[:chunk_size]}" for i, t in enumerate(cand_texts)])
    )


def parse_json_array_in_text(content: str, pool_n: int) -> List[int]:
    """Robustly parse a JSON array of ints from model output; fallback to original order."""
    try_content = strip_code_fences(content or "")
    try:
        order = json.loads(try_content)
    except json.JSONDecodeError:
        start = try_content.find("[")
        end = try_content.rfind("]")
        if start != -1 and end != -1 and start < end:
            try:
                order = json.loads(try_content[start:end + 1])
            except json.JSONDecodeError:
                order = list(range(pool_n))
        else:
            order = list(range(pool_n))
    return [i for i in (order or []) if isinstance(i, int) and 0 <= i < pool_n] or list(range(pool_n))


def retrieve_with_rerank(
    *,
    query: str,
    active_domain: str,
    search_mode: str,
    top_k: int,
    score_threshold: float | None,
    use_colbert: bool,
    colbert_top_n: int,
    enable_cross_encoder_rerank: bool,
    cross_encoder_top_n: int,
) -> Dict[str, Any]:
    """Retrieve and optionally rerank using RetrievalEvalService."""
    service = RetrievalEvalService(active_domain=active_domain)
    return service.run_pipeline(
        query=query,
        search_mode=search_mode,
        top_k=top_k,
        score_threshold=score_threshold,
        query_filter=None,
        with_payload=True,
        exact=False,
        use_colbert=use_colbert,
        colbert_top_n=colbert_top_n,
        enable_cross_encoder_rerank=enable_cross_encoder_rerank,
        cross_encoder_top_n=cross_encoder_top_n,
    )
