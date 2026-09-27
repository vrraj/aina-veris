"""Docling-based PDF extraction for the /index-pdf-docling pipeline.

Produces a structured extraction artifact (typed items with provenance:
item refs, page numbers, normalized bounding boxes, table cell structure)
that the structure-aware chunker consumes. The artifact is serialized to
disk outside Qdrant so citation geometry stays tied to the exact bytes.

This pipeline is additive: the legacy PDFExtractor and POST /pdf path are
untouched. There is deliberately no fallback to the legacy parser; Docling
failures surface as visible errors.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Soft dependency guard: environments without docling get a clear error
# from the service layer instead of an import crash at app startup.
try:  # pragma: no cover - exercised implicitly by the guard below
    from docling.datamodel.base_models import ConversionStatus
    from docling.datamodel.document import DocumentStream
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling_core.types.doc import (
        BoundingBox,
        CoordOrigin,
        DoclingDocument,
        ProvenanceItem,
        TableItem,
        TextItem,
    )
    HAS_DOCLING = True
    _DOCLING_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover
    HAS_DOCLING = False
    _DOCLING_IMPORT_ERROR = exc

try:
    import docling as _docling_pkg

    DOCLING_VERSION = getattr(_docling_pkg, "__version__", "unknown")
except Exception:  # pragma: no cover
    DOCLING_VERSION = "unknown"

PDF_DOCLING_PIPELINE = "pdf_docling_v1"
PDF_DOCLING_PIPELINE_VERSION = "1.0"
ARTIFACT_SCHEMA_VERSION = "1"

DEFAULT_ARTIFACT_DIR = "docling_artifacts"

_EPS = 1e-9
# Sanity limit: Docling bboxes are page-relative after normalization; a box
# larger than this fraction of the page is treated as a parsing artifact.
_MAX_BOX_FRACTION = 1.0 + _EPS


class DoclingUnavailableError(RuntimeError):
    """Raised when the docling package is not installed in this environment."""


class DoclingConversionError(RuntimeError):
    """Raised when Docling fails to convert the PDF (no legacy fallback)."""


@dataclass
class SourceRegion:
    """A page region for one extracted item, normalized for viewer overlay."""

    page_index: int  # zero-based
    page_number: int  # one-based, PDF display number
    bbox_norm: List[float]  # [x0, y0, x1, y1] top-left origin, fractions of page
    item_ref: str
    raw_bbox: Optional[Dict[str, float]] = None  # original l/b/r/t for debugging
    coord_origin: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "page_index": self.page_index,
            "page_number": self.page_number,
            "bbox_norm": [round(v, 6) for v in self.bbox_norm],
            "item_ref": self.item_ref,
            "raw_bbox": self.raw_bbox,
            "coord_origin": self.coord_origin,
        }


@dataclass
class TableCellInfo:
    text: str
    start_row: int
    start_col: int
    row_span: int
    col_span: int
    column_header: bool
    row_header: bool
    row_section: bool
    # Raw cell bbox [l, b, r, t] in page units (BOTTOMLEFT origin) when the
    # parser provides one; used for row-level highlight regions.
    bbox: Optional[List[float]] = None


@dataclass
class TableBlock:
    num_rows: int
    num_cols: int
    col_headers: List[str]
    # rows[r] is a list of cell texts laid out on the grid (spans repeated)
    rows: List[List[str]]
    # structured cells with span/header flags, kept for precise citation
    cells: List[TableCellInfo] = field(default_factory=list)
    markdown: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "num_rows": self.num_rows,
            "num_cols": self.num_cols,
            "col_headers": self.col_headers,
            "rows": self.rows,
            "cells": [c.__dict__ for c in self.cells],
            "markdown": self.markdown,
        }


@dataclass
class ExtractedItem:
    item_ref: str
    kind: str
    verbatim_text: str
    section_path: List[str]
    heading_level: Optional[int] = None
    page_index: Optional[int] = None  # zero-based
    page_number: Optional[int] = None  # one-based display number
    regions: List[SourceRegion] = field(default_factory=list)
    highlight_status: str = "unavailable"
    table: Optional[TableBlock] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "item_ref": self.item_ref,
            "kind": self.kind,
            "verbatim_text": self.verbatim_text,
            "section_path": self.section_path,
            "heading_level": self.heading_level,
            "page_index": self.page_index,
            "page_number": self.page_number,
            "regions": [r.to_dict() for r in self.regions],
            "highlight_status": self.highlight_status,
            "table": self.table.to_dict() if self.table else None,
        }


@dataclass
class DoclingExtraction:
    document_id: str  # "sha256:<hex>" of the PDF bytes
    source: str
    title: str
    page_count: int
    items: List[ExtractedItem]
    warnings: List[str]
    conversion_status: str
    # page_index (zero-based) -> {"width": float, "height": float}
    pages: Dict[int, Dict[str, float]] = field(default_factory=dict)
    artifact_path: Optional[str] = None
    artifact_uri: Optional[str] = None

    @property
    def items_with_regions(self) -> int:
        return sum(1 for i in self.items if i.highlight_status == "available")

    def to_artifact_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "pipeline": PDF_DOCLING_PIPELINE,
            "pipeline_version": PDF_DOCLING_PIPELINE_VERSION,
            "docling_version": DOCLING_VERSION,
            "document_id": self.document_id,
            "source": self.source,
            "title": self.title,
            "page_count": self.page_count,
            "pages": {str(k): v for k, v in self.pages.items()},
            "items": [i.to_dict() for i in self.items],
            "warnings": self.warnings,
            "conversion_status": self.conversion_status,
        }


# ---------------------------------------------------------------------------
# Converter construction
# ---------------------------------------------------------------------------

_CONVERTER_CACHE: Dict[Tuple[bool, str], "DocumentConverter"] = {}


def _build_converter(do_ocr: bool, table_mode: str) -> "DocumentConverter":
    options = PdfPipelineOptions()
    options.do_ocr = bool(do_ocr)
    try:
        options.table_structure_options.mode = TableFormerMode(table_mode)
    except ValueError:
        options.table_structure_options.mode = TableFormerMode.ACCURATE
    return DocumentConverter(
        format_options={"pdf": PdfFormatOption(pipeline_options=options)}
    )


def _get_converter(do_ocr: bool, table_mode: str) -> "DocumentConverter":
    key = (bool(do_ocr), str(table_mode))
    if key not in _CONVERTER_CACHE:
        _CONVERTER_CACHE[key] = _build_converter(*key)
    return _CONVERTER_CACHE[key]


# ---------------------------------------------------------------------------
# Box normalization
# ---------------------------------------------------------------------------

def _normalize_bbox(
    bbox: "BoundingBox",
    page_width: float,
    page_height: float,
    item_ref: str,
) -> Optional[List[float]]:
    """Normalize a Docling bbox to [x0, y0, x1, y1] top-left fractions.

    Docling reports boxes in page pixel/point units. BOTTOMLEFT origin means
    y grows upward; TOPLEFT means y grows downward. Invalid (inverted,
    out-of-page, degenerate) boxes are rejected -> None.
    """
    if page_width <= 0 or page_height <= 0:
        return None
    try:
        l, b, r, t = float(bbox.l), float(bbox.b), float(bbox.r), float(bbox.t)
    except (TypeError, ValueError):
        return None

    origin = getattr(bbox, "coord_origin", None)
    if origin == CoordOrigin.TOPLEFT:
        y0, y1 = b / page_height, t / page_height
    else:  # BOTTOMLEFT (Docling default) or unknown -> assume BOTTOMLEFT
        y0, y1 = 1.0 - t / page_height, 1.0 - b / page_height

    x0, x1 = l / page_width, r / page_width
    box = [x0, y0, x1, y1]
    if any(v != v for v in box):  # NaN check
        return None
    if (
        x0 < -_EPS
        or x1 > _MAX_BOX_FRACTION
        or y0 < -_EPS
        or y1 > _MAX_BOX_FRACTION
        or x1 - x0 <= _EPS
        or y1 - y0 <= _EPS
    ):
        logger.debug("Rejected invalid bbox for %s: %s", item_ref, box)
        return None
    return [min(max(v, 0.0), 1.0) for v in box]


def _flatten_prov(prov: Any) -> List["ProvenanceItem"]:
    """Flatten prov lists; guards against nested lists from odd builders."""
    flat: List[Any] = []

    def _walk(node: Any) -> None:
        if isinstance(node, (list, tuple)):
            for child in node:
                _walk(child)
        else:
            flat.append(node)

    _walk(prov)
    return flat


def _regions_for_item(
    prov: List["ProvenanceItem"],
    pages: Dict[int, Dict[str, float]],
    item_ref: str,
) -> List[SourceRegion]:
    regions: List[SourceRegion] = []
    for p in _flatten_prov(prov):
        page_no = getattr(p, "page_no", None)
        if page_no is None:
            continue
        page_index = int(page_no) - 1
        size = pages.get(page_index) or {}
        w, h = float(size.get("width", 0.0)), float(size.get("height", 0.0))
        bbox = getattr(p, "bbox", None)
        if bbox is None:
            continue
        bbox_norm = _normalize_bbox(bbox, w, h, item_ref)
        raw = None
        try:
            raw = {"l": float(bbox.l), "b": float(bbox.b), "r": float(bbox.r), "t": float(bbox.t)}
        except (TypeError, ValueError):
            raw = None
        if bbox_norm is None:
            continue
        regions.append(
            SourceRegion(
                page_index=page_index,
                page_number=int(page_no),
                bbox_norm=bbox_norm,
                item_ref=item_ref,
                raw_bbox=raw,
                coord_origin=str(getattr(bbox, "coord_origin", "") or ""),
            )
        )
    return regions


# ---------------------------------------------------------------------------
# Table structure extraction
# ---------------------------------------------------------------------------

def _table_block_from_item(item: "TableItem", doc: "DoclingDocument") -> TableBlock:
    data = getattr(item, "data", None)
    cells: List[TableCellInfo] = []
    num_rows = int(getattr(data, "num_rows", 0) or 0)
    num_cols = int(getattr(data, "num_cols", 0) or 0)

    for c in getattr(data, "table_cells", []) or []:
        cell_bbox = None
        raw_bbox = getattr(c, "bbox", None)
        if raw_bbox is not None:
            try:
                cell_bbox = [float(raw_bbox.l), float(raw_bbox.b), float(raw_bbox.r), float(raw_bbox.t)]
            except (TypeError, ValueError):
                cell_bbox = None
        cells.append(
            TableCellInfo(
                text=str(getattr(c, "text", "") or ""),
                start_row=int(getattr(c, "start_row_offset_idx", 0) or 0),
                start_col=int(getattr(c, "start_col_offset_idx", 0) or 0),
                row_span=int(getattr(c, "row_span", 1) or 1),
                col_span=int(getattr(c, "col_span", 1) or 1),
                column_header=bool(getattr(c, "column_header", False)),
                row_header=bool(getattr(c, "row_header", False)),
                row_section=bool(getattr(c, "row_section", False)),
                bbox=cell_bbox,
            )
        )

    # Grid layout: spans are expanded by repeating the cell text, which keeps
    # positional lookup exact for the row-level key/value representation.
    grid: List[List[str]] = [["" for _ in range(max(num_cols, 1))] for _ in range(max(num_rows, 1))]
    for c in cells:
        if c.start_row < num_rows and c.start_col < num_cols:
            for rr in range(c.start_row, min(c.start_row + c.row_span, num_rows)):
                for cc in range(c.start_col, min(c.start_col + c.col_span, num_cols)):
                    grid[rr][cc] = c.text

    col_headers: List[str] = []
    if grid:
        for c in cells:
            if c.column_header and c.start_row == 0:
                col_headers.append(c.text)

    try:
        markdown = item.export_to_markdown(doc=doc)
    except Exception:
        try:
            markdown = item.export_to_markdown()
        except Exception:
            markdown = ""

    return TableBlock(
        num_rows=num_rows,
        num_cols=num_cols,
        col_headers=col_headers,
        rows=[row[:] for row in grid],
        cells=cells,
        markdown=markdown or "",
    )


# ---------------------------------------------------------------------------
# Document walking
# ---------------------------------------------------------------------------

_KIND_BY_LABEL = {
    "paragraph": "paragraph",
    "section_header": "section_header",
    "title": "title",
    "list_item": "list_item",
    "caption": "caption",
    "footnote": "footnote",
    "text": "text",
    "reference": "reference",
    "page_header": "page_header",
    "page_footer": "page_footer",
    "checkbox_selected": "text",
    "checkbox_unselected": "text",
}


def _label_to_kind(label: Any) -> str:
    try:
        raw = str(label.value if hasattr(label, "value") else label)
    except Exception:
        raw = "text"
    return _KIND_BY_LABEL.get(raw, "text")


def _clean_text(value: str) -> str:
    # Preserve symbols/units verbatim; only trim stray control characters
    # that break JSON round-tripping and search text.
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", value or "").strip()


def _walk_document(doc: "DoclingDocument", pages: Dict[int, Dict[str, float]]) -> Tuple[List[ExtractedItem], List[str], str]:
    items: List[ExtractedItem] = []
    warnings: List[str] = []
    title = ""
    section_stack: List[Tuple[int, str]] = []  # (heading level, heading text)

    for item, _level in doc.iterate_items():
        item_ref = getattr(item, "self_ref", None)
        if not item_ref:
            continue

        if isinstance(item, TextItem) or hasattr(item, "label") and hasattr(item, "text"):
            kind = _label_to_kind(item.label)
            text = _clean_text(getattr(item, "text", "") or "")
            if kind == "title" and not title and text:
                title = text
            if kind == "section_header":
                heading_level = int(getattr(item, "level", 1) or 1)
                while section_stack and section_stack[-1][0] >= heading_level:
                    section_stack.pop()
                section_stack.append((heading_level, text))
            else:
                heading_level = None
            if not text:
                continue
            ext = ExtractedItem(
                item_ref=str(item_ref),
                kind=kind,
                verbatim_text=text,
                section_path=[s for _, s in section_stack],
                heading_level=heading_level,
            )
        elif isinstance(item, TableItem):
            block = _table_block_from_item(item, doc)
            ext = ExtractedItem(
                item_ref=str(item_ref),
                kind="table",
                verbatim_text=_clean_text(block.markdown or ""),
                section_path=[s for _, s in section_stack],
                table=block,
            )
        elif hasattr(item, "label") and str(getattr(item.label, "value", item.label)) == "picture":
            ext = ExtractedItem(
                item_ref=str(item_ref),
                kind="picture",
                verbatim_text="",
                section_path=[s for _, s in section_stack],
            )
        else:
            # Groups, lists and other node items: their children are visited
            # separately by iterate_items, so skip the container itself.
            continue

        regions = _regions_for_item(getattr(item, "prov", None) or [], pages, ext.item_ref)
        ext.regions = regions
        if regions:
            ext.page_index = regions[0].page_index
            ext.page_number = regions[0].page_number
            ext.highlight_status = "available"
        else:
            if ext.kind in ("table", "section_header"):
                warnings.append(
                    f"No usable provenance region for {ext.kind} item {ext.item_ref}; "
                    "highlight unavailable"
                )
            ext.highlight_status = "unavailable"

        items.append(ext)

    return items, warnings, title


# ---------------------------------------------------------------------------
# Public extraction API
# ---------------------------------------------------------------------------

def compute_document_id(pdf_bytes: bytes) -> str:
    return "sha256:" + hashlib.sha256(pdf_bytes).hexdigest()


def extract_pdf_document(
    pdf_bytes: bytes,
    source: str,
    *,
    do_ocr: Optional[bool] = None,
    table_mode: Optional[str] = None,
) -> DoclingExtraction:
    """Convert PDF bytes with Docling and build the typed extraction artifact.

    Raises DoclingUnavailableError if docling is not installed and
    DoclingConversionError when conversion fails (no legacy fallback).
    """
    if not HAS_DOCLING:
        raise DoclingUnavailableError(
            "docling is not installed; the /index-pdf-docling pipeline requires it"
        )
    if not pdf_bytes:
        raise DoclingConversionError("Empty PDF payload")

    from backend.core.config import settings as app_settings

    if do_ocr is None:
        do_ocr = bool(getattr(app_settings, "pdf_docling_do_ocr", False))
    if table_mode is None:
        table_mode = str(getattr(app_settings, "pdf_docling_table_mode", "accurate"))

    converter = _get_converter(do_ocr, table_mode)
    document_id = compute_document_id(pdf_bytes)
    filename = "document.pdf"
    try:
        parsed = urlsplit_filename(source)
        if parsed:
            filename = parsed
    except Exception:
        pass

    try:
        result = converter.convert(
            DocumentStream(name=filename, stream=io.BytesIO(pdf_bytes))
        )
    except Exception as exc:
        raise DoclingConversionError(f"Docling conversion failed: {exc}") from exc

    status = getattr(result, "status", None)
    status_str = str(getattr(status, "value", status) or "unknown")
    if status == ConversionStatus.FAILURE:
        raise DoclingConversionError(
            f"Docling conversion failed: {result.errors or 'unknown error'}"
        )

    warnings: List[str] = []
    for err in getattr(result, "errors", []) or []:
        msg = getattr(err, "error_message", None) or str(err)
        page = getattr(err, "page_no", None)
        warnings.append(f"docling: {msg}" + (f" (page {page})" if page else ""))
    if status == ConversionStatus.PARTIAL_SUCCESS:
        warnings.append("docling: conversion completed with partial success")

    doc: "DoclingDocument" = result.document

    pages: Dict[int, Dict[str, float]] = {}
    for page_no, page in (getattr(doc, "pages", None) or {}).items():
        size = getattr(page, "size", None)
        if size is None:
            continue
        try:
            pages[int(page_no) - 1] = {
                "width": float(size.width),
                "height": float(size.height),
            }
        except (TypeError, ValueError):
            continue

    items, walk_warnings, title = _walk_document(doc, pages)
    warnings.extend(walk_warnings)

    if not title:
        title = filename.rsplit(".", 1)[0] if "." in filename else filename

    extraction = DoclingExtraction(
        document_id=document_id,
        source=source,
        title=title,
        page_count=len(pages) or max((i.page_number or 0) for i in items) or 0,
        items=items,
        warnings=warnings,
        conversion_status=status_str,
        pages=pages,
    )
    return extraction


def urlsplit_filename(source: str) -> str:
    """Best-effort filename from a URL-ish source string."""
    from urllib.parse import urlsplit

    path = urlsplit(source or "").path if "://" in (source or "") else (source or "")
    name = (path or "").rsplit("/", 1)[-1]
    return name if name else ""


# ---------------------------------------------------------------------------
# Artifact persistence
# ---------------------------------------------------------------------------

def _artifact_dir(artifact_dir: Optional[str]) -> Path:
    base = artifact_dir or DEFAULT_ARTIFACT_DIR
    return Path(base)


def save_artifact(
    extraction: DoclingExtraction,
    artifact_dir: Optional[str] = None,
) -> Tuple[str, str]:
    """Serialize the artifact next to the durable store.

    Returns (artifact_path, artifact_uri). The URI is stable for the document
    ID regardless of storage location. Write is atomic (temp + rename) so a
    failed write cannot leave a truncated artifact.
    """
    out_dir = _artifact_dir(artifact_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = extraction.to_artifact_dict()
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    checksum = hashlib.sha256(serialized).hexdigest()
    payload["artifact_checksum"] = checksum

    digest = extraction.document_id.split(":", 1)[-1]
    path = out_dir / f"{digest}.json"
    final_serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")

    fd, tmp_name = tempfile.mkstemp(dir=str(out_dir), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(final_serialized)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise

    uri = f"internal://documents/{extraction.document_id}"
    extraction.artifact_path = str(path)
    extraction.artifact_uri = uri
    return str(path), uri


def load_artifact(path: str) -> Dict[str, Any]:
    """Load an artifact JSON from disk and verify its checksum."""
    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    checksum = payload.pop("artifact_checksum", None)
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    actual = hashlib.sha256(serialized).hexdigest()
    if checksum is not None and checksum != actual:
        raise ValueError(f"Artifact checksum mismatch for {path}")
    return payload
