"""Tests for the domain shard resolver (multi-collection domains)."""

import os

os.environ.setdefault("OPENAI_API_KEY", "test")

import pytest

from backend.core.config import DomainEmbeddingEntry
from backend.services import domain_shards
from backend.services.domain_shards import DomainShard, resolve_domain_shards


@pytest.fixture
def domain_config(monkeypatch):
    cfg = {
        "semi": {
            "collection_name": "index_semi",
            "collections": [
                {"name": "index_semi_docling_v1", "pipeline": "docling"},
            ],
            "embedding_model_key": "local:dense_default",
            "model_type": "local",
            "vector_type": "hybrid",
            "search_mode": "hybrid",
        },
        "plain": {
            "collection_name": "index_plain",
            "embedding_model_key": "local:dense_default",
            "model_type": "local",
            "vector_type": "hybrid",
            "search_mode": "hybrid",
        },
    }
    monkeypatch.setattr(domain_shards.settings, "DOMAIN_EMBEDDING_CONFIG", cfg)
    monkeypatch.setattr(domain_shards.settings, "active_domain", "plain")
    return cfg


def test_multi_shard_domain_resolves_primary_then_extras(domain_config):
    shards = resolve_domain_shards("semi")
    assert shards == [
        DomainShard(name="index_semi", primary=True),
        DomainShard(name="index_semi_docling_v1", pipeline="docling"),
    ]


def test_legacy_domain_resolves_single_primary(domain_config):
    assert resolve_domain_shards("plain") == [
        DomainShard(name="index_plain", primary=True)
    ]


def test_unknown_domain_falls_back_to_default(domain_config):
    # active_domain is "plain"; an unknown request resolves to its shards
    shards = resolve_domain_shards("does-not-exist")
    assert shards == [DomainShard(name="index_plain", primary=True)]


def test_none_domain_uses_configured_default(domain_config):
    assert resolve_domain_shards(None) == [
        DomainShard(name="index_plain", primary=True)
    ]


def test_resolver_skips_blank_and_duplicate_extras(domain_config):
    cfg = domain_config["semi"]
    cfg["collections"] = [
        {"name": "  ", "pipeline": "docling"},
        {"name": "index_semi", "pipeline": "docling"},  # dup of primary
        {"name": "index_semi_docling_v1", "pipeline": "docling"},
        {"name": "index_semi_docling_v1"},  # dup extra
    ]
    shards = resolve_domain_shards("semi")
    assert [s.name for s in shards] == ["index_semi", "index_semi_docling_v1"]


def test_entry_validation_rejects_duplicate_collection_names():
    with pytest.raises(ValueError, match="unique names"):
        DomainEmbeddingEntry(
            collection_name="index_a",
            collections=[{"name": "x"}, {"name": "x"}],
            profile="local-hybrid",
        )


def test_entry_validation_rejects_primary_repeated_in_collections():
    with pytest.raises(ValueError, match="must not repeat collection_name"):
        DomainEmbeddingEntry(
            collection_name="index_a",
            collections=[{"name": "index_a", "pipeline": "docling"}],
            profile="local-hybrid",
        )


def test_entry_validation_rejects_blank_collection_name():
    with pytest.raises(ValueError, match="non-empty name"):
        DomainEmbeddingEntry(
            collection_name="index_a",
            collections=[{"name": "  "}],
            profile="local-hybrid",
        )


def test_existing_shard_names_intersects_with_qdrant(monkeypatch):
    class FakeCollections:
        collections = [
            type("C", (), {"name": "index_semi"}),
            type("C", (), {"name": "other"}),
        ]

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def get_collections(self):
            return FakeCollections()

        def close(self):
            pass

    import backend.services.domain_shards as mod

    monkeypatch.setattr("qdrant_client.QdrantClient", FakeClient)
    existing = domain_shards.existing_shard_names(
        ["index_semi", "index_semi_docling_v1"]
    )
    assert existing == {"index_semi"}
