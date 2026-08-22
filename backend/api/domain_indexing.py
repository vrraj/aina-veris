"""Domain-aware embedding and Qdrant indexing helpers for API routes."""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit

import tiktoken
from qdrant_client import models

from backend.core import settings
from backend.db import QdrantDB
from backend.llm.llm_client import get_model_info, get_pricing_for_model
from backend.retrieval.config import resolve_retrieval_specs
from backend.retrieval.config_loader import get_model_config, get_model_config_by_key
from backend.retrieval.embedding_router import EmbeddingRouter
from backend.retrieval.schemas import EmbeddingSpec

logger = logging.getLogger(__name__)


def strip_fragment_url(url: str) -> str:
    """Return a URL without its fragment component."""
    if not url:
        return url
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def get_embedding_rate_per_mm_tokens() -> float:
    """Return the embedding cost (USD) per 1M tokens for the active embedding model."""
    default_rate = 0.0

    try:
        embedding_key = str(getattr(settings, "embedding_model_key", "")).strip()
        if not embedding_key:
            return default_rate

        pricing = get_pricing_for_model(model_key=embedding_key)
        if pricing is None:
            return default_rate

        rate = getattr(pricing, "input_per_mm", None)
        if rate is None:
            return default_rate
        return float(rate)
    except Exception:
        return default_rate


def resolve_domain_config(active_domain: Optional[str]) -> Dict[str, str]:
    """Resolve collection/model settings for a requested domain."""
    available_domains = getattr(settings, "DOMAIN_EMBEDDING_CONFIG", {}) or {}
    configured_default_domain = str(getattr(settings, "active_domain", "") or "").strip() or "default"
    requested_domain = str(active_domain or configured_default_domain).strip()
    effective_domain = requested_domain if requested_domain in available_domains else configured_default_domain
    cfg = available_domains.get(effective_domain) or available_domains.get(configured_default_domain) or {}
    collection_name = str(cfg.get("collection_name") or settings.collection_name)
    embedding_model_key = str(cfg.get("embedding_model_key") or settings.embedding_model_key)
    model_type = str(cfg.get("model_type") or "hosted")
    vector_type = cfg.get("vector_type")
    return {
        "requested_domain": requested_domain,
        "effective_domain": effective_domain,
        "collection_name": collection_name,
        "embedding_model_key": embedding_model_key,
        "model_type": model_type,
        "vector_type": vector_type,
    }


def build_domain_qdrant(active_domain: Optional[str]) -> QdrantDB:
    """Create a Qdrant handle for the requested domain."""
    domain_cfg = resolve_domain_config(active_domain)
    return QdrantDB(
        host=settings.qdrant_host,
        port=settings.qdrant_port,
        collection_name=domain_cfg["collection_name"],
        embedding_model_key=domain_cfg["embedding_model_key"],
    )


def get_embedding_spec_for_domain(active_domain: Optional[str]) -> Dict[str, Any]:
    """Resolve embedding spec with domain config as source of truth for model selection."""
    domain_cfg = resolve_domain_config(active_domain)
    retrieval_specs = resolve_retrieval_specs(
        domain=domain_cfg["effective_domain"],
        config_path=str(getattr(settings, "retrieval_config_path", "prompts/local_models_registry.yaml") or "").strip() or None,
    )

    emb_cfg = (retrieval_specs or {}).get("embedding") or {}

    model_type = str(domain_cfg.get("model_type") or "hosted").lower()
    model_key = domain_cfg["embedding_model_key"]
    normalize = emb_cfg.get("normalize", True)
    batch_size = emb_cfg.get("batch_size", 32)
    device = emb_cfg.get("device")
    extra = emb_cfg.get("extra") if isinstance(emb_cfg.get("extra"), dict) else {}

    if model_type == "local":
        local_dense_cfg = get_model_config("dense") or {}
        resolved_local_cfg = local_dense_cfg
        local_model_name = model_key
        if str(model_key).startswith("local:"):
            try:
                resolved_local_cfg = get_model_config_by_key(model_key)
                local_model_name = str(resolved_local_cfg.get("name") or model_key)
            except Exception:
                local_model_name = model_key
        dimensions = emb_cfg.get("dimensions") or resolved_local_cfg.get("dimensions") or local_dense_cfg.get("dimensions")
        return {
            "runtime": emb_cfg.get("runtime", "fastembed"),
            "provider": "local",
            "model": local_model_name,
            "dimensions": dimensions,
            "normalize": normalize,
            "batch_size": batch_size,
            "device": device,
            "extra": extra,
        }

    provider = str(model_key).split(":", 1)[0] if ":" in str(model_key) else "openai"
    dimensions = emb_cfg.get("dimensions")
    if dimensions is None:
        try:
            model_info = get_model_info(model_key=model_key)
            dimensions = (getattr(model_info, "capabilities", {}) or {}).get("dimensions")
        except Exception:
            dimensions = None

    return {
        "runtime": "hosted",
        "provider": provider,
        "model": model_key,
        "dimensions": dimensions,
        "normalize": normalize,
        "batch_size": batch_size,
        "device": device,
        "extra": extra,
    }


def generate_embeddings_with_retrieval(texts: List[str], active_domain: Optional[str]) -> List[List[float]]:
    """Generate embeddings using the retrieval module."""
    spec_dict = get_embedding_spec_for_domain(active_domain)
    spec = EmbeddingSpec(
        task="embedding",
        runtime=spec_dict["runtime"],
        provider=spec_dict["provider"],
        model=spec_dict["model"],
        dimensions=spec_dict["dimensions"],
        normalize=spec_dict["normalize"],
        batch_size=spec_dict["batch_size"],
        device=spec_dict["device"],
        extra=spec_dict["extra"],
    )

    router = EmbeddingRouter()
    result = router.embed(texts, spec)
    return result.vectors


def index_chunks_with_retrieval(
    chunks: List[Dict[str, Any]],
    active_domain: Optional[str],
    force_delete: bool = True,
    max_chunks: Optional[int] = None,
) -> Dict[str, int]:
    """Index chunks using the retrieval module instead of EmbeddingsManager."""
    domain_cfg = resolve_domain_config(active_domain)
    qdrant = build_domain_qdrant(active_domain)
    collection_name = domain_cfg["collection_name"]

    try:
        qdrant.client.get_collection(collection_name)
    except Exception:
        logger.debug("Collection not found, creating it now...")
        qdrant.create_collection()

    try:
        collection_info = qdrant.client.get_collection(collection_name)
        vectors_cfg = collection_info.config.params.vectors
        sparse_cfg = collection_info.config.params.sparse_vectors
        has_named_dense_vector = isinstance(vectors_cfg, dict) and "dense" in vectors_cfg
        has_sparse_vector = isinstance(sparse_cfg, dict) and "sparse" in sparse_cfg
    except Exception:
        has_named_dense_vector = False
        has_sparse_vector = False

    effective_cap = int(getattr(settings, "max_chunks_per_doc", 500))
    if max_chunks is not None:
        try:
            user_cap = int(max_chunks)
            if user_cap > 0:
                effective_cap = min(effective_cap, user_cap)
        except Exception:
            pass
    if len(chunks) > effective_cap:
        chunks = chunks[:effective_cap]

    spec_dict = get_embedding_spec_for_domain(active_domain)
    batch_size = spec_dict["batch_size"]
    points = []
    tokens_used = 0

    try:
        encoding = tiktoken.get_encoding("cl100k_base")
    except Exception:
        encoding = None

    for batch_start in range(0, len(chunks), batch_size):
        batch = chunks[batch_start:batch_start + batch_size]
        batch_texts = []
        for chunk in batch:
            text = chunk.get("text", "") if isinstance(chunk, dict) else chunk
            batch_texts.append(text)

        embeddings = generate_embeddings_with_retrieval(batch_texts, active_domain)

        for offset, chunk in enumerate(batch):
            idx = batch_start + offset
            text = chunk.get("text", "") if isinstance(chunk, dict) else chunk
            embedding = embeddings[offset]

            if encoding:
                try:
                    tokens_used += len(encoding.encode(text, disallowed_special=()))
                except Exception:
                    pass

            chunk_url = chunk.get("url", "")
            chunk_base_url = chunk.get("base_url", strip_fragment_url(chunk_url))
            payload = {
                "text": text,
                "chunk_index": idx,
                "total_chunks": len(chunks),
                "url": chunk_url,
                "url_lower": (chunk_url or "").lower(),
                "base_url": chunk_base_url,
                "base_url_lower": (chunk_base_url or "").lower(),
                "document_type": chunk.get("document_type", "mediawiki"),
                "source": chunk_url,
                "title": chunk.get("title", ""),
                "section": chunk.get("section", "Lead"),
                "subsection": chunk.get("subsection"),
                "embedding_model": spec_dict["model"],
                "embedding_provider": spec_dict["provider"],
                "embedding_runtime": spec_dict["runtime"],
            }

            sparse_vector = None
            if has_sparse_vector:
                try:
                    sparse_emb = qdrant.generate_sparse_embeddings(text)
                    sparse_indices = sparse_emb.get("indices") or []
                    sparse_values = sparse_emb.get("values") or []
                    sparse_vector = models.SparseVector(indices=sparse_indices, values=sparse_values)
                except Exception:
                    sparse_vector = models.SparseVector(indices=[], values=[])

            if has_named_dense_vector and sparse_vector is not None:
                vector_payload = {"dense": embedding, "sparse": sparse_vector}
            elif has_named_dense_vector:
                vector_payload = {"dense": embedding}
            elif sparse_vector is not None:
                vector_payload = {"sparse": sparse_vector}
            else:
                vector_payload = embedding

            points.append(models.PointStruct(
                id=str(uuid.uuid4()),
                vector=vector_payload,
                payload=payload,
            ))

    if force_delete and chunks:
        url = chunks[0].get("url", "")
        if url:
            qdrant.delete_by_url(url)

    qdrant.client.upsert(
        collection_name=collection_name,
        points=points,
    )

    return {
        "vectors_indexed": len(points),
        "tokens_used": tokens_used,
    }
