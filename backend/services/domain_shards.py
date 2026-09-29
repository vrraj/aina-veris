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
from typing import List, Optional, Sequence

from backend.core import settings


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
