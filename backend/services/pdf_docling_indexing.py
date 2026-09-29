"""Indexing adapter for the Docling PDF pipeline (spec stage 3, additive).

Writes go to a dedicated, versioned collection
(`<domain_collection><pdf_docling_collection_suffix>`) using the domain's
configured embedding model via the existing embedding router, so dense
dimensions always match the domain model. Payloads are built through a
typed, allowlisted builder (never passed wholesale to shared helpers).

Idempotency: point IDs are stable
`uuid5(domain, pipeline_version, source_key, document_id, chunk_id)`, so
re-indexing overwrites instead of duplicating. Document replacement is
transactional in the order the spec requires: stage extraction and
embeddings first, upsert the new version, then retire stale points of the
same `(document_id, pipeline)` that the new version no longer contains.
A failed conversion or upsert leaves the previous searchable version intact.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, List, Optional, Sequence, Set

from qdrant_client import models

from backend.api.domain_indexing import (
    generate_embeddings_with_retrieval,
    get_embedding_spec_for_domain,
    resolve_domain_config,
    strip_fragment_url,
)
from backend.core.config import settings
from backend.db import QdrantDB
from backend.extractor.docling_chunks import DoclingChunk
from backend.extractor.docling_pdf_extractor import (
    PDF_DOCLING_PIPELINE,
    PDF_DOCLING_PIPELINE_VERSION,
)

logger = logging.getLogger(__name__)

_POINT_ID_NAMESPACE = uuid.UUID("8f5f0b9a-2f1e-4f6a-9c3d-7b1a4e5d6c70")

# The complete allowlist of payload fields for the new route. Anything not
# listed here must not reach Qdrant.
_PAYLOAD_FIELDS = frozenset(
    {
        "pipeline",
        "pipeline_version",
        "domain",
        "source_key",
        "document_id",
        "source",
        "url",
        "url_lower",
        "base_url",
        "base_url_lower",
        "document_type",
        "title",
        "section",
        "subsection",
        "section_path",
        "chunk_id",
        "chunk_index",
        "total_chunks",
        "block_type",
        "representation_type",
        "text",
        "display_text",
        "item_refs",
        "regions",
        "page_numbers",
        "highlight_status",
        "citation_label",
        "artifact_uri",
        "embedding_model",
        "embedding_provider",
        "embedding_runtime",
        "token_count",
    }
)


def resolve_docling_collection_name(active_domain: Optional[str]) -> str:
    """Dedicated versioned collection for the Docling pipeline.

    If the domain's collection_name already carries the docling suffix (i.e.
    the request was routed via a domain that points directly at the docling
    collection), use it as-is rather than double-suffixing.
    """
    domain_cfg = resolve_domain_config(active_domain)
    suffix = str(getattr(settings, "pdf_docling_collection_suffix", "_docling_v1"))
    base = str(domain_cfg["collection_name"])
    return base if base.endswith(suffix) else f"{base}{suffix}"


def build_docling_qdrant(active_domain: Optional[str]) -> QdrantDB:
    """Qdrant handle for the domain's dedicated Docling collection."""
    domain_cfg = resolve_domain_config(active_domain)
    return QdrantDB(
        host=settings.qdrant_host,
        port=settings.qdrant_port,
        collection_name=resolve_docling_collection_name(active_domain),
        embedding_model_key=domain_cfg["embedding_model_key"],
        vector_type=domain_cfg["vector_type"],
    )


def stable_point_id(
    domain: str,
    pipeline_version: str,
    source_key: str,
    document_id: str,
    chunk_id: str,
) -> str:
    key = "|".join((domain, pipeline_version, source_key, document_id, chunk_id))
    return str(uuid.uuid5(_POINT_ID_NAMESPACE, key))


def build_docling_payload(
    chunk: DoclingChunk,
    *,
    domain: str,
    source_key: str,
    source: str,
    artifact_uri: Optional[str],
    spec_dict: Dict[str, Any],
    total_chunks: int,
) -> Dict[str, Any]:
    """Typed, allowlisted payload for a Docling pipeline point."""
    section_path = list(chunk.section_path or [])
    section = section_path[-2] if len(section_path) >= 2 else (section_path[0] if section_path else "Lead")
    subsection = section_path[-1] if len(section_path) >= 2 else None
    base_url = strip_fragment_url(source)
    regions = [
        {
            "page_index": r.get("page_index"),
            "page_number": r.get("page_number"),
            "bbox_norm": r.get("bbox_norm"),
            "item_ref": r.get("item_ref"),
            "granularity": r.get("granularity", "item"),
        }
        for r in (chunk.regions or [])
    ]
    payload = {
        "pipeline": PDF_DOCLING_PIPELINE,
        "pipeline_version": PDF_DOCLING_PIPELINE_VERSION,
        "domain": domain,
        "source_key": source_key,
        "document_id": chunk.document_id,
        "source": source,
        "url": source,
        "url_lower": (source or "").lower(),
        "base_url": base_url,
        "base_url_lower": (base_url or "").lower(),
        "document_type": "pdf",
        "title": chunk.title,
        "section": section,
        "subsection": subsection,
        "section_path": section_path,
        "chunk_id": chunk.chunk_id,
        "chunk_index": chunk.chunk_index,
        "total_chunks": total_chunks,
        "block_type": chunk.block_type,
        "representation_type": chunk.representation_type,
        "text": chunk.embedding_text,
        "display_text": chunk.display_text,
        "item_refs": list(chunk.item_refs or []),
        "regions": regions,
        "page_numbers": list(chunk.page_numbers or []),
        "highlight_status": "available" if regions else "unavailable",
        "citation_label": chunk.citation_label,
        "artifact_uri": artifact_uri,
        "embedding_model": spec_dict["model"],
        "embedding_provider": spec_dict["provider"],
        "embedding_runtime": spec_dict["runtime"],
        "token_count": chunk.token_count,
    }
    unexpected = set(payload) - _PAYLOAD_FIELDS
    if unexpected:
        raise ValueError(f"Payload builder produced non-allowlisted fields: {unexpected}")
    return payload


def _pipeline_filter(domain: str, document_id: str) -> models.Filter:
    return models.Filter(
        must=[
            models.FieldCondition(key="pipeline", match=models.MatchValue(value=PDF_DOCLING_PIPELINE)),
            models.FieldCondition(key="document_id", match=models.MatchValue(value=document_id)),
            models.FieldCondition(key="domain", match=models.MatchValue(value=domain)),
        ]
    )


def _existing_point_ids(qdrant: QdrantDB, collection_name: str, domain: str, document_id: str) -> Set[str]:
    ids: Set[str] = set()
    offset = None
    while True:
        points, offset = qdrant.client.scroll(
            collection_name=collection_name,
            scroll_filter=_pipeline_filter(domain, document_id),
            with_payload=False,
            with_vectors=False,
            limit=1000,
            offset=offset,
        )
        ids.update(str(p.id) for p in points)
        if not points or offset is None:
            break
    return ids


class DoclingCancelled(RuntimeError):
    """Raised when a Docling indexing run is cancelled (e.g. client disconnect)."""


def raise_if_cancelled(cancel_event) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise DoclingCancelled("Indexing cancelled")


def _report(progress, message: str) -> None:
    if progress is not None:
        try:
            progress(message)
        except Exception:
            logger.debug("progress callback failed", exc_info=True)


def index_docling_chunks(
    chunks: Sequence[DoclingChunk],
    *,
    active_domain: Optional[str],
    source_key: str,
    source: str,
    artifact_uri: Optional[str] = None,
    max_chunks: Optional[int] = None,
    progress=None,
    cancel_event=None,
) -> Dict[str, Any]:
    """Embed and upsert Docling chunks into the dedicated collection.

    Returns counters. Raises on failure; no legacy fallback.
    """
    domain_cfg = resolve_domain_config(active_domain)
    domain = domain_cfg["effective_domain"]
    collection_name = resolve_docling_collection_name(active_domain)
    qdrant = build_docling_qdrant(active_domain)

    chunk_list = list(chunks)
    effective_cap = int(getattr(settings, "max_chunks_per_doc", 500))
    if max_chunks is not None:
        try:
            user_cap = int(max_chunks)
            if user_cap > 0:
                effective_cap = min(effective_cap, user_cap)
        except Exception:
            pass
    if len(chunk_list) > effective_cap:
        chunk_list = chunk_list[:effective_cap]

    if not chunk_list:
        return {
            "collection_name": collection_name,
            "vectors_indexed": 0,
            "tokens_used": 0,
            "stale_points_deleted": 0,
        }

    spec_dict = get_embedding_spec_for_domain(active_domain)
    batch_size = spec_dict["batch_size"]

    try:
        vectors_cfg = qdrant.client.get_collection(collection_name).config.params.vectors
        sparse_cfg = qdrant.client.get_collection(collection_name).config.params.sparse_vectors
        has_named_dense_vector = isinstance(vectors_cfg, dict) and "dense" in vectors_cfg
        has_sparse_vector = isinstance(sparse_cfg, dict) and "sparse" in sparse_cfg
    except Exception:
        has_named_dense_vector = False
        has_sparse_vector = False

    # Stage embeddings before touching Qdrant so a failure here leaves the
    # previous version fully intact.
    points = []
    tokens_used = 0
    total = len(chunk_list)
    embed_started = time.monotonic()
    _report(progress, f"Embedding {total} chunks (dense + sparse)...")
    logger.info(
        "Docling indexing: embedding %d chunks for %s (batch_size=%d)",
        total, source_key, batch_size,
    )
    for batch_start in range(0, total, batch_size):
        raise_if_cancelled(cancel_event)
        batch = chunk_list[batch_start : batch_start + batch_size]
        batch_texts = [c.embedding_text for c in batch]
        embeddings = generate_embeddings_with_retrieval(batch_texts, active_domain)

        sparse_dicts: List[Optional[Dict[str, List[float]]]] = [None] * len(batch)
        if has_sparse_vector:
            try:
                sparse_dicts = list(qdrant.generate_sparse_embeddings_batch(batch_texts))
            except Exception:
                logger.warning(
                    "Batch sparse embedding failed; falling back to per-chunk calls",
                    exc_info=True,
                )

        for offset_in_batch, chunk in enumerate(batch):
            embedding = embeddings[offset_in_batch]
            payload = build_docling_payload(
                chunk,
                domain=domain,
                source_key=source_key,
                source=source,
                artifact_uri=artifact_uri,
                spec_dict=spec_dict,
                total_chunks=len(chunk_list),
            )
            tokens_used += int(payload["token_count"] or 0)

            vector_payload: Any
            if has_sparse_vector:
                sparse_emb = sparse_dicts[offset_in_batch]
                if sparse_emb is None:
                    try:
                        sparse_emb = qdrant.generate_sparse_embeddings(chunk.embedding_text)
                    except Exception:
                        sparse_emb = None
                sd = sparse_emb or {}
                sparse_vector = models.SparseVector(
                    indices=sd.get("indices") or [],
                    values=sd.get("values") or [],
                )
                vector_payload = (
                    {"dense": embedding, "sparse": sparse_vector}
                    if has_named_dense_vector
                    else {"sparse": sparse_vector}
                )
            else:
                vector_payload = {"dense": embedding} if has_named_dense_vector else embedding

            points.append(
                models.PointStruct(
                    id=stable_point_id(
                        domain,
                        PDF_DOCLING_PIPELINE_VERSION,
                        source_key,
                        chunk.document_id,
                        chunk.chunk_id,
                    ),
                    vector=vector_payload,
                    payload=payload,
                )
            )

        embedded_so_far = batch_start + len(batch)
        _report(progress, f"Embedded {embedded_so_far}/{total} chunks...")
        logger.debug("Docling indexing: embedded %d/%d chunks", embedded_so_far, total)

    logger.info(
        "Docling indexing: embeddings ready in %.1fs; %d points staged",
        time.monotonic() - embed_started, len(points),
    )

    new_ids = {p.id for p in points}

    # Snapshot existing point IDs for this (domain, document_id, pipeline)
    # before the swap so orphans can be retired after a successful upsert.
    try:
        existing_ids = _existing_point_ids(qdrant, collection_name, domain, chunk_list[0].document_id)
    except Exception:
        logger.warning("Could not list existing points for document swap; skipping stale cleanup")
        existing_ids = set()

    # Last checkpoint before the write phase: once the upsert commits, stale
    # cleanup always runs to completion so the collection stays consistent.
    raise_if_cancelled(cancel_event)

    _report(progress, f"Upserting {len(points)} points into {collection_name}...")
    upsert_started = time.monotonic()
    qdrant.client.upsert(collection_name=collection_name, points=points)
    logger.info(
        "Docling indexing: upserted %d points into %s in %.1fs",
        len(points), collection_name, time.monotonic() - upsert_started,
    )

    stale_ids = existing_ids - new_ids
    deleted = 0
    if stale_ids:
        try:
            qdrant.client.delete(
                collection_name=collection_name,
                points_selector=models.PointIdsList(points=list(stale_ids)),
            )
            deleted = len(stale_ids)
        except Exception:
            logger.exception("Failed to retire stale points after upsert")

    _report(progress, f"Indexed {len(points)} chunks into {collection_name}")
    logger.info(
        "Docling indexing: finished %d chunks into %s (stale_points_deleted=%d)",
        len(points), collection_name, deleted,
    )

    return {
        "collection_name": collection_name,
        "vectors_indexed": len(points),
        "tokens_used": tokens_used,
        "stale_points_deleted": deleted,
    }


def count_docling_points_for_document(
    active_domain: Optional[str],
    document_id: str,
) -> int:
    """Duplicate check for the new route: (domain, document_id, pipeline)."""
    domain_cfg = resolve_domain_config(active_domain)
    domain = domain_cfg["effective_domain"]
    qdrant = build_docling_qdrant(active_domain)
    collection_name = resolve_docling_collection_name(active_domain)
    total = 0
    offset = None
    while True:
        points, offset = qdrant.client.scroll(
            collection_name=collection_name,
            scroll_filter=_pipeline_filter(domain, document_id),
            with_payload=False,
            with_vectors=False,
            limit=1000,
            offset=offset,
        )
        total += len(points)
        if not points or offset is None:
            break
    return total
