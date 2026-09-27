"""Tests for the Docling indexing adapter (spec stage 3, additive).

Qdrant and the embedding router are mocked: these tests verify payload
shape, stable IDs, transactional swap ordering, and scoping — not vector
math.
"""

import os

os.environ.setdefault("OPENAI_API_KEY", "test")

import pytest

from docling_fixtures import build_extraction

from backend.extractor.docling_chunks import build_chunks
from backend.extractor.docling_pdf_extractor import (
    PDF_DOCLING_PIPELINE,
    PDF_DOCLING_PIPELINE_VERSION,
)
from backend.services import pdf_docling_indexing as svc


class _FakeCollectionInfo:
    def __init__(self):
        self.config = type("Cfg", (), {})()
        self.config.params = type("Params", (), {})()
        self.config.params.vectors = {"dense": {"size": 8}}
        self.config.params.sparse_vectors = {"sparse": {}}


class _FakeQdrantClient:
    def __init__(self):
        self.upserted = []
        self.deleted = []
        self.scroll_results = []  # list of (points, offset) pages
        self.fail_upsert = False

    def get_collection(self, name):
        return _FakeCollectionInfo()

    def upsert(self, collection_name, points):
        if self.fail_upsert:
            raise RuntimeError("upsert failed")
        self.upserted.extend(points)

    def delete(self, collection_name, points_selector):
        ids = points_selector.points
        self.deleted.extend(ids)

    def scroll(self, collection_name, scroll_filter, with_payload, with_vectors, limit, offset):
        if self.scroll_results:
            return self.scroll_results.pop(0)
        return [], None


class _FakeQdrantDB:
    def __init__(self):
        self.client = _FakeQdrantClient()

    def generate_sparse_embeddings(self, text):
        return {"indices": [1, 2], "values": [0.5, 0.5]}


def _patch_stack(monkeypatch, fail_upsert=False, existing_ids=None):
    fake = _FakeQdrantDB()
    fake.client.fail_upsert = fail_upsert
    if existing_ids:
        fake.client.scroll_results = [([type("P", (), {"id": i})() for i in existing_ids], None)]

    fake_spec = {
        "model": "test-embed",
        "provider": "test",
        "runtime": "test",
        "batch_size": 4,
        "dimensions": 8,
        "normalize": True,
        "device": None,
        "extra": {},
    }
    monkeypatch.setattr(svc, "build_docling_qdrant", lambda active_domain: fake)
    monkeypatch.setattr(svc, "get_embedding_spec_for_domain", lambda active_domain: fake_spec)
    monkeypatch.setattr(
        svc,
        "generate_embeddings_with_retrieval",
        lambda texts, active_domain: [[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8] for _ in texts],
    )
    return fake


def _plan_chunks():
    extraction = build_extraction()
    plan = build_chunks(extraction)
    return extraction, plan


class TestPayload:
    def test_payload_is_allowlisted_and_shaped(self):
        extraction, plan = _plan_chunks()
        chunk = plan.chunks[0]
        payload = svc.build_docling_payload(
            chunk,
            domain="default",
            source_key=extraction.source,
            source=extraction.source,
            artifact_uri="internal://documents/x",
            spec_dict={"model": "m", "provider": "p", "runtime": "r"},
            total_chunks=len(plan.chunks),
        )
        assert set(payload) <= svc._PAYLOAD_FIELDS
        assert payload["pipeline"] == PDF_DOCLING_PIPELINE
        assert payload["pipeline_version"] == PDF_DOCLING_PIPELINE_VERSION
        assert payload["document_type"] == "pdf"
        assert payload["url_lower"] == extraction.source.lower()
        assert payload["base_url"] == extraction.source
        assert payload["section_path"] == chunk.section_path
        assert payload["chunk_id"] == chunk.chunk_id
        assert payload["text"] == chunk.embedding_text
        assert payload["display_text"] == chunk.display_text
        assert payload["artifact_uri"] == "internal://documents/x"
        assert payload["item_refs"]
        assert all(r.get("granularity") for r in payload["regions"])

    def test_section_subsection_derived_from_path(self):
        extraction, plan = _plan_chunks()
        deep = next(
            (c for c in plan.chunks if len(c.section_path) >= 2), plan.chunks[0]
        )
        payload = svc.build_docling_payload(
            deep,
            domain="default",
            source_key=extraction.source,
            source=extraction.source,
            artifact_uri=None,
            spec_dict={"model": "m", "provider": "p", "runtime": "r"},
            total_chunks=len(plan.chunks),
        )
        assert payload["subsection"] == deep.section_path[-1]
        assert payload["section"] == deep.section_path[-2]

    def test_non_allowlisted_field_rejected(self, monkeypatch):
        extraction, plan = _plan_chunks()
        chunk = plan.chunks[0]
        monkeypatch.setattr(svc, "_PAYLOAD_FIELDS", svc._PAYLOAD_FIELDS - {"token_count"})
        with pytest.raises(ValueError):
            svc.build_docling_payload(
                chunk,
                domain="default",
                source_key=extraction.source,
                source=extraction.source,
                artifact_uri=None,
                spec_dict={"model": "m", "provider": "p", "runtime": "r"},
                total_chunks=1,
            )


class TestStableIds:
    def test_point_ids_deterministic(self):
        id1 = svc.stable_point_id("default", "1.0", "s", "doc", "chunk-1")
        id2 = svc.stable_point_id("default", "1.0", "s", "doc", "chunk-1")
        id3 = svc.stable_point_id("default", "1.0", "s", "doc", "chunk-2")
        assert id1 == id2
        assert id1 != id3

    def test_reindex_produces_same_ids_no_duplicates(self, monkeypatch):
        _, plan = _plan_chunks()
        fake = _patch_stack(monkeypatch)
        kwargs = dict(
            active_domain=None,
            source_key="https://example.com/lm358.pdf",
            source="https://example.com/lm358.pdf",
        )
        r1 = svc.index_docling_chunks(plan.chunks, **kwargs)
        r2 = svc.index_docling_chunks(plan.chunks, **kwargs)
        ids1 = [p.id for p in fake.client.upserted[: r1["vectors_indexed"]]]
        ids2 = [p.id for p in fake.client.upserted[r1["vectors_indexed"] :]]
        assert ids1 == ids2  # stable IDs: overwrite, no duplicates
        assert r1["vectors_indexed"] == len(plan.chunks)
        assert not fake.client.deleted  # no orphans on identical reindex


class TestTransactionalSwap:
    def test_stale_points_retired_after_upsert(self, monkeypatch):
        _, plan = _plan_chunks()
        fake = _patch_stack(monkeypatch, existing_ids={"stale-point-1", "stale-point-2"})
        svc.index_docling_chunks(
            plan.chunks,
            active_domain=None,
            source_key="https://example.com/lm358.pdf",
            source="https://example.com/lm358.pdf",
        )
        # delete happens only after the upsert
        assert set(fake.client.deleted) == {"stale-point-1", "stale-point-2"}
        assert len(fake.client.upserted) == len(plan.chunks)

    def test_failed_upsert_leaves_previous_version(self, monkeypatch):
        _, plan = _plan_chunks()
        fake = _patch_stack(monkeypatch, fail_upsert=True)
        with pytest.raises(RuntimeError):
            svc.index_docling_chunks(
                plan.chunks,
                active_domain=None,
                source_key="https://example.com/lm358.pdf",
                source="https://example.com/lm358.pdf",
            )
        assert fake.client.upserted == []
        assert fake.client.deleted == []  # nothing retired on failure

    def test_empty_chunks_write_nothing(self, monkeypatch):
        fake = _patch_stack(monkeypatch)
        result = svc.index_docling_chunks(
            [],
            active_domain=None,
            source_key="https://example.com/lm358.pdf",
            source="https://example.com/lm358.pdf",
        )
        assert result["vectors_indexed"] == 0
        assert fake.client.upserted == []


class TestCollectionNaming:
    def test_dedicated_collection_name(self):
        name = svc.resolve_docling_collection_name(None)
        assert name.endswith("_docling_v1")
        assert name != "document-index"  # never the legacy default
