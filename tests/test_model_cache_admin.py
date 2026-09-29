"""Tests for TTLModelCache eject/reload and the admin service surface."""

import os

os.environ.setdefault("OPENAI_API_KEY", "test")

import pytest

from backend.retrieval.model_cache import TTLModelCache
from backend.services import model_cache_admin as admin


class TestTTLModelCache:
    def test_eject_returns_presence(self):
        cache = TTLModelCache(idle_timeout=60)
        cache.get("k", lambda: object())
        assert cache.eject("k") is True
        assert cache.eject("k") is False
        assert "k" not in cache

    def test_reload_uses_recorded_loader(self):
        cache = TTLModelCache(idle_timeout=60)
        calls = {"n": 0}

        def loader():
            calls["n"] += 1
            return object()

        first = cache.get("k", loader)
        second = cache.reload("k")
        assert calls["n"] == 2
        assert second is not first

    def test_reload_unknown_key_raises(self):
        cache = TTLModelCache(idle_timeout=60)
        with pytest.raises(KeyError):
            cache.reload("missing")

    def test_sweep_and_clear_drop_loaders(self):
        import time
        cache = TTLModelCache(idle_timeout=0)
        cache.get("k", lambda: object())
        assert "k" in cache  # TTL=0 sweeps on next access only
        cache._sweep()
        assert cache.idle_timeout == 0  # sweep returns early when ttl<=0

        cache2 = TTLModelCache(idle_timeout=60)
        cache2.get("k", lambda: object())
        cache2.clear()
        with pytest.raises(KeyError):
            cache2.reload("k")


class TestAdminService:
    def _patched(self, monkeypatch):
        cache = TTLModelCache(idle_timeout=60)
        monkeypatch.setattr(
            admin, "_caches", lambda: {"test-cache": (cache, "desc")}
        )
        return cache

    def test_status_shape(self, monkeypatch):
        cache = self._patched(monkeypatch)
        cache.get("m1", lambda: object())
        status = admin.cache_status()
        entry = status["caches"][0]
        assert entry["name"] == "test-cache"
        assert entry["models"][0]["key"] == "m1"
        assert entry["models"][0]["idle_secs"] >= 0
        assert "local_models" in status

    def test_eject_single_and_all(self, monkeypatch):
        cache = self._patched(monkeypatch)
        cache.get("m1", lambda: object())
        cache.get("m2", lambda: object())
        assert admin.eject_model("test-cache", "m1")["ejected"] is True
        assert admin.eject_model("test-cache")["ejected"] == "all"
        assert len(cache._cache) == 0

    def test_eject_unknown_cache(self, monkeypatch):
        self._patched(monkeypatch)
        with pytest.raises(KeyError):
            admin.eject_model("nope", "x")

    def test_reload(self, monkeypatch):
        cache = self._patched(monkeypatch)
        cache.get("m2", lambda: "model-2")
        assert admin.reload_model("test-cache", "m2")["reloaded"] is True

    def test_eject_non_string_key_via_str_form(self, monkeypatch):
        """Docling converters key on tuples; the API must resolve str(key)
        back to the original object."""
        cache = self._patched(monkeypatch)
        tkey = (False, "accurate", "auto", 8, False, "smolvlm", 1.0)
        cache.get(tkey, lambda: object())
        assert admin.eject_model("test-cache", str(tkey))["ejected"] is True

    def test_reload_non_string_key_via_str_form(self, monkeypatch):
        cache = self._patched(monkeypatch)
        tkey = (False, "accurate", "auto", 8)
        cache.get(tkey, lambda: object())
        assert admin.reload_model("test-cache", str(tkey))["reloaded"] is True
