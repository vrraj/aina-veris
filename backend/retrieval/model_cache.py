"""Time-based idle eviction cache for ONNX/FastEmbed models.

Models are loaded lazily on first access and evicted after being idle
for longer than ``idle_timeout`` seconds.  Evicted models are released
via ``gc.collect()`` to return ONNX Runtime arena memory to the OS.
"""

from __future__ import annotations

import gc
import logging
import threading
import time
import weakref
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_IDLE_TIMEOUT = 300  # 5 minutes
_SWEEP_INTERVAL_SECONDS = 60

# Every live TTLModelCache, held weakly so caches themselves can be
# garbage-collected. One daemon thread sweeps them all — eviction must not
# depend on someone calling get(), or idle models would stay resident
# indefinitely on a quiet server.
_ALL_CACHES: "weakref.WeakSet[TTLModelCache]" = weakref.WeakSet()
_SWEEPER_LOCK = threading.Lock()
_SWEEPER_STARTED = False


def _sweep_registered_caches_once() -> None:
    for cache in list(_ALL_CACHES):
        try:
            cache.sweep()
        except Exception:
            logger.exception("Background model-cache sweep failed")


def _sweep_registered_caches_forever() -> None:
    while True:
        time.sleep(_SWEEP_INTERVAL_SECONDS)
        _sweep_registered_caches_once()


def _ensure_sweeper() -> None:
    global _SWEEPER_STARTED
    with _SWEEPER_LOCK:
        if _SWEEPER_STARTED:
            return
        _SWEEPER_STARTED = True
    threading.Thread(
        target=_sweep_registered_caches_forever,
        name="ttl-model-cache-sweeper",
        daemon=True,
    ).start()


def set_idle_timeout_for_label(label: str, seconds: int) -> int:
    """Update ``idle_timeout`` on every live cache tagged with *label*.

    Cache instances capture their TTL at construction, so a runtime change
    to a TTL setting needs this propagation to affect existing caches —
    new caches resolve the setting at build time on their own. Returns the
    number of caches updated."""
    updated = 0
    for cache in list(_ALL_CACHES):
        if getattr(cache, "label", None) == label:
            cache.idle_timeout = seconds
            updated += 1
    return updated


class TTLModelCache:
    """Cache that evicts entries idle for longer than ``idle_timeout``.

    Each entry stores the model object alongside a last-access timestamp.
    On every ``get`` the timestamp is refreshed and a sweep evicts any
    entries that have been idle past the timeout. A shared background
    thread also sweeps every live cache periodically, so idle memory is
    released even when no requests arrive.
    """

    def __init__(self, idle_timeout: int = DEFAULT_IDLE_TIMEOUT, label: Optional[str] = None):
        self._cache: Dict[str, Tuple[Any, float]] = {}
        self._loaders: Dict[str, Callable[[], Any]] = {}
        self.idle_timeout = idle_timeout
        self.label = label
        _ALL_CACHES.add(self)
        _ensure_sweeper()

    def __contains__(self, key) -> bool:
        return key in self._cache

    def get(self, key: str, loader: Callable[[], Any]) -> Any:
        """Return the cached model for *key*, loading via *loader* on miss.

        Also sweeps idle entries before resolving the request. The loader
        is remembered per key so entries can be reloaded on demand.
        """
        self._sweep()
        if key in self._cache:
            model, _ = self._cache[key]
            self._cache[key] = (model, time.monotonic())
            self._loaders[key] = loader
            return model

        logger.debug("Loading model into cache: key=%s", key)
        model = loader()
        self._cache[key] = (model, time.monotonic())
        self._loaders[key] = loader
        return model

    def eject(self, key: str) -> bool:
        """Drop a single cached model immediately. Returns True if present."""
        existed = key in self._cache
        self._cache.pop(key, None)
        self._loaders.pop(key, None)
        if existed:
            gc.collect()
        return existed

    def reload(self, key: str) -> Any:
        """Evict *key* and immediately re-load it via its recorded loader."""
        loader = self._loaders.get(key)
        if loader is None:
            raise KeyError(key)
        self.eject(key)
        return self.get(key, loader)

    def sweep(self) -> None:
        """Evict models idle for longer than ``idle_timeout``."""
        if self.idle_timeout <= 0:
            return
        now = time.monotonic()
        evicted: list = []
        for key, (_, last_access) in list(self._cache.items()):
            if now - last_access > self.idle_timeout:
                evicted.append(key)
                del self._cache[key]
                self._loaders.pop(key, None)

        if evicted:
            logger.info(
                "Evicted %d idle model(s) from cache: %s",
                len(evicted),
                evicted,
            )
            gc.collect()

    # Backwards-compatible alias for existing callers.
    _sweep = sweep

    def clear(self) -> None:
        """Drop all cached models immediately."""
        self._cache.clear()
        self._loaders.clear()
        gc.collect()

    def stats(self) -> Dict[str, Any]:
        """Return a snapshot of cache contents and idle times."""
        now = time.monotonic()
        return {
            "cached_models": len(self._cache),
            "idle_timeout_s": self.idle_timeout,
            "models": {
                key: {"idle_secs": int(now - last_access)}
                for key, (_, last_access) in self._cache.items()
            },
        }
