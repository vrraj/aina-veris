"""Qdrant collection administration helpers used by the admin API."""

from __future__ import annotations

from typing import Any, Dict, List

from qdrant_client import QdrantClient
from qdrant_client.http import models

from backend.core.config import settings


class CollectionNotFoundError(Exception):
    """Raised when an operation names a collection that Qdrant does not have."""


def _configured_domains_by_collection() -> Dict[str, List[str]]:
    result: Dict[str, List[str]] = {}
    for domain, config in (getattr(settings, "DOMAIN_EMBEDDING_CONFIG", {}) or {}).items():
        collection_name = str((config or {}).get("collection_name") or "").strip()
        if collection_name:
            result.setdefault(collection_name, []).append(str(domain))
    return result


def _vector_description(info: Any) -> str:
    params = getattr(getattr(info, "config", None), "params", None)
    vectors = getattr(params, "vectors", None)
    sparse_vectors = getattr(params, "sparse_vectors", None)
    dense_parts: List[str] = []
    if isinstance(vectors, dict):
        dense_parts = [f"{name}: {spec.size}D" for name, spec in vectors.items()]
    elif vectors is not None:
        dense_parts = [f"dense: {vectors.size}D"]
    if sparse_vectors:
        dense_parts.extend(f"{name}: sparse" for name in sparse_vectors)
    return ", ".join(dense_parts) or "No vectors configured"


def list_collections(client: QdrantClient) -> List[Dict[str, Any]]:
    """Return concise Qdrant collection metadata suitable for the admin table."""
    domain_map = _configured_domains_by_collection()
    collections = []
    for summary in client.get_collections().collections:
        info = client.get_collection(summary.name)
        collections.append(
            {
                "name": summary.name,
                "points_count": int(getattr(info, "points_count", 0) or 0),
                "vectors_count": int(getattr(info, "vectors_count", 0) or 0),
                "vector_config": _vector_description(info),
                "domains": domain_map.get(summary.name, []),
            }
        )
    return sorted(collections, key=lambda collection: collection["name"])


def _to_diff(config: Any, diff_type: Any) -> Any:
    if config is None:
        return None
    return diff_type(**config.model_dump(exclude_none=True))


def recreate_collection(client: QdrantClient, collection_name: str) -> Dict[str, Any]:
    """Recreate a collection with its current configuration and payload indexes.

    Qdrant's recreate operation removes every point.  Taking the configuration
    and payload-index snapshot first lets the replacement stay compatible with
    the domain that was using it.
    """
    try:
        info = client.get_collection(collection_name)
    except Exception as exc:
        raise CollectionNotFoundError(collection_name) from exc

    config = info.config
    params = config.params
    payload_schema = getattr(info, "payload_schema", {}) or {}
    payload_indexes = {
        field_name: details.params or details.data_type
        for field_name, details in payload_schema.items()
    }
    points_count = int(getattr(info, "points_count", 0) or 0)

    client.recreate_collection(
        collection_name=collection_name,
        vectors_config=params.vectors,
        sparse_vectors_config=getattr(params, "sparse_vectors", None),
        shard_number=getattr(params, "shard_number", None),
        sharding_method=getattr(params, "sharding_method", None),
        replication_factor=getattr(params, "replication_factor", None),
        write_consistency_factor=getattr(params, "write_consistency_factor", None),
        on_disk_payload=getattr(params, "on_disk_payload", None),
        payload=getattr(params, "payload", None),
        hnsw_config=_to_diff(getattr(config, "hnsw_config", None), models.HnswConfigDiff),
        optimizers_config=_to_diff(getattr(config, "optimizer_config", None), models.OptimizersConfigDiff),
        wal_config=_to_diff(getattr(config, "wal_config", None), models.WalConfigDiff),
        quantization_config=getattr(config, "quantization_config", None),
        strict_mode_config=_to_diff(getattr(config, "strict_mode_config", None), models.StrictModeConfig),
        metadata=getattr(config, "metadata", None),
    )
    for field_name, field_schema in payload_indexes.items():
        client.create_payload_index(
            collection_name=collection_name,
            field_name=field_name,
            field_schema=field_schema,
        )

    return {
        "name": collection_name,
        "deleted_points": points_count,
        "restored_payload_indexes": sorted(payload_indexes),
    }
