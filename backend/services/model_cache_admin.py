"""Admin surface over the process-local TTLModelCache instances.

Aggregates the model caches that live in module singletons (embedding
providers, rerankers, Docling converters) so the management API and UI
can inspect them, eject entries, and force reloads. "Local" location is
derived from the local_models_registry cache dirs.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional, Tuple

from backend.retrieval.model_cache import TTLModelCache

logger = logging.getLogger(__name__)


def _caches() -> Dict[str, Tuple[TTLModelCache, str]]:
    """name -> (cache, description). Missing modules are skipped."""
    caches: Dict[str, Tuple[TTLModelCache, str]] = {}
    try:
        from backend.retrieval.providers.fastembed_embedding_provider import (
            _get_shared_model_caches,
        )
        dense, sparse = _get_shared_model_caches()
        caches["embeddings-dense"] = (dense, "Dense FastEmbed (e.g. bge-base-en-v1.5)")
        caches["embeddings-sparse"] = (sparse, "Sparse FastEmbed (SPLADE)")
    except Exception as exc:
        logger.debug("embedding caches unavailable: %s", exc)

    try:
        from backend.retrieval.providers.bgem3_embedding_provider import (
            _get_shared_model_cache,
        )
        caches["embeddings-m3"] = (
            _get_shared_model_cache(),
            "BGE-M3 unified dense+sparse (FlagEmbedding)",
        )
    except Exception as exc:
        logger.debug("bgem3 cache unavailable: %s", exc)

    try:
        from backend.retrieval import retrieval_eval_service as eval_service
        caches["colbert-reranker"] = (eval_service._colbert_cache, "ColBERT late interaction")
        caches["cross-encoder-reranker"] = (
            eval_service._cross_encoder_cache,
            "Cross-encoder reranker",
        )
    except Exception as exc:
        logger.debug("reranker caches unavailable: %s", exc)

    try:
        from backend.extractor.docling_pdf_extractor import _get_converter_cache
        caches["docling-converters"] = (
            _get_converter_cache(),
            "Docling DocumentConverter (layout/TableFormer/OCR/VLM)",
        )
    except Exception as exc:
        logger.debug("docling converter cache unavailable: %s", exc)

    return caches


def _loaded_keys(caches: Dict[str, Tuple[TTLModelCache, str]]) -> Dict[str, str]:
    """cache key -> cache name, across all caches."""
    loaded: Dict[str, str] = {}
    for name, (cache, _desc) in caches.items():
        for key in cache.stats()["models"].keys():
            loaded[str(key)] = name
    return loaded


def _local_models(loaded: Dict[str, str]) -> list:
    """Registry local models with their on-disk cache_dir and memory state."""
    try:
        from backend.retrieval.config_loader import (
            load_retrieval_config,
            resolve_local_model_cache_dir,
        )
        local = load_retrieval_config().get("local_models") or {}
    except Exception as exc:
        logger.debug("local models registry unavailable: %s", exc)
        return []

    out = []
    for kind, cfg in local.items():
        if not isinstance(cfg, dict):
            continue
        name = str(cfg.get("name") or "")
        try:
            cache_dir = resolve_local_model_cache_dir(cfg)
        except Exception:
            cache_dir = str(cfg.get("cache_dir") or "")
        loaded_key, in_memory_in = next(
            ((key, cache_name) for key, cache_name in loaded.items() if name and key.startswith(name)),
            (None, None),
        )
        out.append(
            {
                "kind": kind,
                "name": name,
                "enabled": bool(cfg.get("enabled", True)),
                "cache_dir": cache_dir,
                "on_disk": bool(cache_dir) and os.path.isdir(cache_dir),
                "in_memory": in_memory_in is not None,
                "loaded_in": in_memory_in,
                "loaded_key": loaded_key,
            }
        )
    return out


def _providers_of(model: Any) -> Optional[list]:
    """Best-effort: the ONNX Runtime providers a loaded fastembed model
    actually bound (model.model is the InferenceSession)."""
    try:
        session = getattr(getattr(model, "model", None), "model", None)
        get_providers = getattr(session, "get_providers", None)
        if callable(get_providers):
            return list(get_providers())
    except Exception:
        pass
    return None


def cache_status() -> Dict[str, Any]:
    """Snapshot every known model cache plus local-model locations."""
    caches = _caches()
    loaded = _loaded_keys(caches)

    cache_entries = []
    for name, (cache, desc) in caches.items():
        stats = cache.stats()
        cache_entries.append(
            {
                "name": name,
                "description": desc,
                "idle_timeout_s": stats["idle_timeout_s"],
                "models": [
                    {
                        "key": str(key),
                        "idle_secs": meta["idle_secs"],
                        "providers": _providers_of(cache._cache.get(key, (None, 0))[0]),
                    }
                    for key, meta in stats["models"].items()
                ],
            }
        )

    return {
        "local_models_root": os.path.expanduser(
            str(os.getenv("LOCAL_MODELS_CACHE_PATH") or "~/models")
        ),
        "hf_home": os.getenv("HF_HOME"),
        "caches": cache_entries,
        "local_models": _local_models(loaded),
    }


def _resolve_key(cache: TTLModelCache, key: str) -> Any:
    """Map the string form shown by the API back to the original cache key
    (caches may use non-string keys, e.g. the Docling converter tuples)."""
    for existing in list(cache._cache.keys()):
        if str(existing) == key:
            return existing
    return key


def eject_model(cache_name: str, key: Optional[str] = None) -> Dict[str, Any]:
    """Eject one model (key) or an entire cache (key=None)."""
    caches = _caches()
    if cache_name not in caches:
        raise KeyError(f"Unknown cache '{cache_name}'")
    cache = caches[cache_name][0]

    if key is None:
        cache.clear()
        logger.info("Model cache cleared: %s", cache_name)
        return {"cache": cache_name, "ejected": "all"}

    ejected = cache.eject(_resolve_key(cache, key))
    if ejected:
        logger.info("Model ejected: cache=%s key=%s", cache_name, key)
    return {"cache": cache_name, "key": key, "ejected": ejected}


def reload_model(cache_name: str, key: str) -> Dict[str, Any]:
    """Eject and immediately re-load a model via its recorded loader."""
    caches = _caches()
    if cache_name not in caches:
        raise KeyError(f"Unknown cache '{cache_name}'")
    cache = caches[cache_name][0]
    cache.reload(_resolve_key(cache, key))
    logger.info("Model reloaded: cache=%s key=%s", cache_name, key)
    return {"cache": cache_name, "key": key, "reloaded": True}


def eject_all() -> Dict[str, Any]:
    """Drop every cached model in the process."""
    cleared = []
    for name, (cache, _desc) in _caches().items():
        cache.clear()
        cleared.append(name)
    logger.info("All model caches cleared: %s", cleared)
    return {"cleared": cleared}
