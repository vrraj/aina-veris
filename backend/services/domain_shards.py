"""Resolve a domain's shard collections.

A domain maps to one primary collection (``collection_name``) plus optional
additional shards declared under ``collections`` in
``prompts/domain_embedding_config.yaml``. Every code path that operates on
a domain's corpus — indexing existence checks, retrieval fan-out, document
deletion, exploration — should resolve shards through this module so
multi-shard behavior stays consistent instead of being re-derived per
call site.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from backend.core import settings
from backend.retrieval.fusion import reciprocal_rank_fusion


@dataclass(frozen=True)
class DomainShard:
    """One searchable collection belonging to a domain."""

    name: str
    pipeline: Optional[str] = None
    primary: bool = False


def resolve_domain_shards(active_domain: Optional[str]) -> List[DomainShard]:
    """Resolve the shard collections for a requested domain.

    Follows the same domain fallback semantics as
    ``backend.api.domain_indexing.resolve_domain_config``: requested domain
    -> configured default domain -> its ``default`` entry. Returns the
    primary shard (``collection_name``) first, then any declared
    ``collections`` extras in declaration order.
    """
    available = getattr(settings, "DOMAIN_EMBEDDING_CONFIG", {}) or {}
    configured_default = (
        str(getattr(settings, "active_domain", "") or "").strip() or "default"
    )
    requested = str(active_domain or configured_default).strip()
    effective = requested if requested in available else configured_default
    cfg = available.get(effective) or available.get(configured_default) or {}

    primary_name = str(cfg.get("collection_name") or settings.collection_name)
    shards = [DomainShard(name=primary_name, primary=True)]
    seen = {primary_name}
    for spec in cfg.get("collections") or []:
        name = str((spec or {}).get("name") or "").strip()
        if not name or name in seen:
            continue
        shards.append(
            DomainShard(
                name=name,
                pipeline=str(spec.get("pipeline") or "").strip() or None,
            )
        )
        seen.add(name)
    return shards


def existing_shard_names(shard_names: Sequence[str]) -> set:
    """Return the subset of ``shard_names`` that exist in Qdrant.

    Shards are created lazily on first index, so a declared shard may not
    exist yet — callers should query only existing shards.
    """
    from qdrant_client import QdrantClient

    client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)
    try:
        existing = {c.name for c in client.get_collections().collections}
    finally:
        try:
            client.close()
        except Exception:
            pass
    return {name for name in shard_names if name in existing}


def _url_filter(source: str):
    from qdrant_client.http import models

    return models.Filter(
        must=[
            models.FieldCondition(
                key="url_lower", match=models.MatchValue(value=source.lower())
            )
        ]
    )


def count_document_in_shards(
    active_domain: Optional[str],
    source: str,
    exclude_shard: Optional[str] = None,
) -> dict:
    """Count points whose ``url`` payload matches ``source`` across the
    domain's existing shards (skipping ``exclude_shard``).

    Both PDF pipelines store the canonical source string in the ``url``
    payload (lower-cased in ``url_lower``), so this is the cross-pipeline
    document identity. Returns ``{collection_name: count}``.
    """
    from qdrant_client import QdrantClient

    shards = resolve_domain_shards(active_domain)
    names = [s.name for s in shards if s.name != exclude_shard]
    existing = existing_shard_names(names)
    if not existing:
        return {}

    client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)
    try:
        counts = {}
        flt = _url_filter(source)
        for name in sorted(existing):
            counts[name] = int(
                client.count(collection_name=name, count_filter=flt, exact=True).count
            )
        return {name: n for name, n in counts.items() if n > 0}
    finally:
        try:
            client.close()
        except Exception:
            pass


def delete_document_from_shards(source: str, shard_names: Sequence[str]) -> dict:
    """Delete points matching ``source`` from the given collections.

    Returns ``{collection_name: deleted_count}`` for collections that
    existed. Callers should pass collections confirmed to hold the
    document (from ``count_document_in_shards``).
    """
    from qdrant_client import QdrantClient
    from qdrant_client.http import models

    existing = existing_shard_names(shard_names)
    if not existing:
        return {}

    client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)
    try:
        deleted = {}
        flt = _url_filter(source)
        for name in sorted(existing):
            before = int(
                client.count(collection_name=name, count_filter=flt, exact=True).count
            )
            if before:
                client.delete(
                    collection_name=name,
                    points_selector=models.FilterSelector(filter=flt),
                )
                deleted[name] = before
        return deleted
    finally:
        try:
            client.close()
        except Exception:
            pass


def _search_view(
    view,
    *,
    mode: str,
    query: str,
    top_k: int,
    score_threshold: Optional[float],
    query_filter: Optional[Dict[str, Any]],
    with_payload: bool,
    exact: bool,
) -> Dict[str, Any]:
    """Run a single-collection search against the given shard view."""
    caps = view._get_collection_vector_capabilities()
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
        results = view.search_similar_hybrid(
            query=query,
            limit=top_k,
            score_threshold=effective_score_threshold,
            query_filter=query_filter,
            with_payload=with_payload,
            exact=exact,
        )
    elif effective_mode == "sparse":
        results = view.search_similar_sparse(
            query=query,
            limit=top_k,
            score_threshold=effective_score_threshold,
            query_filter=query_filter,
            with_payload=with_payload,
            exact=exact,
        )
    else:
        results = view.search_similar(
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


def fan_out(
    active_domain: Optional[str],
    *,
    top_k: int,
    search_call,
) -> List[Dict[str, Any]]:
    """Run ``search_call(view)`` across the domain's existing shards and
    RRF-merge the candidate lists.

    ``search_call`` receives either ``None`` (single-shard fast path: use
    the primary db as-is) or a shard view produced by
    ``QdrantDB.for_collection(name)``. Each shard contributes up to
    ``top_k`` candidates; the merged list is capped at ``top_k``.
    """
    shards = resolve_domain_shards(active_domain)
    names = [s.name for s in shards]
    if len(names) == 1:
        collections = names
    else:
        existing = existing_shard_names(names)
        collections = [name for name in names if name in existing]

    if len(collections) <= 1:
        return search_call(None)

    entries = [
        {"query": name, "results": search_call(name)} for name in collections
    ]
    return reciprocal_rank_fusion(entries, limit=max(1, int(top_k)))


def search_shards(
    qdrant_db,
    *,
    active_domain: Optional[str],
    query: str,
    search_mode: str,
    top_k: int,
    score_threshold: Optional[float] = None,
    query_filter: Optional[Dict[str, Any]] = None,
    with_payload: bool = True,
    exact: bool = True,
) -> Dict[str, Any]:
    """Search a domain's corpus across all its existing shards.

    Common service for every search path (chat, search endpoint, retrieval
    orchestration). Single-shard domains take the unchanged
    single-collection path; multi-shard domains query each existing shard
    with the mode its vector layout supports (top_k candidates per shard)
    and merge the candidate lists with reciprocal-rank fusion.
    """
    mode = str(search_mode or "dense").strip().lower()

    def _run(name):
        view = qdrant_db if name is None else qdrant_db.for_collection(name)
        return _search_view(
            view,
            mode=mode,
            query=query,
            top_k=top_k,
            score_threshold=score_threshold,
            query_filter=query_filter,
            with_payload=with_payload,
            exact=exact,
        )

    shards = resolve_domain_shards(active_domain)
    names = [s.name for s in shards]
    if len(names) == 1:
        return _run(None)

    existing = existing_shard_names(names)
    collections = [name for name in names if name in existing]
    if len(collections) <= 1:
        return _run(None)

    shard_entries = []
    shard_modes: Dict[str, str] = {}
    for name in collections:
        single = _run(name)
        shard_modes[name] = single["effective_search_mode"]
        shard_entries.append({"query": name, "results": single["results"]})

    merged = reciprocal_rank_fusion(shard_entries, limit=max(1, int(top_k)))
    primary_mode = shard_modes.get(collections[0]) or mode
    return {
        "results": merged,
        "requested_search_mode": mode,
        "effective_search_mode": primary_mode,
        "fallback_reason": None,
        "vector_capabilities": qdrant_db._get_collection_vector_capabilities(),
        "shards_searched": shard_modes,
    }
