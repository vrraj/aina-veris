"""Routing tests for POST /batch/process_docs.

Covers the batch-level/per-item `pipeline` field for pdf items and the
(input, request) handler dispatch used by process_item.
"""

import types

import pytest
from pydantic import ValidationError

import backend.main as main
from backend.api.endpoints import pdf_docling as pdf_docling_endpoint


def _batch(**kwargs):
    kwargs.setdefault("items", [])
    kwargs.setdefault("estimate", False)
    return main.BatchRequest(**kwargs)


def _pdf_item(url="https://example.com/doc.pdf", **kwargs):
    kwargs.setdefault("doc_type", "pdf")
    return main.BatchURLItem(url=url, **kwargs)


def _spy(name):
    async def handler(input_data, request):
        return {"handler": name, "input": input_data, "request": request}

    return handler


def test_pipeline_defaults_and_validation():
    assert _batch().pipeline == "pymupdf"
    assert _pdf_item().pipeline is None
    with pytest.raises(ValidationError):
        _batch(pipeline="ocr")
    with pytest.raises(ValidationError):
        _pdf_item(pipeline="ocr")


async def test_pdf_item_routes_to_docling_via_batch_default(monkeypatch):
    spy = _spy("docling")
    monkeypatch.setattr(pdf_docling_endpoint, "index_pdf_docling", spy)
    monkeypatch.setattr(
        main, "index_pdf", _spy("pymupdf")
    )

    batch = _batch(pipeline="docling")
    item = _pdf_item()
    result = await main.process_item(item, batch, types.SimpleNamespace(), main.settings)

    assert result["status"] == "success"
    res = result["result"]
    assert res["handler"] == "docling"
    assert isinstance(res["input"], main.PDFDoclingInput)
    assert res["input"].estimate == batch.estimate
    # HTTP request object is forwarded for enforce_origin_host
    assert res["request"] is not None


async def test_pdf_item_per_item_override_beats_batch_default(monkeypatch):
    docling_spy = _spy("docling")
    monkeypatch.setattr(pdf_docling_endpoint, "index_pdf_docling", docling_spy)
    monkeypatch.setattr(main, "index_pdf", _spy("pymupdf"))

    batch = _batch(pipeline="docling")
    item = _pdf_item(pipeline="pymupdf")
    result = await main.process_item(item, batch, types.SimpleNamespace(), main.settings)

    assert result["result"]["handler"] == "pymupdf"
    assert isinstance(result["result"]["input"], main.PDFInput)


async def test_pdf_item_defaults_to_pymupdf(monkeypatch):
    monkeypatch.setattr(
        pdf_docling_endpoint, "index_pdf_docling", _spy("docling")
    )
    monkeypatch.setattr(main, "index_pdf", _spy("pymupdf"))

    result = await main.process_item(
        _pdf_item(), _batch(), types.SimpleNamespace(), main.settings
    )
    assert result["result"]["handler"] == "pymupdf"


async def test_handlers_receive_http_request(monkeypatch):
    """process_item must pass the starlette Request to handlers so
    enforce_origin_host inside them works (regression: batch dispatch used
    to call handlers with a single arg / BatchRequest fields)."""
    sentinel = object()
    seen = {}

    async def spy(input_data, request):
        seen["request"] = request
        return {}

    monkeypatch.setattr(main, "index_pdf", spy)
    result = await main.process_item(
        _pdf_item(), _batch(), sentinel, main.settings
    )
    assert result["status"] == "success"
    assert seen["request"] is sentinel
