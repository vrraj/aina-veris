"""Runtime-adjustable configuration surface.

Exposes a curated allowlist of ``settings`` fields through the admin API and
the Veris Configuration page so operators can tune memory and latency knobs
without restarting the service. Changes are in-memory only: a restart resets
every value to its env/default.

Each tunable is declared with bounds and an optional ``cache_label`` — when
the setting is a model-cache TTL, applying it also propagates the new
``idle_timeout`` to live ``TTLModelCache`` instances carrying that label.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import psutil

from backend.core import settings
from backend.retrieval.model_cache import set_idle_timeout_for_label

logger = logging.getLogger(__name__)

TUNABLES: List[Dict[str, Any]] = [
    {
        "key": "pdf_docling_free_converter_mb",
        "label": "Docling memory floor",
        "unit": "MB",
        "min": 0,
        "max": 65536,
        "description": (
            "Free RAM required to keep the Docling converter cached after "
            "conversion; below this floor it is ejected before embedding "
            "models load. 0 disables the check."
        ),
    },
    {
        "key": "embed_batch_size_override",
        "label": "Embedding batch size",
        "unit": "chunks",
        "min": 0,
        "max": 512,
        "description": (
            "Override for the per-model embedding batch size from the model "
            "registry (smaller batches lower peak memory during indexing). "
            "0 = use the registry default."
        ),
    },
    {
        "key": "model_cache_idle_ttl_seconds",
        "label": "Retrieval model idle TTL",
        "unit": "seconds",
        "min": 30,
        "max": 86400,
        "description": (
            "How long an idle dense/sparse/reranker model stays resident. "
            "Propagates to live retrieval caches immediately."
        ),
        "cache_label": "retrieval",
    },
    {
        "key": "ingestion_model_cache_idle_ttl_seconds",
        "label": "Ingestion model idle TTL",
        "unit": "seconds",
        "min": 30,
        "max": 86400,
        "description": (
            "How long an idle Docling converter stays resident. Propagates "
            "to the live converter cache immediately."
        ),
        "cache_label": "ingestion",
    },
    {
        "key": "mcp_tool_timeout_seconds",
        "label": "MCP tool timeout",
        "unit": "seconds",
        "min": 1,
        "max": 600,
        "description": "Hard cap on external MCP tool calls; timed-out calls are cancelled.",
    },
    {
        "key": "top_k",
        "label": "Default top_k",
        "unit": "docs",
        "min": 1,
        "max": 200,
        "description": "Default retrieval breadth when a request does not pass top_k.",
        "note": (
            "Chat and search paths read this per request; a few raw-search "
            "helpers bind it at startup — restart to guarantee it everywhere."
        ),
    },
]

_BY_KEY = {t["key"]: t for t in TUNABLES}


def _tunable_entry(spec: Dict[str, Any]) -> Dict[str, Any]:
    entry = {
        "key": spec["key"],
        "label": spec["label"],
        "value": getattr(settings, spec["key"]),
        "unit": spec["unit"],
        "min": spec["min"],
        "max": spec["max"],
        "description": spec["description"],
    }
    if spec.get("note"):
        entry["note"] = spec["note"]
    return entry


def _read_cgroup_memory() -> Optional[Dict[str, int]]:
    """Read the container cgroup memory limit/usage when running in Docker.

    psutil reports VM-wide numbers inside a container; the cgroup files
    report the container's own cap (when one is configured)."""
    candidates = [
        ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory.current"),
        (
            "/sys/fs/cgroup/memory/memory.limit_in_bytes",
            "/sys/fs/cgroup/memory/memory.usage_in_bytes",
        ),
    ]
    for limit_path, usage_path in candidates:
        try:
            with open(limit_path) as fh:
                raw_limit = fh.read().strip()
            with open(usage_path) as fh:
                raw_usage = fh.read().strip()
            limit_bytes = int(raw_limit)
            usage_bytes = int(raw_usage)
        except (OSError, ValueError):
            continue
        if limit_bytes <= 0 or limit_bytes >= 1 << 60:  # 'max'/no cap sentinel
            continue
        return {
            "limit_mb": limit_bytes // (1024 * 1024),
            "used_mb": usage_bytes // (1024 * 1024),
        }
    return None


def memory_status() -> Dict[str, Any]:
    """Live memory view: VM/system totals, this process, cgroup, model caches."""
    vm = psutil.virtual_memory()
    swap = psutil.swap_memory()
    proc = psutil.Process(os.getpid())

    try:
        from backend.services.model_cache_admin import cache_status

        caches = cache_status()["caches"]
    except Exception as exc:
        logger.debug("model cache status unavailable: %s", exc)
        caches = []

    # GPU context — reported, not tunable: device/provider choices bind at
    # model load. torch may not be imported yet on a quiet process, so this
    # is deliberately cheap when it isn't.
    import sys as _sys

    _torch = _sys.modules.get("torch")
    mps_available = None
    if _torch is not None:
        try:
            mps_available = bool(_torch.backends.mps.is_available())
        except Exception:
            mps_available = None

    return {
        "acceleration": {
            "docling_device": str(
                getattr(settings, "pdf_docling_accelerator_device", "auto")
            ),
            "mps_available": mps_available,
        },
        "system": {
            "total_mb": vm.total // (1024 * 1024),
            "available_mb": vm.available // (1024 * 1024),
            "used_percent": vm.percent,
        },
        "swap": {
            "total_mb": swap.total // (1024 * 1024),
            "used_mb": swap.used // (1024 * 1024),
        },
        "process_rss_mb": proc.memory_info().rss // (1024 * 1024),
        "container": _read_cgroup_memory(),
        "model_caches": caches,
    }


def runtime_status() -> Dict[str, Any]:
    """Everything the Veris Configuration page renders in one payload."""
    return {
        "tunables": [_tunable_entry(spec) for spec in TUNABLES],
        "memory": memory_status(),
        "persistence": "in-memory — values reset to env/defaults on restart",
    }


def apply_updates(updates: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and apply runtime updates to the live settings object.

    Raises ``ValueError`` on an unknown key, unparseable value, or value
    outside the declared range. All valid updates apply atomically — if any
    key fails validation, nothing is changed."""
    parsed: Dict[str, float] = {}
    for key, value in updates.items():
        spec = _BY_KEY.get(key)
        if spec is None:
            raise ValueError(f"Unknown tunable '{key}'")
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"'{key}' expects a number, got {value!r}")
        if number < spec["min"] or number > spec["max"]:
            raise ValueError(
                f"'{key}' must be between {spec['min']} and {spec['max']} {spec['unit']}"
            )
        parsed[key] = number

    applied: Dict[str, Any] = {}
    propagated: Dict[str, int] = {}
    for key, number in parsed.items():
        spec = _BY_KEY[key]
        current = getattr(settings, key)
        value = int(number) if isinstance(current, int) else number
        setattr(settings, key, value)
        applied[key] = value
        label = spec.get("cache_label")
        if label:
            propagated[key] = set_idle_timeout_for_label(label, int(number))
    logger.info("Runtime config applied: %s", applied)
    return {"applied": applied, "caches_updated": propagated}
