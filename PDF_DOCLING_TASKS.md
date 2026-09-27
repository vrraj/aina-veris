# PDF Docling Pipeline — Work Track

Tracks implementation of `internal-specs-docs/pdf-docling-implementation-spec.md`
(additive `POST /index-pdf-docling` pipeline). The spec is the source of truth;
this file is the working task list. Check off a task only when its acceptance
check passes. Keep deferred/blocked items visible with a reason.

## Goal summary

Add a separate Docling-based ingestion pipeline for technical PDFs
(datasheets: part numbers, spec tables, multicolumn layouts) alongside the
existing `/pdf` path, **without changing legacy behavior**:

- New route `POST /index-pdf-docling` -> Docling extraction -> structure-aware
  chunks (item refs + page-region provenance) -> isolated versioned Qdrant
  collection (`<domain_collection>_docling_v1`).
- Stable document/point IDs, typed allowlisted payloads, `pipeline=pdf_docling_v1`.
- `estimate` mode with zero writes; no silent fallback to the legacy parser.
- Long term (deferred, see spec stages 4-7): hybrid retrieval tuning, validated
  citations with viewer highlights, VLM figure descriptions, ColPali.

## Non-goals for this track (deferred)

- Stage 4 retrieval tuning/reranking evaluation — needs real datasheet eval set.
- Stage 5 citation rendering + PDF viewer highlights — needs immutable PDF store
  and UI work; separate track.
- Stages 6-7 (VLM figures, ColPali) — only if evaluations show a gap.

## Blocked / pending input

- Real representative datasheet PDFs (spec stage 0) — not yet supplied. Tests
  currently use a generated synthetic PDF fixture; geometry quality must be
  re-validated on real datasheets before production use.

## Tasks

Legend: `[ ]` pending · `[~]` in progress · `[x]` done (acceptance check passed)

### T0 — Bootstrap

- [x] T0.1 Create feature branch `feat/pdf-docling-pipeline` (keeps `main` and
  legacy `/pdf` untouched). — `git branch --show-current`
- [x] T0.2 Create this tracked task file. — committed

### T1 — Dependency and configuration

- [x] T1.1 Install pinned `docling` into `.venv` (>= 7 days old release,
  currently `2.125.0`) and verify `DocumentConverter` imports. — pip install;
  import verified. Note: docling pulls the torch stack (large).
- [x] T1.2 Pin `docling` in `requirements.txt`; add settings block
  (`pdf_docling_*`) in `backend/core/config.py` + `.env.example` notes.
  Acceptance: `.venv/bin/python -c "from docling.document_converter import DocumentConverter"` OK.
  — Verified; also bumped `pydantic-settings` to 2.15.0 and `beautifulsoup4`
  to 4.15.0 (required by docling-core; app imports re-verified), and
  regenerated `requirements.lock` via `make lock`.

### T2 — Extraction artifact (spec stage 1)

- [x] T2.1 `backend/extractor/docling_pdf_extractor.py`: convert PDF bytes with
  a pinned-options `DocumentConverter`, walk the `DoclingDocument` tree in
  reading order, and emit a typed artifact: items with `item_ref`, `kind`,
  `verbatim_text`, `page_index`, normalized `bbox` (top-left fractions,
  `[0,1]`), heading/section hierarchy, tables with cell/row structure,
  captions, warnings. Reject inverted/out-of-page boxes (mark
  `highlight_status=unavailable`).
  Acceptance: unit test on synthetic PDF; headings, table rows, page numbers
  and normalized bboxes present; no Qdrant writes.
  — `tests/test_docling_extractor.py` (programmatic DoclingDocument, no model
  inference): 17 passed. Opt-in e2e conversion test (RUN_DOCLING_E2E=1)
  passed against a drawn-table PDF: table headers/rows, bboxes, page numbers
  verified.
- [x] T2.2 Artifact persistence: serialize artifact JSON to
  `pdf_docling_artifact_dir` as `sha256:<document_id>.json` with checksum;
  return `artifact_uri`. (PDF byte storage + authorized serving is deferred
  with stage 5; artifact keeps geometry for later citation use.)
  Acceptance: file written, round-trips, checksum matches.
  — Round-trip + tamper-detection tests pass.

### T3 — Structure-aware chunker (spec stage 2)

- [x] T3.1 `backend/extractor/docling_chunks.py`: build chunks from artifact
  items, not exported Markdown. Prose: split at paragraphs/sentences within a
  section, ~300-600 token budget (cl100k, consistent with existing pipeline),
  `embedding_text` prefixed with title + section breadcrumb; `display_text`
  stays verbatim. Tables: keep small tables intact; split large ones by row
  groups with repeated headers/units/conditions; also emit row-level
  key/value representation for exact parameter lookup. Chunks carry
  `chunk_id`, `section_path`, `block_type`, `representation_type`,
  `item_refs`, `regions[]`, `page_numbers[]`.
  Acceptance: unit tests — token budget respected, no chunk crosses unrelated
  sections, table groups repeat headers, every value keeps parameter/unit,
  all chunks resolve to `item_refs` or mark location unavailable.
  — `tests/test_docling_chunks.py` passes; page headers/footers excluded from
  embedding; row_kv chunks carry row-level regions (union of cell bboxes).

### T4 — Indexing adapter (spec stage 3, additive)

- [x] T4.1 `backend/services/pdf_docling_indexing.py`: typed, allowlisted
  payload builder (spec payload example), stable point ID
  `uuid5(domain, pipeline_version, source_key, document_id, chunk ordinal)`,
  `document_id = sha256(pdf bytes)`, `pipeline=pdf_docling_v1`. Write to
  dedicated collection `f"{domain_collection}_docling_v1"` using the existing
  domain embedding spec/router (dimensions must match domain model).
  Acceptance: re-index same document -> same point IDs (no duplicates);
  payload contains exactly the allowlisted fields.
  — `tests/test_pdf_docling_indexing.py`: payload allowlist enforced;
  re-index yields identical IDs; dedicated collection name.
- [x] T4.2 Transactional replace: stage extraction+embeddings, delete previous
  points for `(document_id, pipeline)` only after successful upsert of the new
  version; failures leave prior version searchable. `estimate` writes nothing.
  No legacy fallback on Docling failure — visible error.
  Acceptance: unit tests with mocked Qdrant verify delete-after-upsert
  scoping and estimate no-write.
  — Mocked-Qdrant tests pass: stale points retired only after upsert;
  failed upsert deletes nothing; empty chunks write nothing.

### T5 — Route + service (spec integration section)

- [x] T5.1 `backend/api/endpoints/pdf_docling.py` router +
  `PDFDoclingInput`/response schemas (reuse PDFInput semantics: `file`
  overrides `url` bytes, `url` canonical source; `estimate`, `max_chunks`
  reports omissions, `skip_sections` on normalized heading paths,
  `force_delete` scoped to this pipeline's points only). Wire into
  `backend/main.py` with `enforce_origin_host` + domain resolution.
  Acceptance: route test (TestClient, mocked indexing) returns counters,
  pipeline id, document_id, warnings; legacy `/pdf` route untouched.
  — `tests/test_pdf_docling_route.py` (direct handler invocation; starlette
  TestClient is incompatible with installed httpx): counters, warnings,
  provenance coverage, error mapping (422/503/500), `/pdf` still registered.
- [x] T5.2 Duplicate check for the new route filters on
  `(domain, source_key, pipeline)` only; cannot collide with legacy points.
  — Filters on `(domain, document_id, pipeline)` in the dedicated collection;
  legacy `/pdf` duplicate check untouched (different collection).

### T6 — Tests

- [x] T6.1 `tests/test_docling_extractor.py`, `tests/test_docling_chunks.py`,
  `tests/test_pdf_docling_indexing.py`, `tests/test_pdf_docling_route.py`.
  Fixture: synthetic PDF generated with pymupdf (headings, a spec table with
  units/conditions, multicolumn text). Acceptance: full pytest suite passes.
  — E2E conversion test is opt-in (`RUN_DOCLING_E2E=1`, downloads Docling
  models on first run) to keep CI fast; unit tests are model-free via
  programmatic DoclingDocument fixtures.

### T7 — End-to-end validation (local Qdrant)

- [x] T7.1 Start qdrant (docker compose), index the synthetic sample via
  `POST /index-pdf-docling`, verify points land in the dedicated collection
  with correct payload, re-index yields no duplicates, `estimate` writes
  nothing, and retrieval on the new collection returns expected chunks.
  Verify legacy `/pdf` still works against the original collection.
  — Validated against local Qdrant (host port 6335, `semiconductor_datasheets`
  domain, local fastembed hybrid collection):
  - `document_index_semiconductor_datasheets_docling_v1` auto-created
    (dense 768 + sparse); 3 chunks indexed, provenance coverage 1.0,
    artifact saved to `docling_artifacts/` (gitignored).
  - Re-index without force_delete -> duplicate gate fires (vectors_found=3),
    no writes; forced re-index -> same stable point IDs, count unchanged
    (no duplicates); `estimate` -> "Estimate only", zero writes.
  - Dense retrieval ("Input offset voltage maximum") returns the spec-table
    chunk first with valid normalized region bboxes and page numbers.
  - Legacy collections untouched (all writes went to the `_docling_v1`
    collection only; `/pdf` code path unchanged).

### T8 — Documentation

- [x] T8.1 Document the new endpoint, settings, collection naming, and
  limitations (synthetic-fixture validation only) in `docs/`.
  — `docs/pdf-docling-pipeline.md` added; linked from `docs/index.md` and
  `docs/api-reference.md`.

## Change log

- 2026-09-27: work track created on `feat/pdf-docling-pipeline`.
- 2026-09-27: T1 done — docling 2.125.0 pinned, settings added, lock refreshed.
- 2026-09-27: T2 done — Docling extractor with typed artifact, provenance
  regions, table structure, artifact persistence with checksum.
- 2026-09-27: T3 done — structure-aware chunker: prose sentence packing with
  section breadcrumbs, table row groups with repeated headers, row-level
  key/value chunks with row-granularity regions.
- 2026-09-27: T4/T5/T6 done — indexing adapter (typed allowlisted payload,
  stable IDs, transactional swap, dedicated `_docling_v1` collection),
  `/index-pdf-docling` route + service, test suites for all four layers.
- 2026-09-27: T7 done — end-to-end validation against local Qdrant passed
  (dedicated hybrid collection, duplicate gate, stable-ID re-index, estimate
  no-write, dense retrieval with regions). T8 done — docs added and linked.
