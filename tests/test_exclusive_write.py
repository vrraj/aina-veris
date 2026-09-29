"""Tests for exclusive write: a document lives in only one shard per domain."""

import os

os.environ.setdefault("OPENAI_API_KEY", "test")

import pytest

import backend.services.domain_shards as shard_module
from backend.services import pdf_docling_service as service_module
from backend.core.schemas import PDFDoclingInput


# ---------------------------------------------------------------------------
# Shard helpers (count / delete across collections)
# ---------------------------------------------------------------------------


class _FakeClient:
    def __init__(self, collections, counts, deletions=None):
        self._collections = collections
        self._counts = counts
        self.deletions = deletions if deletions is not None else []

    def get_collections(self):
        return type(
            "Result",
            (),
            {
                "collections": [
                    type("C", (), {"name": n}) for n in self._collections
                ]
            },
        )()

    def count(self, collection_name, count_filter=None, exact=True):
        return type("Count", (), {"count": self._counts.get(collection_name, 0)})()

    def delete(self, collection_name, points_selector=None):
        self.deletions.append(collection_name)

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
    }
    monkeypatch.setattr(shard_module.settings, "DOMAIN_EMBEDDING_CONFIG", cfg)
    monkeypatch.setattr(shard_module.settings, "active_domain", "semi")
    return cfg


def _install_fake_client(monkeypatch, collections, counts, deletions=None):
    fake = _FakeClient(collections, counts, deletions)
    monkeypatch.setattr("qdrant_client.QdrantClient", lambda **kwargs: fake)
    return fake


def test_count_document_in_shards_excludes_target_and_missing(domain_config, monkeypatch):
    _install_fake_client(
        monkeypatch,
        collections=["index_semi", "index_semi_docling_v1"],
        counts={"index_semi": 5, "index_semi_docling_v1": 3},
    )
    counts = shard_module.count_document_in_shards(
        "semi", "file://doc.pdf", exclude_shard="index_semi_docling_v1"
    )
    # only the primary shard is checked (docling shard excluded, none missing)
    assert counts == {"index_semi": 5}


def test_count_document_in_shards_skips_nonexistent_shards(domain_config, monkeypatch):
    # declared docling shard does not exist in Qdrant yet
    _install_fake_client(
        monkeypatch,
        collections=["index_semi"],
        counts={"index_semi": 2},
    )
    counts = shard_module.count_document_in_shards(
        "semi", "file://doc.pdf", exclude_shard="index_semi_docling_v1"
    )
    assert counts == {"index_semi": 2}


def test_delete_document_from_shards_deletes_only_populated(domain_config, monkeypatch):
    deletions = []
    _install_fake_client(
        monkeypatch,
        collections=["index_semi", "index_semi_docling_v1"],
        counts={"index_semi": 4, "index_semi_docling_v1": 0},
        deletions=deletions,
    )
    deleted = shard_module.delete_document_from_shards(
        "file://doc.pdf", ["index_semi", "index_semi_docling_v1"]
    )
    assert deleted == {"index_semi": 4}
    assert deletions == ["index_semi"]


# ---------------------------------------------------------------------------
# Docling pipeline: refuse / migrate
# ---------------------------------------------------------------------------


class _FakePlan:
    chunks = []
    omitted_chunks = 0


class _FakeExtraction:
    document_id = "sha256:abc"
    title = "Doc"
    page_count = 2
    warnings = []
    items = [1, 2]
    items_with_regions = 2


def _make_input(**overrides):
    import base64

    body = {
        "file": base64.b64encode(b"%PDF-fake-bytes").decode("ascii"),
        "filename": "doc.pdf",
    }
    body.update(overrides)
    return PDFDoclingInput(**body)


@pytest.fixture
def docling_pipeline(monkeypatch):
    """Stub everything around the exclusive-write logic."""
    calls = {"deleted": None}

    monkeypatch.setattr(
        service_module, "extract_pdf_document", lambda b, s: _FakeExtraction()
    )
    monkeypatch.setattr(service_module, "count_docling_points_for_document", lambda d, i: 0)
    monkeypatch.setattr(
        service_module, "resolve_docling_collection_name", lambda d: "index_semi_docling_v1"
    )
    monkeypatch.setattr(service_module, "build_chunks", lambda e, max_chunks=None, skip_sections=None: _FakePlan())
    monkeypatch.setattr(service_module, "save_artifact", lambda e, d: ("/tmp/a.json", "internal://a"))
    monkeypatch.setattr(service_module, "save_source_pdf", lambda b, i, d: "/tmp/a.pdf")
    monkeypatch.setattr(
        service_module,
        "index_docling_chunks",
        lambda *a, **k: {
            "collection_name": "index_semi_docling_v1",
            "vectors_indexed": 3,
            "tokens_used": 10,
            "stale_points_deleted": 0,
        },
    )
    monkeypatch.setattr(service_module, "get_embedding_rate_per_mm_tokens", lambda: 0.0)
    monkeypatch.setattr(service_module.settings, "check_document_indexed", True, raising=False)

    def fake_delete(source, shard_names):
        calls["deleted"] = (source, list(shard_names))
        return dict.fromkeys(shard_names, 4)

    monkeypatch.setattr(service_module, "delete_document_from_shards", fake_delete)
    return calls


def test_refuses_without_force_when_document_in_other_shard(docling_pipeline, monkeypatch):
    monkeypatch.setattr(
        service_module,
        "count_document_in_shards",
        lambda d, s, exclude_shard=None: {"index_semi": 5},
    )
    result = service_module.index_pdf_docling(_make_input())
    assert result["already_indexed"] is True
    assert result["existing_collection"] == "index_semi"
    assert "migrate" in result["hint"].lower()
    assert docling_pipeline["deleted"] is None  # nothing was deleted


def test_force_migrates_from_other_shard(docling_pipeline, monkeypatch):
    monkeypatch.setattr(
        service_module,
        "count_document_in_shards",
        lambda d, s, exclude_shard=None: {"index_semi": 5},
    )
    result = service_module.index_pdf_docling(_make_input(force_delete=True))
    assert result["chunks_indexed"] == 3
    assert result["migrated_from_collections"] == {"index_semi": 4}
    assert docling_pipeline["deleted"] == ("file://doc.pdf", ["index_semi"])


def test_no_other_shard_writes_normally(docling_pipeline, monkeypatch):
    monkeypatch.setattr(
        service_module,
        "count_document_in_shards",
        lambda d, s, exclude_shard=None: {},
    )
    result = service_module.index_pdf_docling(_make_input())
    assert result["chunks_indexed"] == 3
    assert result["migrated_from_collections"] == {}
    assert docling_pipeline["deleted"] is None


def test_estimate_skips_exclusive_write_check(docling_pipeline, monkeypatch):
    called = {"count": 0}

    def counting(d, s, exclude_shard=None):
        called["count"] += 1
        return {"index_semi": 5}

    monkeypatch.setattr(service_module, "count_document_in_shards", counting)
    result = service_module.index_pdf_docling(_make_input(estimate=True))
    assert "estimate only" in result["message"].lower()
    assert called["count"] == 0
