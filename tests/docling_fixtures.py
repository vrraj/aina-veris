"""Shared fixtures for Docling pipeline tests.

Builds DoclingDocument instances programmatically (no model inference) so
tests run fast and offline.
"""

from docling_core.types.doc import (
    BoundingBox,
    CoordOrigin,
    DoclingDocument,
    ProvenanceItem,
)
from docling_core.types.doc.items.table.table_data import TableCell, TableData

from backend.extractor.docling_pdf_extractor import DoclingExtraction, _walk_document

PAGE_W, PAGE_H = 600.0, 800.0
PAGES = {0: {"width": PAGE_W, "height": PAGE_H}}


def _prov(page_no, l, b, r, t, origin=CoordOrigin.BOTTOMLEFT):
    return ProvenanceItem(
        page_no=page_no,
        bbox=BoundingBox(l=l, b=b, r=r, t=t, coord_origin=origin),
        charspan=(0, 0),
    )


def _cell(text, row, col, *, column_header=False, row_header=False, bbox=None):
    return TableCell(
        text=text,
        start_row_offset_idx=row,
        end_row_offset_idx=row + 1,
        start_col_offset_idx=col,
        end_col_offset_idx=col + 1,
        row_span=1,
        col_span=1,
        column_header=column_header,
        row_header=row_header,
        row_section=False,
        bbox=BoundingBox(l=bbox[0], b=bbox[1], r=bbox[2], t=bbox[3], coord_origin=CoordOrigin.BOTTOMLEFT) if bbox else None,
    )


def _spec_table_cells(with_bboxes=False):
    """2-column key/value spec table; optionally with per-cell bboxes."""
    cells = [
        _cell("Parameter", 0, 0, column_header=True),
        _cell("Value", 0, 1, column_header=True),
    ]
    rows = [
        ("Supply voltage", "32 V"),
        ("Input offset voltage", "7.0 mV"),
        ("Input bias current", "150 nA"),
        ("Gain-bandwidth product", "1.0 MHz"),
        ("Slew rate", "0.5 V/us"),
    ]
    for r, (param, value) in enumerate(rows, start=1):
        bbox_p = (50, 590 - r * 20, 300, 610 - r * 20) if with_bboxes else None
        bbox_v = (300, 590 - r * 20, 550, 610 - r * 20) if with_bboxes else None
        cells.append(_cell(param, r, 0, bbox=bbox_p))
        cells.append(_cell(value, r, 1, bbox=bbox_v))
    return cells


def build_datasheet_doc(with_bboxes=False, big_table=False) -> DoclingDocument:
    """Programmatic datasheet: title, headings, prose, spec table, footer."""
    doc = DoclingDocument(name="test")
    doc.add_page(page_no=1, size={"width": PAGE_W, "height": PAGE_H})
    doc.add_title(text="LM358 Datasheet", prov=_prov(1, 50, 740, 400, 780))
    doc.add_heading(
        text="Electrical Characteristics",
        level=1,
        prov=_prov(1, 50, 680, 350, 720),
    )
    doc.add_text(
        label="paragraph",
        text="The LM358 operates from a single supply over 3V to 32V.",
        prov=_prov(1, 50, 620, 500, 660),
    )
    n_rows = 10 if big_table else 5
    cells = _spec_table_cells(with_bboxes=with_bboxes)
    if big_table:
        for r in range(6, 11):
            cells.append(_cell(f"Parameter {r}", r, 0))
            cells.append(_cell(f"{r * 10} V", r, 1))
    doc.add_table(
        data=TableData(num_rows=1 + n_rows, num_cols=2, table_cells=cells),
        prov=_prov(1, 50, 400, 550, 600),
    )
    doc.add_heading(
        text="Typical Application",
        level=2,
        prov=_prov(1, 50, 360, 300, 390),
    )
    doc.add_text(
        label="paragraph",
        text="Suitable for battery-operated devices.",
        prov=_prov(1, 50, 300, 400, 340),
    )
    doc.add_text(
        label="page_footer",
        text="LM358 Datasheet Rev 1.0",
        prov=_prov(1, 50, 40, 300, 70),
    )
    return doc


def build_extraction(doc=None, source="https://example.com/lm358.pdf", **kwargs):
    """Walk a programmatic doc and wrap it in a DoclingExtraction."""
    doc = doc or build_datasheet_doc(**kwargs)
    items, warnings, title = _walk_document(doc, PAGES)
    return DoclingExtraction(
        document_id="sha256:" + "a" * 64,
        source=source,
        title=title or "LM358 Datasheet",
        page_count=1,
        items=items,
        warnings=warnings,
        conversion_status="success",
        pages=dict(PAGES),
    )
