import logging
import threading
from typing import Any, Dict, List

from backend.retrieval.model_cache import TTLModelCache
from backend.retrieval.schemas import EmbeddingSpec, EmbeddingResult


logger = logging.getLogger(__name__)


_shared_cache_lock = threading.Lock()
_shared_model_cache: TTLModelCache | None = None

# BGE-M3's native context window; callers may override via spec.extra.
_M3_DEFAULT_MAX_LENGTH = 8192


def _idle_timeout() -> int:
    """Resolve the model idle TTL from settings (imported lazily to avoid cycles)."""
    try:
        from backend.core.config import settings
        return int(getattr(settings, "model_cache_idle_ttl_seconds", 300))
    except Exception:
        return 300


def _get_shared_model_cache() -> TTLModelCache:
    """Process-wide cache so the (large) BGE-M3 weights outlive router instances."""
    global _shared_model_cache
    with _shared_cache_lock:
        if _shared_model_cache is None:
            _shared_model_cache = TTLModelCache(
                idle_timeout=_idle_timeout(), label="retrieval"
            )
        return _shared_model_cache


def _resolve_device(spec: EmbeddingSpec) -> str:
    if spec.device:
        return spec.device
    try:
        import torch

        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def _lexical_to_sparse_dict(lexical_weights: Dict[Any, Any]) -> Dict[str, List[float]]:
    """Convert FlagEmbedding lexical weights {vocab_id: weight} to the
    {indices, values} shape the rest of the pipeline uses."""
    if not lexical_weights:
        return {"indices": [], "values": []}
    items = sorted((int(k), float(v)) for k, v in lexical_weights.items())
    return {
        "indices": [idx for idx, _ in items],
        "values": [val for _, val in items],
    }


class Bgem3EmbeddingProvider:
    """Unified single-pass provider backed by FlagEmbedding's BGEM3FlagModel.

    One encode() produces dense (1024-d) + sparse lexical weights. The
    ColBERT head is available in the model but unused — Stage-B rescoring
    goes through the existing ColBERTv2 reranker.
    """

    def __init__(self):
        self._models = _get_shared_model_cache()

    def _get_model(self, spec: EmbeddingSpec):
        from FlagEmbedding import BGEM3FlagModel

        device = _resolve_device(spec)
        key = f"{spec.model}:{device}"

        def _loader():
            cache_dir = (spec.extra or {}).get("cache_dir")
            kwargs: Dict[str, Any] = {}
            if cache_dir:
                import os

                kwargs["cache_dir"] = os.path.expandvars(
                    os.path.expanduser(str(cache_dir))
                )
            logger.info(
                "Initializing BGE-M3 model='%s' device='%s'",
                spec.model,
                device,
            )
            return BGEM3FlagModel(
                spec.model,
                use_fp16=bool((spec.extra or {}).get("use_fp16", False)),
                devices=device,
                **kwargs,
            )

        return self._models.get(key, _loader)

    def embed(self, texts: List[str], spec: EmbeddingSpec) -> EmbeddingResult:
        model = self._get_model(spec)
        want_sparse = spec.vector_type == "sparse" or "sparse" in spec.emits
        max_length = int(
            (spec.extra or {}).get("max_length") or _M3_DEFAULT_MAX_LENGTH
        )

        out = model.encode(
            list(texts),
            batch_size=spec.batch_size,
            max_length=max_length,
            return_dense=True,
            return_sparse=want_sparse,
            return_colbert_vecs=False,
        )

        sparse_vectors = (
            [_lexical_to_sparse_dict(w) for w in out["lexical_weights"]]
            if want_sparse
            else None
        )

        # Sparse query path consumes sparse dicts via `vectors`, matching the
        # fastembed sparse provider's contract.
        if spec.vector_type == "sparse":
            return EmbeddingResult(
                vectors=list(sparse_vectors or []),
                model=spec.model,
                dimensions=None,
                runtime=spec.runtime,
                usage={"input_text_count": len(texts), "local": True},
                metadata={
                    "provider": spec.provider,
                    "batch_size": spec.batch_size,
                    "vector_type": "sparse",
                },
            )

        dense = [list(map(float, v)) for v in out["dense_vecs"]]
        return EmbeddingResult(
            vectors=dense,
            model=spec.model,
            dimensions=int(out["dense_vecs"].shape[-1]),
            runtime=spec.runtime,
            sparse_vectors=sparse_vectors,
            usage={"input_text_count": len(texts), "local": True},
            metadata={
                "provider": spec.provider,
                "batch_size": spec.batch_size,
                "vector_type": "dense",
                "emits": list(spec.emits),
            },
        )
