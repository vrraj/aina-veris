"""Tests for the /index-pdf-docling route and its service layer.

starlette's TestClient is incompatible with the installed httpx version, so
the route handler is invoked directly with a minimal fake Request.
"""

import asyncio
import os

os.environ.setdefault("OPENAI_API_KEY", "test")

import pytest

import backend.main as main_module
from backend.api.endpoints import pdf_docling as endpoint_module
from backend.core.schemas import PDFDoclingInput
from backend.services import pdf_docling_service as service_module
from backend.services.pdf_docling_service import DoclingPipelineError


class _FakeRequest:
    def __init__(self):
        # host allowlists are configured via .env; use the default local host
        self.headers = {"host": "localhost:8100"}


def _run(pdf_input):
    return asyncio.run(endpoint_module.index_pdf_docling(pdf_input, _FakeRequest()))


def _make_input(**overrides):
    body = {
        "url": "https://example.com/lm358.pdf",
    }
    body.update(overrides)
    return PDFDoclingInput(**body)


class TestRoute:
    def test_successful_index_returns_service_payload(self, monkeypatch):
        expected = {
            "message": "PDF indexed via Docling pipeline",
            "pipeline": "pdf_docling_v1",
            "document_id": "sha256:abc",
            "chunks_indexed": 12,
        }
        monkeypatch.setattr(endpoint_module, "run_docling_indexing", lambda pi: expected)
        result = _run(_make_input())
        assert result == expected

    def test_pipeline_error_maps_to_422(self, monkeypatch):
        def raise_pipeline(pi):
            raise DoclingPipelineError("Docling conversion failed: bad PDF")

        monkeypatch.setattr(endpoint_module, "run_docling_indexing", raise_pipeline)
        with pytest.raises(endpoint_module.HTTPException) as exc_info:
            _run(_make_input())
        assert exc_info.value.status_code == 422
        assert "Docling conversion failed" in exc_info.value.detail

    def test_missing_docling_maps_to_503(self, monkeypatch):
        def boom(pi):
            raise DoclingPipelineError(
                "docling is not installed; the /index-pdf-docling pipeline requires it"
            )

        monkeypatch.setattr(endpoint_module, "run_docling_indexing", boom)
        with pytest.raises(endpoint_module.HTTPException) as exc_info:
            _run(_make_input())
        assert exc_info.value.status_code == 503

    def test_disabled_feature_maps_to_503(self, monkeypatch):
        monkeypatch.setattr(endpoint_module.settings, "pdf_docling_enabled", False, raising=False)
        with pytest.raises(endpoint_module.HTTPException) as exc_info:
            _run(_make_input())
        assert exc_info.value.status_code == 503

    def test_unexpected_error_surfaces_as_500(self, monkeypatch):
        def boom(pi):
            raise RuntimeError("surprise")

        monkeypatch.setattr(endpoint_module, "run_docling_indexing", boom)
        with pytest.raises(endpoint_module.HTTPException) as exc_info:
            _run(_make_input())
        assert exc_info.value.status_code == 500
        assert "surprise" in exc_info.value.detail

    def test_legacy_pdf_route_untouched(self):
        # /pdf must still be registered with its original contract
        routes = {getattr(r, "path", "") for r in main_module.app.routes}
        assert "/pdf" in routes
        assert "/index-pdf-docling" in routes


class TestInputModel:
    def test_skip_sections_defaults_to_empty(self):
        model = PDFDoclingInput(url="https://example.com/x.pdf")
        assert model.skip_sections == []
        assert model.estimate is False
        assert model.force_delete is False


class TestService:
    def test_estimate_writes_nothing(self, monkeypatch):
        from docling_fixtures import build_extraction

        extraction = build_extraction()
        monkeypatch.setattr(service_module, "_fetch_pdf", lambda url: b"pdf-bytes")
        monkeypatch.setattr(
            service_module, "extract_pdf_document", lambda b, s: extraction
        )

        def no_index(*a, **k):
            raise AssertionError("index_docling_chunks must not be called in estimate mode")

        def no_save(*a, **k):
            raise AssertionError("save_artifact must not be called in estimate mode")

        monkeypatch.setattr(service_module, "index_docling_chunks", no_index)
        monkeypatch.setattr(service_module, "save_artifact", no_save)

        result = service_module.index_pdf_docling(
            _make_input(url="https://x/lm358.pdf", estimate=True)
        )
        assert result["message"] == "Estimate only"
        assert result["chunks_planned"] > 0
        assert result["document_id"].startswith("sha256:")
        assert "provenance_coverage" in result
        assert "parsing_warnings" in result

    def test_missing_file_and_url_raises(self):
        with pytest.raises(DoclingPipelineError):
            service_module.index_pdf_docling(PDFDoclingInput())

    def test_already_indexed_returns_confirmation(self, monkeypatch):
        from docling_fixtures import build_extraction

        extraction = build_extraction()
        monkeypatch.setattr(service_module, "_fetch_pdf", lambda url: b"pdf-bytes")
        monkeypatch.setattr(
            service_module, "extract_pdf_document", lambda b, s: extraction
        )
        monkeypatch.setattr(
            service_module, "count_docling_points_for_document", lambda d, doc: 7
        )

        def no_index(*a, **k):
            raise AssertionError("must not index without confirmation")

        monkeypatch.setattr(service_module, "index_docling_chunks", no_index)

        result = service_module.index_pdf_docling(_make_input(url="https://x/lm358.pdf"))
        assert result["already_indexed"] is True
        assert result["vectors_found"] == 7
        assert result["confirmation_required"] is True

    def test_happy_path_calls_indexing(self, monkeypatch):
        from docling_fixtures import build_extraction

        extraction = build_extraction()
        monkeypatch.setattr(service_module, "_fetch_pdf", lambda url: b"pdf-bytes")
        monkeypatch.setattr(
            service_module, "extract_pdf_document", lambda b, s: extraction
        )
        monkeypatch.setattr(
            service_module, "count_docling_points_for_document", lambda d, doc: 0
        )
        monkeypatch.setattr(
            service_module,
            "save_artifact",
            lambda e, d: ("/tmp/x.json", f"internal://documents/{e.document_id}"),
        )
        calls = {}

        def fake_index(chunks, **kw):
            calls.update(kw)
            calls["n"] = len(chunks)
            return {
                "collection_name": "c_docling_v1",
                "vectors_indexed": len(chunks),
                "tokens_used": 10,
                "stale_points_deleted": 0,
            }

        monkeypatch.setattr(service_module, "index_docling_chunks", fake_index)

        result = service_module.index_pdf_docling(
            _make_input(url="https://x/lm358.pdf", force_delete=True)
        )
        assert result["chunks_indexed"] > 0
        assert result["pipeline"] == "pdf_docling_v1"
        assert "embedding_cost" in result
        assert calls["source_key"] == "https://x/lm358.pdf"
        assert calls["artifact_uri"].startswith("internal://documents/")
