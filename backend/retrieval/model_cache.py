"""Time-based idle eviction cache for ONNX/FastEmbed models.

Models are loaded lazily on first access and evicted after being idle
for longer than ``idle_timeout`` seconds.  Evicted models are released
via ``gc.collect()`` to return ONNX Runtime arena memory to the OS.
"""

from __future__ import annotations

import gc
import logging
import time
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_IDLE_TIMEOUT = 300  # 5 minutes


class TTLModelCache:
    """Cache that evicts entries idle for longer than ``idle_timeout``.

    Each entry stores the model object alongside a last-access timestamp.
    On every ``get`` the timestamp is refreshed and a sweep evicts any
    entries that have been idle past the timeout.
    """

    def __init__(self, idle_timeout: int = DEFAULT_IDLE_TIMEOUT):
        self._cache: Dict[str, Tuple[Any, float]] = {}
        self.idle_timeout = idle_timeout

    def get(self, key: str, loader: Callable[[], Any]) -> Any:
        """Return the cached model for *key*, loading via *loader* on miss.

        Also sweeps idle entries before resolving the request.
        """
        self._sweep()
        if key in self._cache:
            model, _ = self._cache[key]
            self._cache[key] = (model, time.monotonic())
            return model

        logger.debug("Loading model into cache: key=%s", key)
        model = loader()
        self._cache[key] = (model, time.monotonic())
        return model

    def _sweep(self) -> None:
        """Evict models idle for longer than ``idle_timeout``."""
        if self.idle_timeout <= 0:
            return
        now = time.monotonic()
        evicted: list[str] = []
        for key, (_, last_access) in list(self._cache.items()):
            if now - last_access > self.idle_timeout:
                evicted.append(key)
                del self._cache[key]

        if evicted:
            logger.info(
                "Evicted %d idle model(s) from cache: %s",
                len(evicted),
                evicted,
            )
            gc.collect()

    def clear(self) -> None:
        """Drop all cached models immediately."""
        self._cache.clear()
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
