"""API endpoint for the additive Docling PDF pipeline.

Route is transport-focused: it validates the request, enforces host
policy, and maps service errors to HTTP responses. Business logic lives in
backend/services/pdf_docling_service.py.
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
import json
import queue
import threading
import time

from backend.api.security import enforce_origin_host
from backend.core.config import settings
from backend.core.schemas import PDFDoclingInput
from backend.extractor.docling_pdf_extractor import source_pdf_path
from backend.services.pdf_docling_indexing import DoclingCancelled
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


@router.post(
    "/index-pdf-docling/stream",
    tags=["2. Ingest"],
    summary="1e. Index PDF via Docling with live stage progress (SSE)",
)
async def index_pdf_docling_stream(pdf_input: PDFDoclingInput, request: Request):
    """Streaming variant of /index-pdf-docling. Emits `event: stage` SSE
    frames as the pipeline progresses (e.g. "Loading model: ..." on a cold
    converter build, extraction, indexing), then a final `event: result`
    carrying the same JSON body as the plain endpoint, or `event: error`.
    During long silent steps (Docling convert on CPU can take minutes) an
    `event: stage` heartbeat re-emits the last stage with elapsed seconds so
    the client can show the request is still alive. If the client disconnects,
    a cooperative cancel event is set and the worker raises at the next stage
    boundary (emitted as `event: cancelled`); Docling conversion itself cannot
    be interrupted mid-call, so a disconnect during convert takes effect once
    it returns. The plain endpoint is unchanged for non-streaming clients.
    """
    if not bool(getattr(settings, "pdf_docling_enabled", True)):
        raise HTTPException(status_code=503, detail="Docling PDF pipeline is disabled")

    enforce_origin_host(request)

    HEARTBEAT_SECONDS = 15

    def event_stream():
        events: "queue.Queue" = queue.Queue()
        cancel = threading.Event()
        started = time.monotonic()
        events.put(("stage", {
            "message": "Docling extraction - layout, table structure, and "
                       "figure stages can take a few minutes on CPU.",
        }))

        def progress(message: str) -> None:
            events.put(("stage", {"message": message}))

        def run() -> None:
            try:
                events.put(("result", run_docling_indexing(
                    pdf_input, progress, cancel_event=cancel
                )))
            except DoclingCancelled:
                events.put(("cancelled", {"detail": "Indexing cancelled"}))
            except DoclingPipelineError as exc:
                events.put(("error", {"detail": str(exc)}))
            except Exception as exc:
                events.put(("error", {"detail": f"Docling pipeline error: {exc}"}))
            finally:
                events.put(None)  # sentinel

        threading.Thread(target=run, daemon=True).start()
        last_stage = ""
        try:
            while True:
                try:
                    item = events.get(timeout=HEARTBEAT_SECONDS)
                except queue.Empty:
                    elapsed = int(time.monotonic() - started)
                    detail = last_stage or "Still processing"
                    payload = {"message": f"{detail} ({elapsed}s elapsed)"}
                    yield f"event: stage\ndata: {json.dumps(payload)}\n\n"
                    continue
                if item is None:
                    break
                event, payload = item
                if event == "stage":
                    last_stage = payload.get("message", last_stage)
                yield f"event: {event}\ndata: {json.dumps(payload)}\n\n"
        finally:
            # On client disconnect this generator is closed, which lands here
            # and stops the worker at its next stage checkpoint. On normal
            # completion the worker already finished, so this is a no-op.
            cancel.set()

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get(
    "/docling-document/{document_id}",
    tags=["3. Search & Chat"],
    summary="Serve the stored source PDF for a docling-indexed document",
)
async def get_docling_document(document_id: str, request: Request):
    """Return the persisted source PDF so file:// (uploaded) citations can
    deep-link to the document. Browsers open it in the built-in PDF viewer;
    the frontend appends #page=N to land on the cited page."""
    enforce_origin_host(request)
    path = source_pdf_path(
        document_id, getattr(settings, "pdf_docling_artifact_dir", None)
    )
    if not path:
        raise HTTPException(status_code=404, detail="Source PDF not found for this document")
    return FileResponse(path, media_type="application/pdf")
