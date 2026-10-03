"""API endpoints for managing process-local model caches.

Lets the UI inspect which embedding/reranker/Docling models are resident
in memory, where their weights live on disk, and eject or reload them
without restarting the service.
"""

import logging

from fastapi import APIRouter, HTTPException, Request

from backend.api.security import enforce_origin_host
from backend.core.schemas import ModelCacheActionInput
from backend.services import model_cache_admin

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get(
    "/models/cache",
    tags=["4. Index Admin"],
    summary="List cached models, idle times, and on-disk locations",
)
async def get_model_cache(request: Request):
    """Snapshot of all TTLModelCache instances: which models are in
    memory, how long they've been idle, and the local (on-disk) model
    cache locations for the registry-configured local models."""
    enforce_origin_host(request)
    return model_cache_admin.cache_status()


@router.post(
    "/models/cache/eject",
    tags=["4. Index Admin"],
    summary="Eject a model (or a whole cache) from memory",
)
def eject_model(payload: ModelCacheActionInput, request: Request):
    """Drop one cached model by key, or the entire cache when `key` is
    omitted. Memory is released via gc.collect(); the model reloads
    lazily on next use."""
    enforce_origin_host(request)
    try:
        return model_cache_admin.eject_model(payload.cache, payload.key)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post(
    "/models/cache/reload",
    tags=["4. Index Admin"],
    summary="Eject and immediately re-load a cached model",
)
def reload_model(payload: ModelCacheActionInput, request: Request):
    """Evict the model then synchronously re-load it via its recorded
    loader — e.g. after swapping weights on disk."""
    enforce_origin_host(request)
    if not payload.key:
        raise HTTPException(status_code=422, detail="key is required for reload")
    try:
        return model_cache_admin.reload_model(payload.cache, payload.key)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post(
    "/models/cache/eject-all",
    tags=["4. Index Admin"],
    summary="Eject every cached model in the process",
)
def eject_all_models(request: Request):
    """Clear all model caches (embeddings, rerankers, Docling converters)."""
    enforce_origin_host(request)
    return model_cache_admin.eject_all()
