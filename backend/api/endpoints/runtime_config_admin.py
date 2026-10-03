"""API endpoints for the runtime-tunable configuration surface.

Powers the Veris Configuration page: operators can inspect the curated
tunable set (current value, range, description), watch live system/process
memory, and adjust values without restarting the service. Changes are
in-memory only and reset to env/defaults on restart.
"""

import logging

from fastapi import APIRouter, HTTPException, Request

from backend.api.security import enforce_origin_host
from backend.core.schemas import RuntimeConfigUpdateInput
from backend.services import runtime_config

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get(
    "/config/runtime",
    tags=["4. Index Admin"],
    summary="List runtime-tunable settings and live memory status",
)
async def get_runtime_config(request: Request):
    """Tunables table (value, unit, range, description) plus system/process/
    container memory and resident model caches."""
    enforce_origin_host(request)
    return runtime_config.runtime_status()


@router.post(
    "/config/runtime",
    tags=["4. Index Admin"],
    summary="Apply runtime-tunable settings without a restart",
)
async def update_runtime_config(payload: RuntimeConfigUpdateInput, request: Request):
    """Validate and apply updates to live settings. TTL tunables also
    propagate to live model caches. In-memory only — a restart resets all
    values to env/defaults."""
    enforce_origin_host(request)
    if not payload.updates:
        raise HTTPException(status_code=422, detail="updates must not be empty")
    try:
        return runtime_config.apply_updates(payload.updates)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
