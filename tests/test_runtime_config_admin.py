"""Tests for the runtime-tunable config service and TTL propagation."""

import os

os.environ.setdefault("OPENAI_API_KEY", "test")

import pytest

from backend.core import settings
from backend.retrieval.model_cache import TTLModelCache
from backend.services import runtime_config


@pytest.fixture
def restore_settings():
    """Snapshot mutable tunables and restore them after the test."""
    keys = [t["key"] for t in runtime_config.TUNABLES]
    original = {k: getattr(settings, k) for k in keys}
    yield
    for k, v in original.items():
        setattr(settings, k, v)


class TestRuntimeStatus:
    def test_status_shape(self):
        status = runtime_config.runtime_status()
        tunables = {t["key"]: t for t in status["tunables"]}
        assert "pdf_docling_free_converter_mb" in tunables
        assert tunables["pdf_docling_free_converter_mb"]["value"] == (
            settings.pdf_docling_free_converter_mb
        )
        for t in tunables.values():
            assert t["min"] <= t["max"]
            assert t["unit"]

        memory = status["memory"]
        assert memory["system"]["total_mb"] > 0
        assert memory["process_rss_mb"] > 0
        assert isinstance(memory["model_caches"], list)


class TestApplyUpdates:
    def test_applies_value(self, restore_settings):
        result = runtime_config.apply_updates({"pdf_docling_free_converter_mb": 8192})
        assert result["applied"]["pdf_docling_free_converter_mb"] == 8192
        assert settings.pdf_docling_free_converter_mb == 8192

    def test_rejects_unknown_key(self, restore_settings):
        before = settings.pdf_docling_free_converter_mb
        with pytest.raises(ValueError, match="Unknown tunable"):
            runtime_config.apply_updates({"not_a_setting": 1})
        assert settings.pdf_docling_free_converter_mb == before

    def test_rejects_out_of_range(self, restore_settings):
        with pytest.raises(ValueError, match="between"):
            runtime_config.apply_updates({"top_k": 0})
        with pytest.raises(ValueError, match="between"):
            runtime_config.apply_updates({"embed_batch_size_override": -5})

    def test_atomic_on_partial_failure(self, restore_settings):
        before_floor = settings.pdf_docling_free_converter_mb
        with pytest.raises(ValueError):
            runtime_config.apply_updates(
                {"pdf_docling_free_converter_mb": 9999, "bogus": 1}
            )
        assert settings.pdf_docling_free_converter_mb == before_floor

    def test_int_fields_reject_fractional_semantics(self, restore_settings):
        runtime_config.apply_updates({"model_cache_idle_ttl_seconds": 120})
        assert settings.model_cache_idle_ttl_seconds == 120


class TestTTLPropagation:
    def test_retrieval_label_updates_live_caches(self, restore_settings):
        cache = TTLModelCache(idle_timeout=300, label="retrieval")
        other = TTLModelCache(idle_timeout=900, label="ingestion")
        runtime_config.apply_updates({"model_cache_idle_ttl_seconds": 60})
        assert cache.idle_timeout == 60
        assert other.idle_timeout == 900

    def test_ingestion_label_updates_live_caches(self, restore_settings):
        cache = TTLModelCache(idle_timeout=900, label="ingestion")
        runtime_config.apply_updates({"ingestion_model_cache_idle_ttl_seconds": 120})
        assert cache.idle_timeout == 120


class TestEmbedBatchSizeOverride:
    def test_override_flows_into_domain_spec(self, restore_settings):
        from backend.api.domain_indexing import get_embedding_spec_for_domain

        default = get_embedding_spec_for_domain("finance")["batch_size"]
        assert default > 0  # registry default

        runtime_config.apply_updates({"embed_batch_size_override": 7})
        assert get_embedding_spec_for_domain("finance")["batch_size"] == 7

        runtime_config.apply_updates({"embed_batch_size_override": 0})
        assert (
            get_embedding_spec_for_domain("finance")["batch_size"] == default
        )
