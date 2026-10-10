"""Citation geometry emitted by the legacy /pdf extractor.

Both parse paths (PyMuPDF and pymupdf4llm) attach regions /
page_numbers / highlight_status to emitted chunks so file:// citations
can deep-link into the PDF viewer — same payload shape as Docling.
"""

import os

os.environ.setdefault("OPENAI_API_KEY", "test")

import fitz
import pytest

from backend.core.config import settings
from backend.extractor import pdf_extractor
from backend.extractor.pdf_extractor import PDFExtractor


def _make_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((50, 60), "Mount Whitney", fontsize=18)
    page.insert_text(
        (50, 100),
        "Mount Whitney is the tallest mountain in the contiguous United States.",
        fontsize=11,
    )
    page.insert_text(
        (50, 120), "It rises to 4,421 metres in the Sierra Nevada.", fontsize=11
    )
    page.insert_text((50, 140), "It lies in California.", fontsize=11)
    return doc.tobytes()


def _extractor() -> PDFExtractor:
    return PDFExtractor(chunk_size=400, chunk_overlap=40)


def _assert_geometry(payloads):
    assert payloads
    assert [p for p in payloads if p.get("regions")], "no payload has regions"
    for p in payloads:
        assert p.get("highlight_status") in ("available", "unavailable")
        for r in p.get("regions") or []:
            assert r["page_number"] >= 1
            bbox = r["bbox_norm"]
            assert len(bbox) == 4
            assert all(0.0 <= v <= 1.0 for v in bbox)
            assert bbox[0] < bbox[2] and bbox[1] < bbox[3]


def test_pymupdf_path_emits_regions(monkeypatch):
    monkeypatch.setattr(settings, "pdf_use_pymupdf4llm", False)
    payloads = _extractor().parse_from_bytes(_make_pdf(), "file://test.pdf")
    _assert_geometry(payloads)
    assert any(p.get("page_numbers") == [1] for p in payloads)


@pytest.mark.skipif(
    not pdf_extractor.HAS_PYMUPDF4LLM, reason="pymupdf4llm not installed"
)
def test_pymupdf4llm_path_emits_regions(monkeypatch):
    monkeypatch.setattr(settings, "pdf_use_pymupdf4llm", True)
    payloads = _extractor().parse_from_bytes(_make_pdf(), "file://test.pdf")
    _assert_geometry(payloads)


def test_index_payload_keeps_citation_fields(monkeypatch):
    """index_chunks_with_retrieval must pass citation fields through to the
    Qdrant payload instead of dropping them (it allowlists fields)."""
    from backend.api import domain_indexing

    captured = {}

    class _Client:
        def get_collection(self, name):
            raise Exception("missing")

        def upsert(self, collection_name, points):
            captured["points"] = points

    class _Qdrant:
        client = _Client()

        def create_collection(self):
            captured["created"] = True

        def delete_by_url(self, url):
            captured["deleted_url"] = url

    monkeypatch.setattr(
        domain_indexing, "resolve_domain_config", lambda d: {"collection_name": "c"}
    )
    monkeypatch.setattr(domain_indexing, "build_domain_qdrant", lambda d: _Qdrant())
    monkeypatch.setattr(
        domain_indexing,
        "get_embedding_spec_for_domain",
        lambda d: {"batch_size": 8, "model": "m", "provider": "p", "runtime": "r"},
    )
    monkeypatch.setattr(
        domain_indexing,
        "generate_embeddings_with_retrieval",
        lambda texts, d: [[0.0] * 4 for _ in texts],
    )

    res = domain_indexing.index_chunks_with_retrieval(
        [
            {
                "text": "hello",
                "url": "file://a.pdf",
                "document_id": "sha256:" + "a" * 64,
                "artifact_uri": "internal://documents/sha256:" + "a" * 64,
                "regions": [{"page_number": 1, "bbox_norm": [0.1, 0.1, 0.5, 0.2]}],
                "page_numbers": [1],
                "highlight_status": "available",
            }
        ],
        active_domain="default",
    )
    assert res["vectors_indexed"] == 1
    pl = captured["points"][0].payload
    assert pl["document_id"] == "sha256:" + "a" * 64
    assert pl["regions"][0]["bbox_norm"] == [0.1, 0.1, 0.5, 0.2]
    assert pl["page_numbers"] == [1]
    assert pl["highlight_status"] == "available"
