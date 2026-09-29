"""Tests for the domain shard resolver (multi-collection domains)."""

import os

os.environ.setdefault("OPENAI_API_KEY", "test")

import pytest

from backend.core.config import DomainEmbeddingEntry
from backend.services import domain_shards
from backend.services.domain_shards import DomainShard, resolve_domain_shards

shard_module = domain_shards


class _FakeClient:
    def __init__(self, collections, counts=None):
        self._collections = collections

    def get_collections(self):
        return type(
            "Result",
            (),
            {"collections": [type("C", (), {"name": n}) for n in self._collections]},
        )()

    def close(self):
        pass


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


# ---------------------------------------------------------------------------
# Fan-out search across shards
# ---------------------------------------------------------------------------


class _FakeView:
    def __init__(self, name, caps, results):
        self.collection_name = name
        self._caps = caps
        self._results = results
        self.searched_modes = []

    def _get_collection_vector_capabilities(self):
        return self._caps

    def search_similar(self, **kwargs):
        self.searched_modes.append("dense")
        return list(self._results)

    def search_similar_sparse(self, **kwargs):
        self.searched_modes.append("sparse")
        return list(self._results)

    def search_similar_hybrid(self, **kwargs):
        self.searched_modes.append("hybrid")
        return list(self._results)


class _FakeDB(_FakeView):
    """Primary db that is also a view and can produce shard views."""

    def __init__(self, shard_specs):
        primary_caps, primary_results = shard_specs["index_semi"]
        super().__init__("index_semi", primary_caps, primary_results)
        self._shard_specs = shard_specs
        self.views = {}

    def for_collection(self, name):
        caps, results = self._shard_specs[name]
        view = _FakeView(name, caps, results)
        self.views[name] = view
        return view


def _result(i):
    return {"id": i, "score": 0.9 - i * 0.1, "payload": {"text": f"result {i}"}}


def test_search_shards_single_collection_fast_path(domain_config, monkeypatch):
    # "plain"-style domain: no extras declared -> no Qdrant probe, direct search
    db = _FakeDB({"index_semi": ({"has_dense": True, "has_sparse": False}, [_result(1)])})
    result = shard_module.search_shards(
        db, active_domain="plain", query="q", search_mode="dense", top_k=5
    )
    assert [r["id"] for r in result["results"]] == [1]
    assert "shards_searched" not in result
    assert db.searched_modes == ["dense"]


def test_search_shards_fans_out_and_rrf_merges(domain_config, monkeypatch):
    monkeypatch.setattr(
        "qdrant_client.QdrantClient",
        lambda **kwargs: _FakeClient(
            ["index_semi", "index_semi_docling_v1"], {}
        ),
    )
    db = _FakeDB(
        {
            "index_semi": ({"has_dense": True, "has_sparse": False}, [_result(1), _result(2)]),
            "index_semi_docling_v1": (
                {"has_dense": True, "has_sparse": False},
                [_result(3), _result(4)],
            ),
        }
    )
    result = shard_module.search_shards(
        db, active_domain="semi", query="q", search_mode="dense", top_k=3
    )
    assert set(result["shards_searched"]) == {"index_semi", "index_semi_docling_v1"}
    assert len(result["results"]) == 3  # merged capped at top_k
    merged_ids = {r["id"] for r in result["results"]}
    assert merged_ids == {1, 2, 3, 4} or len(merged_ids) == 3
    # both shards were queried through views
    assert set(db.views) == {"index_semi", "index_semi_docling_v1"}


def test_search_shards_skips_declared_but_missing_shard(domain_config, monkeypatch):
    # docling shard declared but not created in Qdrant yet: only the primary
    # exists, so the search takes the single-collection shape
    monkeypatch.setattr(
        "qdrant_client.QdrantClient",
        lambda **kwargs: _FakeClient(["index_semi"], {}),
    )
    db = _FakeDB({"index_semi": ({"has_dense": True, "has_sparse": False}, [_result(1)])})
    result = shard_module.search_shards(
        db, active_domain="semi", query="q", search_mode="dense", top_k=5
    )
    assert [r["id"] for r in result["results"]] == [1]
    assert "shards_searched" not in result
    assert db.views == {}  # no shard views were created


def test_search_shards_resolves_mode_per_shard(domain_config, monkeypatch):
    monkeypatch.setattr(
        "qdrant_client.QdrantClient",
        lambda **kwargs: _FakeClient(
            ["index_semi", "index_semi_docling_v1"], {}
        ),
    )
    db = _FakeDB(
        {
            # primary is dense-only; docling shard has sparse too
            "index_semi": ({"has_dense": True, "has_sparse": False}, [_result(1)]),
            "index_semi_docling_v1": (
                {"has_dense": True, "has_sparse": True},
                [_result(2)],
            ),
        }
    )
    result = shard_module.search_shards(
        db, active_domain="semi", query="q", search_mode="hybrid", top_k=5
    )
    assert result["shards_searched"] == {
        "index_semi": "dense",  # fell back per its layout
        "index_semi_docling_v1": "hybrid",
    }


def test_fan_out_primitive_passes_none_for_single_shard(domain_config):
    seen = []

    def search_call(name):
        seen.append(name)
        return [_result(1)]

    merged = shard_module.fan_out("plain", top_k=5, search_call=search_call)
    assert seen == [None]
    assert [r["id"] for r in merged] == [1]


def test_fan_out_primitive_merges_multi_shard(domain_config, monkeypatch):
    monkeypatch.setattr(
        "qdrant_client.QdrantClient",
        lambda **kwargs: _FakeClient(
            ["index_semi", "index_semi_docling_v1"], {}
        ),
    )
    seen = []

    def search_call(name):
        seen.append(name)
        return [_result(1)] if name == "index_semi" else [_result(2), _result(3)]

    merged = shard_module.fan_out("semi", top_k=2, search_call=search_call)
    assert seen == ["index_semi", "index_semi_docling_v1"]
    assert len(merged) == 2  # capped at top_k
