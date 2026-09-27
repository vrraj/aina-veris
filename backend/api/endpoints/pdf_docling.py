"""API endpoint for the additive Docling PDF pipeline.

Route is transport-focused: it validates the request, enforces host
policy, and maps service errors to HTTP responses. Business logic lives in
backend/services/pdf_docling_service.py.
"""

from fastapi import APIRouter, HTTPException, Request

from backend.api.security import enforce_origin_host
from backend.core.config import settings
from backend.core.schemas import PDFDoclingInput
from backend.services.pdf_docling_service import (
    DoclingPipelineError,
    index_pdf_docling as run_docling_indexing,
)

router = APIRouter()


@router.post(
    "/index-pdf-docling",
    tags=["2. Ingest"],
    summary="1d. Index PDF via Docling pipeline (technical PDFs, datasheets)",
)
async def index_pdf_docling(pdf_input: PDFDoclingInput, request: Request):
    """Index a technical PDF through the Docling pipeline.

    Additive to POST /pdf: writes only to the dedicated
    `<collection>_docling_v1` Qdrant collection. No legacy fallback;
    extraction failures return a visible error.
    """
    if not bool(getattr(settings, "pdf_docling_enabled", True)):
        raise HTTPException(status_code=503, detail="Docling PDF pipeline is disabled")

    enforce_origin_host(request)

    try:
        return run_docling_indexing(pdf_input)
    except DoclingPipelineError as exc:
        message = str(exc)
        if "docling is not installed" in message.lower():
            raise HTTPException(status_code=503, detail=message)
        raise HTTPException(status_code=422, detail=message)
    except Exception as exc:  # unexpected — surface visibly, never fall back
        raise HTTPException(status_code=500, detail=f"Docling pipeline error: {exc}")
