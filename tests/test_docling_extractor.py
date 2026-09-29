"""Tests for the Docling PDF extraction artifact (spec stage 1).

Unit tests build a DoclingDocument programmatically (no model inference)
so they run fast and offline. The end-to-end conversion test is opt-in via
RUN_DOCLING_E2E=1 because it downloads Docling model artifacts on first run.
"""

import json
import os

os.environ.setdefault("OPENAI_API_KEY", "test")

import pytest

from backend.extractor.docling_pdf_extractor import (
    DEFAULT_ARTIFACT_DIR,
    DoclingExtraction,
    DoclingUnavailableError,
    ExtractedItem,
    PDF_DOCLING_PIPELINE,
    SourceRegion,
    TableBlock,
    _normalize_bbox,
    _regions_for_item,
    _walk_document,
    compute_document_id,
    load_artifact,
    save_artifact,
)

from docling_core.types.doc import (
    BoundingBox,
    CoordOrigin,
)
from docling_core.types.doc.items.table.table_data import TableCell, TableData

from docling_fixtures import (
    PAGE_H,
    PAGE_W,
    PAGES,
    _prov,
    build_datasheet_doc,
)


# ---------------------------------------------------------------------------
# Box normalization
# ---------------------------------------------------------------------------

class TestNormalizeBbox:
    def test_bottomleft_origin_converts_to_topleft_fractions(self):
        bbox = BoundingBox(l=50, b=700, r=300, t=750, coord_origin=CoordOrigin.BOTTOMLEFT)
        norm = _normalize_bbox(bbox, PAGE_W, PAGE_H, "/texts/0")
        assert norm is not None
        x0, y0, x1, y1 = norm
        assert x0 == pytest.approx(50 / PAGE_W)
        assert x1 == pytest.approx(300 / PAGE_W)
        # top edge (t=750) maps to y0 measured from the top
        assert y0 == pytest.approx(1 - 750 / PAGE_H)
        assert y1 == pytest.approx(1 - 700 / PAGE_H)

    def test_topleft_origin_kept_as_is(self):
        bbox = BoundingBox(l=50, b=100, r=300, t=150, coord_origin=CoordOrigin.TOPLEFT)
        norm = _normalize_bbox(bbox, PAGE_W, PAGE_H, "/texts/0")
        assert norm is not None
        assert norm[1] == pytest.approx(100 / PAGE_H)
        assert norm[3] == pytest.approx(150 / PAGE_H)

    def test_inverted_box_rejected(self):
        bbox = BoundingBox(l=300, b=700, r=50, t=750, coord_origin=CoordOrigin.BOTTOMLEFT)
        assert _normalize_bbox(bbox, PAGE_W, PAGE_H, "/texts/0") is None

    def test_out_of_page_box_rejected(self):
        bbox = BoundingBox(l=-50, b=700, r=3000, t=750, coord_origin=CoordOrigin.BOTTOMLEFT)
        assert _normalize_bbox(bbox, PAGE_W, PAGE_H, "/texts/0") is None

    def test_degenerate_box_rejected(self):
        bbox = BoundingBox(l=100, b=700, r=100, t=750, coord_origin=CoordOrigin.BOTTOMLEFT)
        assert _normalize_bbox(bbox, PAGE_W, PAGE_H, "/texts/0") is None

    def test_zero_page_size_rejected(self):
        bbox = BoundingBox(l=50, b=700, r=300, t=750, coord_origin=CoordOrigin.BOTTOMLEFT)
        assert _normalize_bbox(bbox, 0.0, 0.0, "/texts/0") is None


# ---------------------------------------------------------------------------
# Region extraction
# ---------------------------------------------------------------------------

class TestRegions:
    def test_valid_prov_produces_region_with_page_numbers(self):
        prov = _prov(1, 50, 700, 300, 750)
        regions = _regions_for_item([prov], PAGES, "/texts/0")
        assert len(regions) == 1
        region = regions[0]
        assert region.page_index == 0
        assert region.page_number == 1
        assert all(0.0 <= v <= 1.0 for v in region.bbox_norm)
        assert region.item_ref == "/texts/0"

    def test_missing_page_size_skips_region(self):
        prov = _prov(2, 50, 700, 300, 750)
        assert _regions_for_item([prov], PAGES, "/texts/0") == []

    def test_nested_prov_list_is_flattened(self):
        prov = _prov(1, 50, 700, 300, 750)
        regions = _regions_for_item([[prov]], PAGES, "/texts/0")
        assert len(regions) == 1


# ---------------------------------------------------------------------------
# Document walking
# ---------------------------------------------------------------------------

class TestWalkDocument:
    def test_items_kinds_and_section_paths(self):
        doc = build_datasheet_doc()
        items, warnings, title = _walk_document(doc, PAGES)
        kinds = [i.kind for i in items]
        assert "title" in kinds
        assert "section_header" in kinds
        assert "paragraph" in kinds
        assert "table" in kinds

        # prose under "Electrical Characteristics" carries the section path
        elec = [i for i in items if i.kind == "paragraph" and "LM358 operates" in i.verbatim_text]
        assert elec and elec[0].section_path == ["Electrical Characteristics"]

        # nested heading under level-1 keeps full breadcrumb
        nested = [i for i in items if i.kind == "section_header" and i.verbatim_text == "Typical Application"]
        assert nested and nested[0].heading_level == 2

        app = [i for i in items if i.kind == "paragraph" and "battery-operated" in i.verbatim_text]
        assert app and app[0].section_path == ["Electrical Characteristics", "Typical Application"]

    def test_table_structure_extracted(self):
        doc = build_datasheet_doc()
        items, _, _ = _walk_document(doc, PAGES)
        table = next(i for i in items if i.kind == "table")
        assert table.table is not None
        assert table.table.col_headers == ["Parameter", "Value"]
        assert table.table.rows[1] == ["Supply voltage", "32 V"]
        # verbatim_text carries the markdown rendering
        assert "Supply voltage" in table.verbatim_text

    def test_regions_and_highlight_status(self):
        doc = build_datasheet_doc()
        items, _, _ = _walk_document(doc, PAGES)
        for item in items:
            assert item.regions, f"missing region for {item.item_ref}"
            assert item.highlight_status == "available"
            assert item.page_number == 1
            assert item.page_index == 0

    def test_no_prov_marks_unavailable_and_warns_for_table(self):
        doc = build_datasheet_doc()
        # strip prov from the table
        table_item = next(item for item, _lvl in doc.iterate_items() if "tables/" in item.self_ref)
        table_item.prov = []
        items, warnings, _ = _walk_document(doc, PAGES)
        table = next(i for i in items if i.kind == "table")
        assert table.highlight_status == "unavailable"
        assert table.regions == []
        assert any("tables/0" in w for w in warnings)

    def test_title_extracted(self):
        doc = build_datasheet_doc()
        _, _, title = _walk_document(doc, PAGES)
        assert title == "LM358 Datasheet"


# ---------------------------------------------------------------------------
# Artifact persistence
# ---------------------------------------------------------------------------

def _make_extraction(tmp_path):
    items = [
        ExtractedItem(
            item_ref="/tables/0",
            kind="table",
            verbatim_text="| Parameter |",
            section_path=["Electrical Characteristics"],
            page_index=0,
            page_number=1,
            regions=[SourceRegion(page_index=0, page_number=1, bbox_norm=[0.1, 0.2, 0.8, 0.5], item_ref="/tables/0")],
            highlight_status="available",
            table=TableBlock(num_rows=2, num_cols=3, col_headers=["Parameter"], rows=[["Parameter"], ["Vsupply"]]),
        ),
    ]
    return DoclingExtraction(
        document_id="sha256:" + "a" * 64,
        source="https://example.com/lm358.pdf",
        title="LM358 Datasheet",
        page_count=1,
        items=items,
        warnings=[],
        conversion_status="success",
        pages=PAGES,
    )


class TestArtifact:
    def test_save_and_load_round_trip(self, tmp_path):
        extraction = _make_extraction(tmp_path)
        path, uri = save_artifact(extraction, str(tmp_path))
        assert uri == f"internal://documents/{extraction.document_id}"
        assert path.endswith(".json")

        payload = load_artifact(path)
        assert payload["pipeline"] == PDF_DOCLING_PIPELINE
        assert payload["document_id"] == extraction.document_id
        assert payload["items"][0]["item_ref"] == "/tables/0"
        assert payload["items"][0]["regions"][0]["bbox_norm"] == [0.1, 0.2, 0.8, 0.5]
        assert payload["pages"]["0"] == {"width": PAGE_W, "height": PAGE_H}

    def test_tampered_artifact_fails_checksum(self, tmp_path):
        extraction = _make_extraction(tmp_path)
        path, _ = save_artifact(extraction, str(tmp_path))
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        payload["title"] = "tampered"
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        with pytest.raises(ValueError):
            load_artifact(path)

    def test_document_id_stable_for_same_bytes(self):
        data = b"some pdf bytes"
        assert compute_document_id(data) == compute_document_id(data)
        assert compute_document_id(data) != compute_document_id(b"other bytes")
        assert compute_document_id(data).startswith("sha256:")


class TestAcceleratorConfig:
    def test_normalize_device_accepts_valid_values(self):
        from backend.extractor.docling_pdf_extractor import _normalize_device

        assert _normalize_device("cpu") == "cpu"
        assert _normalize_device("CUDA") == "cuda"
        assert _normalize_device("cuda:1") == "cuda:1"
        assert _normalize_device("mps") == "mps"
        assert _normalize_device("xpu") == "xpu"
        assert _normalize_device("auto") == "auto"
        assert _normalize_device(None) == "auto"

    def test_normalize_device_falls_back_to_auto(self):
        from backend.extractor.docling_pdf_extractor import _normalize_device

        assert _normalize_device("tpu") == "auto"
        assert _normalize_device("") == "auto"

    def test_build_converter_wires_accelerator_options(self):
        from backend.extractor.docling_pdf_extractor import _build_converter

        converter = _build_converter(do_ocr=False, table_mode="fast", accelerator_device="cpu", num_threads=2)
        from docling.datamodel.base_models import InputFormat

        options = converter.format_to_options[InputFormat.PDF].pipeline_options
        assert str(options.accelerator_options.device) == "cpu"
        assert options.accelerator_options.num_threads == 2
        assert options.do_ocr is False

    def test_converter_cache_distinguishes_device(self):
        from backend.extractor.docling_pdf_extractor import _get_converter

        cpu = _get_converter(False, "fast", "cpu", 4)
        auto = _get_converter(False, "fast", "auto", 4)
        cpu_again = _get_converter(False, "fast", "cpu", 4)
        assert cpu is cpu_again  # cached
        assert cpu is not auto   # different device -> distinct converter


# ---------------------------------------------------------------------------
# End-to-end conversion (opt-in: downloads Docling models on first run)
# ---------------------------------------------------------------------------

RUN_E2E = os.environ.get("RUN_DOCLING_E2E") == "1"


@pytest.mark.skipif(not RUN_E2E, reason="set RUN_DOCLING_E2E=1 to run Docling model inference tests")
class TestEndToEndConversion:
    def test_synthetic_datasheet_conversion(self):
        import fitz

        pdf_doc = fitz.open()
        page = pdf_doc.new_page(width=612, height=792)
        page.insert_text((72, 72), "LM358 Dual Operational Amplifier Datasheet", fontsize=16)
        rows = [
            ["Parameter", "Conditions", "Min", "Typ", "Max", "Unit"],
            ["Input offset voltage", "Vcm=0, Ta=25C", "1.0", "2.0", "7.0", "mV"],
            ["Input bias current", "Ta=25C", "20", "45", "150", "nA"],
            ["Gain-bandwidth product", "Ta=25C", "0.5", "1.0", "", "MHz"],
        ]
        cols = [72, 210, 340, 400, 450, 500, 550]
        top, rowh = 100, 28
        for r, row in enumerate(rows):
            y = top + r * rowh
            for c, cell in enumerate(row):
                page.insert_text((cols[c] + 4, y + 18), cell, fontsize=8)
            page.draw_line((72, y), (550, y))
        page.draw_line((72, top + len(rows) * rowh), (550, top + len(rows) * rowh))
        for x in cols:
            page.draw_line((x, top), (x, top + len(rows) * rowh))
        pdf_bytes = pdf_doc.tobytes()

        from backend.extractor.docling_pdf_extractor import extract_pdf_document

        extraction = extract_pdf_document(pdf_bytes, "https://example.com/lm358.pdf")
        assert extraction.conversion_status in ("success", "partial_success")
        assert extraction.page_count == 1

        tables = [i for i in extraction.items if i.kind == "table"]
        assert tables, "expected a detected table"
        table = tables[0]
        assert "Input offset voltage" in table.verbatim_text
        assert table.table.col_headers[:3] == ["Parameter", "Conditions", "Min"]
        assert table.table.rows[1][0] == "Input offset voltage"
        assert table.table.rows[1][-1] == "mV"
        assert table.highlight_status == "available"
        assert table.regions[0].page_number == 1
        assert all(0.0 <= v <= 1.0 for v in table.regions[0].bbox_norm)


def test_picture_description_text_from_annotations():
    """VLM description annotations on a picture item become verbatim_text."""
    from types import SimpleNamespace
    from backend.extractor.docling_pdf_extractor import _picture_description_text

    item = SimpleNamespace(
        annotations=[
            SimpleNamespace(kind="description", text="Block diagram of the oscillator"),
            SimpleNamespace(kind="classification", text="ignored"),
            SimpleNamespace(kind="description", text="  "),
        ]
    )
    assert _picture_description_text(item) == "Block diagram of the oscillator"


def test_picture_description_text_empty_without_annotations():
    from types import SimpleNamespace
    from backend.extractor.docling_pdf_extractor import _picture_description_text

    assert _picture_description_text(SimpleNamespace(annotations=None)) == ""
    assert _picture_description_text(SimpleNamespace()) == ""


def test_picture_description_text_from_meta():
    """Newer docling-core: VLM output lands in item.meta.description.text."""
    from types import SimpleNamespace
    from backend.extractor.docling_pdf_extractor import _picture_description_text

    item = SimpleNamespace(
        meta=SimpleNamespace(
            description=SimpleNamespace(text="Timing diagram of the output driver")
        ),
        annotations=[],
    )
    assert _picture_description_text(item) == "Timing diagram of the output driver"
