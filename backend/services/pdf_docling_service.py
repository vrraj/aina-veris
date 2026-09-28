"""Service orchestration for the Docling PDF pipeline (/index-pdf-docling).

Coordinates extraction, artifact persistence, chunk planning, duplicate
checking and Qdrant indexing for the additive Docling path. The legacy
/pdf flow is untouched. Docling failures are surfaced as visible errors
(`DoclingPipelineError`); there is no fallback to the legacy parser.
"""

from __future__ import annotations

import base64
import logging
from typing import Any, Dict, Optional

import httpx

from backend.api.domain_indexing import get_embedding_rate_per_mm_tokens
from backend.core.config import settings
from backend.extractor.docling_chunks import build_chunks
from backend.extractor.docling_pdf_extractor import (
    DoclingConversionError,
    DoclingUnavailableError,
    compute_document_id,
    extract_pdf_document,
    save_artifact,
    save_source_pdf,
)
from backend.services.pdf_docling_indexing import (
    count_docling_points_for_document,
    index_docling_chunks,
)

logger = logging.getLogger(__name__)

_FETCH_TIMEOUT = 60


class DoclingPipelineError(RuntimeError):
    """Visible pipeline failure (no silent legacy fallback)."""


def _fetch_pdf(url: str) -> bytes:
    try:
        with httpx.Client(timeout=_FETCH_TIMEOUT, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
            return response.content
    except Exception as exc:
        raise DoclingPipelineError(f"Failed to download PDF from {url}: {exc}") from exc


def _resolve_source(pdf_input) -> Dict[str, Any]:
    """Resolve bytes + canonical source per spec: file overrides url bytes,
    url stays the canonical source key when provided."""
    file_data = None
    if pdf_input.file:
        try:
            file_data = base64.b64decode(pdf_input.file)
        except Exception as exc:
            raise DoclingPipelineError(f"Invalid base64 file data: {exc}") from exc

    if not pdf_input.url and file_data is None:
        raise DoclingPipelineError("Provide either a PDF file or a URL")

    if pdf_input.url:
        source = pdf_input.url
        pdf_bytes = file_data if file_data is not None else _fetch_pdf(pdf_input.url)
    else:
        digest = compute_document_id(file_data).split(":", 1)[-1]
        if pdf_input.filename:
            source = f"file://{pdf_input.filename}"
        else:
            source = f"uploaded://{digest}"
        pdf_bytes = file_data

    return {"pdf_bytes": pdf_bytes, "source": source}


def index_pdf_docling(pdf_input) -> Dict[str, Any]:
    """Run the Docling pipeline: extract -> artifact -> chunks -> index.

    Returns a JSON-serializable result dict. `estimate` performs extraction
    and chunk planning only — no Qdrant writes, no artifact writes.
    """
    if not bool(getattr(settings, "pdf_docling_enabled", True)):
        raise DoclingPipelineError("Docling PDF pipeline is disabled (pdf_docling_enabled=false)")

    resolved = _resolve_source(pdf_input)
    pdf_bytes, source = resolved["pdf_bytes"], resolved["source"]

    try:
        extraction = extract_pdf_document(pdf_bytes, source)
    except DoclingUnavailableError as exc:
        raise DoclingPipelineError(str(exc)) from exc
    except DoclingConversionError as exc:
        raise DoclingPipelineError(str(exc)) from exc

    # Duplicate check scoped to this pipeline's points only (never legacy).
    if bool(getattr(settings, "check_document_indexed", True)) and not pdf_input.estimate:
        try:
            existing = count_docling_points_for_document(
                pdf_input.active_domain, extraction.document_id
            )
        except Exception as exc:
            logger.warning("Docling duplicate check failed for %s: %s", source, exc)
            existing = 0
        if existing > 0 and not pdf_input.force_delete:
            return {
                "message": "Document already indexed in the Docling pipeline",
                "pipeline": "pdf_docling_v1",
                "source": source,
                "document_id": extraction.document_id,
                "already_indexed": True,
                "vectors_found": int(existing),
                "confirmation_required": True,
                "hint": "Resubmit with force_delete=true to re-index this document",
            }

    plan = build_chunks(
        extraction,
        max_chunks=pdf_input.max_chunks or None,
        skip_sections=pdf_input.skip_sections,
    )

    provenance_coverage = (
        extraction.items_with_regions / len(extraction.items) if extraction.items else 0.0
    )

    if pdf_input.estimate:
        tokens_used = sum(c.token_count for c in plan.chunks)
        return {
            "message": "Estimate only",
            "pipeline": "pdf_docling_v1",
            "source": source,
            "document_id": extraction.document_id,
            "chunks_planned": len(plan.chunks),
            "chunks_omitted_by_max_chunks": plan.omitted_chunks,
            "tokens_used": tokens_used,
            "parsing_warnings": extraction.warnings,
            "provenance_coverage": round(provenance_coverage, 4),
            "page_count": extraction.page_count,
            "title": extraction.title,
        }

    artifact_path, artifact_uri = save_artifact(
        extraction, getattr(settings, "pdf_docling_artifact_dir", None)
    )
    logger.info("Docling artifact saved: %s (%s)", artifact_uri, artifact_path)

    try:
        pdf_path = save_source_pdf(
            pdf_bytes,
            extraction.document_id,
            getattr(settings, "pdf_docling_artifact_dir", None),
        )
        logger.info("Docling source PDF saved: %s", pdf_path)
    except Exception as exc:
        # Source persistence is best-effort: citations still render with
        # page metadata; file:// links just won't be navigable.
        logger.warning("Could not persist source PDF for %s: %s", source, exc)

    result = index_docling_chunks(
        plan.chunks,
        active_domain=pdf_input.active_domain,
        source_key=source,
        source=source,
        artifact_uri=artifact_uri,
        max_chunks=pdf_input.max_chunks or None,
    )

    rate_per_mm = get_embedding_rate_per_mm_tokens()
    embedding_cost = (result["tokens_used"] * rate_per_mm) / 1_000_000.0

    return {
        "message": "PDF indexed via Docling pipeline",
        "pipeline": "pdf_docling_v1",
        "pipeline_version": "1.0",
        "source": source,
        "document_id": extraction.document_id,
        "title": extraction.title,
        "page_count": extraction.page_count,
        "collection": result["collection_name"],
        "chunks_indexed": result["vectors_indexed"],
        "chunks_omitted_by_max_chunks": plan.omitted_chunks,
        "stale_points_deleted": result["stale_points_deleted"],
        "tokens_used": result["tokens_used"],
        "embedding_cost": round(embedding_cost, 8),
        "parsing_warnings": extraction.warnings,
        "provenance_coverage": round(provenance_coverage, 4),
        "artifact_uri": artifact_uri,
    }
