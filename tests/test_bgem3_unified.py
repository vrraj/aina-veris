"""Tests for the BGE-M3 unified single-pass path (local:m3_default).

Model weights are mocked — these tests verify routing, spec resolution,
sparse-vocabulary compatibility, and the one-pass indexing branch.
"""

import os

os.environ.setdefault("OPENAI_API_KEY", "test")

import numpy as np
import pytest

from backend.retrieval.schemas import EmbeddingResult, EmbeddingSpec


class TestSpecResolution:
    def test_m3_domain_resolves_bgem3_runtime_and_emits(self):
        from backend.api.domain_indexing import get_embedding_spec_for_domain

        spec = get_embedding_spec_for_domain("semiconductor_datasheets_m3")
        assert spec["runtime"] == "bgem3"
        assert spec["model"] == "BAAI/bge-m3"
        assert spec["dimensions"] == 1024
        assert set(spec["emits"]) == {"dense", "sparse"}

    def test_legacy_hybrid_domain_unchanged(self):
        from backend.api.domain_indexing import get_embedding_spec_for_domain

        spec = get_embedding_spec_for_domain("semiconductor_datasheets_docling")
        assert spec["runtime"] == "fastembed"
        assert spec["model"] == "BAAI/bge-base-en-v1.5"
        assert spec["dimensions"] == 768
        assert spec["emits"] == []

    def test_m3_profile_resolves_modes(self):
        from backend.api.domain_indexing import resolve_domain_config

        cfg = resolve_domain_config("semiconductor_datasheets_m3")
        assert cfg["embedding_model_key"] == "local:m3_default"
        assert cfg["model_type"] == "local"
        assert cfg["vector_type"] == "hybrid"


class _FakeM3Model:
    """Stands in for FlagEmbedding.BGEM3FlagModel."""

    def __init__(self):
        self.encode_calls = 0
        self.last_kwargs = None

    def encode(self, sentences, **kwargs):
        self.encode_calls += 1
        self.last_kwargs = kwargs
        n = len(sentences)
        return {
            "dense_vecs": np.ones((n, 1024), dtype=np.float32) * 0.5,
            "lexical_weights": [{7: 0.9, 2: 0.1} for _ in sentences],
            "colbert_vecs": np.ones((n, 1024), dtype=np.float32),
        }


def _patched_provider(monkeypatch):
    from backend.retrieval.providers import bgem3_embedding_provider as prov

    model = _FakeM3Model()
    monkeypatch.setattr(prov.Bgem3EmbeddingProvider, "_get_model", lambda self, spec: model)
    return prov.Bgem3EmbeddingProvider(), model


class TestBgem3Provider:
    def test_dense_spec_returns_dense_plus_sparse_bundle(self, monkeypatch):
        provider, model = _patched_provider(monkeypatch)
        spec = EmbeddingSpec(
            task="embedding", runtime="bgem3", provider="local",
            model="BAAI/bge-m3", dimensions=1024, batch_size=8,
            emits=["dense", "sparse"],
        )
        result = provider.embed(["a", "b", "c"], spec)

        assert model.encode_calls == 1
        assert model.last_kwargs["return_colbert_vecs"] is False
        assert len(result.vectors) == 3
        assert len(result.vectors[0]) == 1024
        assert result.dimensions == 1024
        # sparse dicts aligned, indices sorted ascending
        assert result.sparse_vectors[0] == {"indices": [2, 7], "values": [0.1, 0.9]}
        assert len(result.sparse_vectors) == 3

    def test_sparse_spec_returns_sparse_dicts_as_vectors(self, monkeypatch):
        provider, model = _patched_provider(monkeypatch)
        spec = EmbeddingSpec(
            task="embedding", runtime="bgem3", provider="local",
            model="BAAI/bge-m3", vector_type="sparse",
            emits=["dense", "sparse"],
        )
        result = provider.embed(["q"], spec)

        assert model.encode_calls == 1
        assert result.vectors == [{"indices": [2, 7], "values": [0.1, 0.9]}]
        assert result.dimensions is None

    def test_dense_only_emit_skips_sparse(self, monkeypatch):
        provider, model = _patched_provider(monkeypatch)
        spec = EmbeddingSpec(
            task="embedding", runtime="bgem3", provider="local",
            model="BAAI/bge-m3", vector_type="dense", emits=["dense"],
        )
        result = provider.embed(["x"], spec)

        assert model.last_kwargs["return_sparse"] is False
        assert result.sparse_vectors is None


class TestSparseVocabularyBinding:
    """Sparse vectors are vocabulary-bound to their model: an M3 domain's
    sparse query spec must point at BGE-M3, not the global SPLADE entry."""

    def _qdrant(self, model_key):
        from backend.db.qdrant_db import QdrantDB

        db = QdrantDB.__new__(QdrantDB)
        db.embedding_model_key = model_key
        return db

    def test_m3_domain_uses_bgem3_sparse_spec(self):
        spec = self._qdrant("local:m3_default")._sparse_embedding_spec()
        assert spec.runtime == "bgem3"
        assert spec.model == "BAAI/bge-m3"
        assert spec.vector_type == "sparse"

    def test_splade_domain_keeps_fastembed_sparse_spec(self):
        spec = self._qdrant("local:dense_default")._sparse_embedding_spec()
        assert spec.runtime == "fastembed"
        assert "Splade" in spec.model or "splade" in spec.model.lower()


class TestOnePassIndexing:
    def test_emits_domain_makes_one_embed_call_no_sparse_pass(self, monkeypatch):
        from backend.services import pdf_docling_indexing as svc
        from tests.test_pdf_docling_indexing import (
            _FakeQdrantDB,
            _plan_chunks,
        )

        fake = _FakeQdrantDB()
        spec = {
            "model": "BAAI/bge-m3", "provider": "local", "runtime": "bgem3",
            "batch_size": 4, "dimensions": 1024, "normalize": True,
            "device": None, "extra": {}, "emits": ["dense", "sparse"],
        }
        bundle_calls = []

        def _bundle(texts, active_domain):
            bundle_calls.append(len(texts))
            return EmbeddingResult(
                vectors=[[0.1] * 1024 for _ in texts],
                model="BAAI/bge-m3", dimensions=1024, runtime="bgem3",
                sparse_vectors=[{"indices": [2, 7], "values": [0.1, 0.9]} for _ in texts],
            )

        monkeypatch.setattr(svc, "build_docling_qdrant", lambda active_domain: fake)
        monkeypatch.setattr(svc, "get_embedding_spec_for_domain", lambda active_domain: spec)
        monkeypatch.setattr(svc, "generate_embedding_bundle_with_retrieval", _bundle)
        monkeypatch.setattr(
            svc, "generate_embeddings_with_retrieval",
            lambda *a, **k: pytest.fail("two-pass dense path must not run"),
        )

        extraction, plan = _plan_chunks()
        result = svc.index_docling_chunks(
            plan.chunks,
            active_domain="semiconductor_datasheets_m3",
            source_key=extraction.source,
            source=extraction.source,
        )

        assert result["vectors_indexed"] == len(plan.chunks)
        assert sum(bundle_calls) == len(plan.chunks)
        # The dedicated sparse model pass must never run for a unified model.
        assert fake.sparse_batch_calls == 0
        assert fake.sparse_single_calls == 0
        point = fake.client.upserted[0]
        assert set(point.vector) == {"dense", "sparse"}
        assert point.vector["sparse"].indices == [2, 7]

    def test_non_emits_domain_keeps_two_pass_path(self, monkeypatch):
        from backend.services import pdf_docling_indexing as svc
        from tests.test_pdf_docling_indexing import _plan_chunks, _patch_stack

        fake = _patch_stack(monkeypatch)
        monkeypatch.setattr(
            svc, "generate_embedding_bundle_with_retrieval",
            lambda *a, **k: pytest.fail("unified path must not run"),
        )

        extraction, plan = _plan_chunks()
        result = svc.index_docling_chunks(
            plan.chunks,
            active_domain="semiconductor_datasheets_docling",
            source_key=extraction.source,
            source=extraction.source,
        )

        assert result["vectors_indexed"] == len(plan.chunks)
        assert fake.sparse_batch_calls > 0
