import logging
import threading
from typing import List, Tuple

from backend.retrieval.model_cache import TTLModelCache
from backend.retrieval.schemas import EmbeddingSpec, EmbeddingResult


logger = logging.getLogger(__name__)


_shared_cache_lock = threading.Lock()
_shared_model_caches: Tuple[TTLModelCache, TTLModelCache] | None = None


def _idle_timeout() -> int:
    """Resolve the model idle TTL from settings (imported lazily to avoid cycles)."""
    try:
        from backend.core.config import settings
        return int(getattr(settings, "model_cache_idle_ttl_seconds", 300))
    except Exception:
        return 300


def _get_shared_model_caches() -> Tuple[TTLModelCache, TTLModelCache]:
    """Return the process-wide dense and sparse FastEmbed model caches.

    EmbeddingRouter is intentionally lightweight and may be constructed for
    individual requests or chunks. The ONNX models, however, must outlive a
    router instance so their configured idle TTL is meaningful.
    """
    global _shared_model_caches
    with _shared_cache_lock:
        if _shared_model_caches is None:
            idle_timeout = _idle_timeout()
            _shared_model_caches = (
                TTLModelCache(idle_timeout=idle_timeout),
                TTLModelCache(idle_timeout=idle_timeout),
            )
        return _shared_model_caches


class FastEmbedEmbeddingProvider:
    def __init__(self):
        # Share caches across every provider and router in this Python process.
        # A model is reinitialized only after TTL eviction or process restart.
        self._models, self._sparse_models = _get_shared_model_caches()

    @staticmethod
    def _build_model_kwargs(spec: EmbeddingSpec):
        import os

        kwargs = {}
        if spec.extra:
            kwargs.update(spec.extra)

        cache_dir = kwargs.get("cache_dir")
        if not cache_dir:
            from backend.retrieval.config_loader import resolve_local_model_cache_dir

            cache_dir = resolve_local_model_cache_dir({})

        if cache_dir:
            kwargs["cache_dir"] = os.path.expandvars(os.path.expanduser(str(cache_dir)))

        return kwargs

    def _get_model(self, spec: EmbeddingSpec):
        from fastembed import TextEmbedding

        key = f"{spec.model}:{spec.device or 'default'}"

        def _loader():
            kwargs = self._build_model_kwargs(spec)
            logger.debug(
                "Initializing FastEmbed dense model='%s' cache_dir='%s'",
                spec.model,
                kwargs.get("cache_dir"),
            )
            return TextEmbedding(
                model_name=spec.model,
                **kwargs,
            )

        return self._models.get(key, _loader)

    def _get_sparse_model(self, spec: EmbeddingSpec):
        from fastembed import SparseTextEmbedding

        key = f"{spec.model}:{spec.device or 'default'}"

        def _loader():
            kwargs = self._build_model_kwargs(spec)
            logger.debug(
                "Initializing FastEmbed sparse model='%s' cache_dir='%s'",
                spec.model,
                kwargs.get("cache_dir"),
            )
            return SparseTextEmbedding(
                model_name=spec.model,
                **kwargs,
            )

        return self._sparse_models.get(key, _loader)

    def embed(self, texts: List[str], spec: EmbeddingSpec) -> EmbeddingResult:
        if spec.vector_type == "sparse":
            return self._embed_sparse(texts, spec)
        else:
            return self._embed_dense(texts, spec)

    def _embed_dense(self, texts: List[str], spec: EmbeddingSpec) -> EmbeddingResult:
        model = self._get_model(spec)
        vectors = [list(v) for v in model.embed(texts, batch_size=spec.batch_size)]

        return EmbeddingResult(
            vectors=vectors,
            model=spec.model,
            dimensions=spec.dimensions,
            runtime=spec.runtime,
            usage={
                "input_text_count": len(texts),
                "local": True,
            },
            metadata={
                "provider": spec.provider,
                "batch_size": spec.batch_size,
                "vector_type": "dense",
            },
        )

    def _embed_sparse(self, texts: List[str], spec: EmbeddingSpec) -> EmbeddingResult:
        model = self._get_sparse_model(spec)
        sparse_vectors = list(model.embed(texts, batch_size=spec.batch_size))

        # Convert SparseEmbedding objects to dict format (indices + values)
        vectors = []
        for sv in sparse_vectors:
            if hasattr(sv, "indices") and hasattr(sv, "values"):
                indices = sv.indices.tolist() if hasattr(sv.indices, "tolist") else list(sv.indices)
                values = sv.values.tolist() if hasattr(sv.values, "tolist") else list(sv.values)
                vectors.append({"indices": indices, "values": values})
            elif hasattr(sv, "items"):
                # Fallback for dict-like sparse vectors
                items = list(sv.items())
                vectors.append({
                    "indices": [int(idx) for idx, _ in items],
                    "values": [float(val) for _, val in items]
                })
            else:
                vectors.append({"indices": [], "values": []})

        return EmbeddingResult(
            vectors=vectors,
            model=spec.model,
            dimensions=None,  # Sparse embeddings don't have fixed dimensions
            runtime=spec.runtime,
            usage={
                "input_text_count": len(texts),
                "local": True,
            },
            metadata={
                "provider": spec.provider,
                "batch_size": spec.batch_size,
                "vector_type": "sparse",
            },
        )
