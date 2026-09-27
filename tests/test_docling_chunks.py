"""Tests for the structure-aware Docling chunker (spec stage 2)."""

import os

os.environ.setdefault("OPENAI_API_KEY", "test")

from docling_fixtures import build_extraction

from backend.extractor.docling_chunks import build_chunks


class TestProseChunks:
    def test_prose_chunks_carry_breadcrumb_and_refs(self):
        extraction = build_extraction()
        plan = build_chunks(extraction)
        prose = [c for c in plan.chunks if c.block_type == "prose"]
        assert prose
        for chunk in prose:
            assert chunk.embedding_text.startswith(extraction.title)
            assert chunk.item_refs
            assert chunk.page_numbers == [1]
            assert chunk.display_text  # verbatim text preserved
        elec = [c for c in prose if "LM358 operates" in c.display_text][0]
        assert elec.section_path == ["Electrical Characteristics"]
        assert elec.citation_label.startswith("LM358 Datasheet > Electrical Characteristics")

    def test_no_chunk_crosses_sections(self):
        extraction = build_extraction()
        plan = build_chunks(extraction)
        for chunk in plan.chunks:
            if chunk.block_type == "prose":
                assert chunk.section_path in (
                    [],
                    ["Electrical Characteristics"],
                    ["Electrical Characteristics", "Typical Application"],
                )

    def test_page_footer_excluded(self):
        extraction = build_extraction()
        plan = build_chunks(extraction)
        assert not any("Rev 1.0" in c.display_text for c in plan.chunks)

    def test_token_budget_respected(self):
        extraction = build_extraction()
        budget = 40
        plan = build_chunks(extraction, chunk_size=budget, chunk_overlap=0)
        for chunk in plan.chunks:
            if chunk.block_type == "prose":
                assert chunk.token_count <= budget + 20, chunk.token_count

    def test_skip_sections_drops_matching_section(self):
        extraction = build_extraction()
        plan = build_chunks(extraction, skip_sections=["Typical Application"])
        assert not any(
            c.section_path and c.section_path[-1] == "Typical Application"
            for c in plan.chunks
        )
        assert plan.skipped_sections == ["typical application"]

    def test_max_chunks_truncation_reports_omissions(self):
        extraction = build_extraction()
        full = build_chunks(extraction)
        plan = build_chunks(extraction, max_chunks=2)
        assert len(plan.chunks) == 2
        assert plan.omitted_chunks == len(full.chunks) - 2
        assert plan.total_chunks == len(full.chunks)

    def test_chunk_ids_stable_and_sequential(self):
        extraction = build_extraction()
        plan1 = build_chunks(extraction)
        plan2 = build_chunks(extraction)
        assert [c.chunk_id for c in plan1.chunks] == [c.chunk_id for c in plan2.chunks]
        assert [c.chunk_index for c in plan1.chunks] == list(range(len(plan1.chunks)))
        for chunk in plan1.chunks:
            assert chunk.chunk_id.startswith("chunk-")
            assert chunk.document_id == extraction.document_id


class TestTableChunks:
    def test_small_table_stays_intact(self):
        extraction = build_extraction()
        plan = build_chunks(extraction)
        tables = [c for c in plan.chunks if c.block_type == "table"]
        assert tables
        full = [c for c in tables if c.representation_type == "table_full"]
        assert full
        chunk = full[0]
        assert "Supply voltage" in chunk.display_text
        assert "32 V" in chunk.display_text
        assert chunk.item_refs and chunk.item_refs[0].startswith("#/tables/")
        assert chunk.regions  # table region preserved
        assert chunk.page_numbers == [1]

    def test_large_table_splits_into_row_groups_with_repeated_headers(self):
        extraction = build_extraction(big_table=True)
        # tiny budget forces row-group splitting
        plan = build_chunks(extraction, chunk_size=60, rows_per_group=3)
        groups = [c for c in plan.chunks if c.representation_type == "table_rows"]
        assert len(groups) >= 2
        for group in groups:
            assert "Parameter" in group.display_text  # header repeated
            assert group.display_text.count("\n") >= 2  # header + separator + rows
            assert group.section_path == ["Electrical Characteristics"]

    def test_large_table_gets_row_kv_representation(self):
        extraction = build_extraction(big_table=True)
        plan = build_chunks(extraction, chunk_size=60, rows_per_group=3)
        row_chunks = [c for c in plan.chunks if c.representation_type == "row_kv"]
        assert len(row_chunks) >= 5
        for chunk in row_chunks:
            assert "Parameter=" in chunk.display_text or "=" in chunk.display_text
            assert chunk.block_type == "table_row"
            # parameter, value and units all present for the row
            assert any(unit in chunk.lexical_text for unit in ("V", "mV", "nA", "MHz", "us"))

    def test_row_kv_regions_refine_to_table_row(self):
        extraction = build_extraction(with_bboxes=True)
        plan = build_chunks(extraction, chunk_size=60, min_rows_row_repr=3)
        row_chunks = [c for c in plan.chunks if c.representation_type == "row_kv"]
        assert row_chunks
        for chunk in row_chunks:
            assert chunk.regions
            region = chunk.regions[0]
            assert region.get("granularity") == "table_row"
            bbox = region["bbox_norm"]
            assert len(bbox) == 4
            assert all(0.0 <= v <= 1.0 for v in bbox)
            assert bbox[2] > bbox[0] and bbox[3] > bbox[1]

    def test_row_kv_keeps_parameter_unit_and_conditions_together(self):
        extraction = build_extraction()
        plan = build_chunks(extraction, chunk_size=60, min_rows_row_repr=3)
        offset_row = next(
            c for c in plan.chunks if c.representation_type == "row_kv" and "offset" in c.lexical_text
        )
        assert "Input offset voltage" in offset_row.display_text
        assert "7.0 mV" in offset_row.display_text  # value with unit


class TestChunkPlanShape:
    def test_all_chunks_resolve_item_refs_or_mark_unavailable(self):
        extraction = build_extraction()
        plan = build_chunks(extraction)
        item_refs = {i.item_ref for i in extraction.items}
        for chunk in plan.chunks:
            assert chunk.item_refs
            for ref in chunk.item_refs:
                assert ref in item_refs
            # region-bearing chunks must have valid normalized boxes
            for region in chunk.regions:
                bbox = region["bbox_norm"]
                assert all(0.0 <= v <= 1.0 for v in bbox)

    def test_to_dict_round_trip_fields(self):
        extraction = build_extraction()
        plan = build_chunks(extraction)
        payload = [c.to_dict() for c in plan.chunks]
        for chunk, d in zip(plan.chunks, payload):
            assert d["chunk_id"] == chunk.chunk_id
            assert d["representation_type"] == chunk.representation_type
            assert d["section_path"] == chunk.section_path
            assert d["item_refs"] == chunk.item_refs
