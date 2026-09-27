# Docling PDF Ingestion Pipeline

[← Documentation home](index.md)

`POST /index-pdf-docling` is an **additive** ingestion path for technical
PDFs — datasheets with part numbers, specification tables, footnotes, and
multicolumn layouts. It runs alongside the existing `POST /pdf` pipeline
and never changes it: the legacy route, its extractor, and its collections
are untouched.

The pipeline is built on [Docling](https://docling-project.github.io/docling/)
and records citation-grade provenance for every indexed chunk: the source
item reference, page number, and a normalized bounding box suitable for
viewer overlays.

## When to use it

- Use `POST /pdf` for general documents (existing behavior).
- Use `POST /index-pdf-docling` for technical/datasheet PDFs where table
  structure, part-number lookup, and precise citation regions matter.

## How it works

```
POST /index-pdf-docling
  -> Docling extraction (typed artifact, saved outside Qdrant)
  -> structure-aware chunker (item refs + page regions)
  -> embedding via the domain's configured model
  -> Qdrant: <domain_collection>_docling_v1
```

Key properties:

- **Isolated collection.** Points are written to a dedicated versioned
  collection (`<domain_collection>_docling_v1`, e.g.
  `document_index_semiconductor_datasheets_docling_v1`). Legacy `/pdf`
  points are never read or written by this path.
- **Stable IDs.** `document_id = sha256(PDF bytes)`; point IDs derive
  deterministically from `(domain, pipeline_version, source_key,
  document_id, chunk)`. Re-indexing overwrites points instead of
  duplicating them, and stale points from a smaller replacement are retired
  only after the new version is upserted.
- **Structure-aware chunks.** Prose is split within sections with a title +
  section breadcrumb prefix on the embedded text; `display_text` stays
  verbatim. Small tables stay intact; large tables split into row groups
  with repeated headers, plus a row-level key/value representation
  (`representation_type=row_kv`) for exact parameter/value lookup.
- **Provenance.** Every chunk carries `item_refs`, `page_numbers`, and
  normalized `regions` (`[x0, y0, x1, y1]`, top-left origin, fractions of
  page width/height). Items without a trustworthy box are indexed with
  `highlight_status=unavailable` rather than a fabricated region.
- **No silent fallback.** If Docling conversion fails, the route returns a
  visible error (`422`). It never degrades to the legacy parser.
- **Artifacts.** The full extraction artifact (items, table cells, raw
  boxes) is serialized to `docling_artifacts/` with a checksum and referenced
  by `artifact_uri` from each point, keeping citation geometry tied to the
  exact indexed bytes.

## Request

Same shape as `POST /pdf`:

```json
{
  "url": "https://example.com/part.pdf",
  "file": "<base64 PDF bytes — overrides url bytes, url stays canonical>",
  "filename": "part.pdf",
  "active_domain": "semiconductor_datasheets",
  "estimate": false,
  "force_delete": false,
  "max_chunks": 0,
  "skip_sections": []
}
```

- `estimate=true` runs extraction and chunk planning only — no Qdrant or
  artifact writes.
- `force_delete=true` re-indexes this pipeline's points for the document.
  Without it, an already-indexed document returns an `already_indexed`
  confirmation instead of writing.
- `max_chunks` truncates and reports `chunks_omitted_by_max_chunks`.
- `skip_sections` matches normalized heading paths (default: none).

## Response

```json
{
  "message": "PDF indexed via Docling pipeline",
  "pipeline": "pdf_docling_v1",
  "pipeline_version": "1.0",
  "source": "https://example.com/part.pdf",
  "document_id": "sha256:...",
  "title": "...",
  "page_count": 12,
  "collection": "document_index_semiconductor_datasheets_docling_v1",
  "chunks_indexed": 48,
  "chunks_omitted_by_max_chunks": 0,
  "stale_points_deleted": 0,
  "tokens_used": 15230,
  "parsing_warnings": [],
  "provenance_coverage": 0.97,
  "artifact_uri": "internal://documents/sha256:..."
}
```

`provenance_coverage` is the fraction of extracted items with a valid
highlight region. `parsing_warnings` surfaces Docling conversion errors and
missing provenance for tables/sections.

## Point payload (allowlisted)

Each Qdrant point stores: `pipeline`, `pipeline_version`, `domain`,
`source_key`, `document_id`, `source`/`url`/`url_lower`/`base_url`/
`base_url_lower`, `document_type`, `title`, `section`, `subsection`,
`section_path`, `chunk_id`, `chunk_index`, `total_chunks`, `block_type`,
`representation_type`, `text` (embedded text), `display_text`,
`item_refs`, `regions`, `page_numbers`, `highlight_status`,
`citation_label`, `artifact_uri`, `embedding_model`/`provider`/`runtime`,
and `token_count`.

## Configuration

Settings (see `.env.example`):

| Setting | Default | Purpose |
|---|---|---|
| `PDF_DOCLING_ENABLED` | `true` | Feature gate for the route. |
| `PDF_DOCLING_ARTIFACT_DIR` | `docling_artifacts` | Extraction artifact store. |
| `PDF_DOCLING_CHUNK_SIZE` | `500` | Embedding token budget per chunk. |
| `PDF_DOCLING_CHUNK_OVERLAP` | `50` | Prose overlap tokens. |
| `PDF_DOCLING_TABLE_ROWS_PER_CHUNK` | `12` | Data rows per table row-group chunk. |
| `PDF_DOCLING_MIN_ROWS_ROW_REPR` | `8` | Tables with more data rows also get `row_kv` chunks. |
| `PDF_DOCLING_COLLECTION_SUFFIX` | `_docling_v1` | Dedicated collection suffix. |
| `PDF_DOCLING_DO_OCR` | `false` | Enable Docling OCR (rapidocr) for scanned pages. |
| `PDF_DOCLING_TABLE_MODE` | `accurate` | Docling TableFormer mode (`fast` or `accurate`). |
| `PDF_DOCLING_ACCELERATOR_DEVICE` | `auto` | Inference device: `auto`, `cpu`, `mps`, `cuda`, `cuda:N`, `xpu`. |
| `PDF_DOCLING_NUM_THREADS` | `4` | CPU threads for Docling model inference. |

## CPU vs GPU

Two independent knobs control whether Docling uses CPU or GPU:

**1. Runtime device** (`PDF_DOCLING_ACCELERATOR_DEVICE`) selects the inference
device per conversion, regardless of which PyTorch build is installed:

- `auto` (default) — best available (CUDA on NVIDIA Linux, MPS on Apple
  Silicon, CPU otherwise)
- `cpu` — force CPU; avoids GPU/accelerator initialization entirely
- `mps` / `cuda` / `cuda:N` / `xpu` — pin a specific accelerator

`PDF_DOCLING_NUM_THREADS` bounds CPU inference threads (relevant in `cpu`
mode and for CPU-side ops). Both apply only to the Docling pipeline; nothing
else in the app is affected.

**2. Install-time PyTorch build.** The default install (`requirements.txt` /
`requirements.lock`) resolves PyTorch from PyPI, which on Linux bundles CUDA
(~4 GB including the `nvidia-*` dependency tree). If you don't have or need
an NVIDIA GPU, install with the CPU-only overlay instead:

```bash
make install-cpu
# or: pip install -r requirements-cpu.txt
```

The overlay adds PyTorch's CPU wheel index
(`https://download.pytorch.org/whl/cpu`). The CPU wheels carry a `+cpu`
local version (e.g. `2.14.0+cpu`), which pip ranks above the plain PyPI
build, so `torch`/`torchvision` resolve to the CPU variant and the
`nvidia-*` packages are skipped entirely (~200 MB instead of ~4 GB on
Linux; macOS is CPU-only either way). For the smallest footprint pair this
with `PDF_DOCLING_ACCELERATOR_DEVICE=cpu`. GPU users install from
`requirements.txt` / `requirements.lock` as usual.

## Operational notes

- **Dependencies.** Docling (pinned in `requirements.txt`) pulls the
  torch stack; the dependency lock reflects this. First conversion on a
  machine downloads Docling model artifacts into the HuggingFace cache.
- **Host access to Qdrant.** When running the app on the host (not in
  Docker), set `QDRANT_PORT=6335` per `docker-compose.yml`.
- **CI.** Unit tests run without Docling model inference (programmatic
  `DoclingDocument` fixtures). The end-to-end conversion test is opt-in via
  `RUN_DOCLING_E2E=1`.

## Limitations and next steps

- Validated against a synthetic datasheet fixture (drawn table, headings,
  prose). **Geometry and table quality must be re-validated on real
  datasheet PDFs before production use** — the fixture cannot establish
  scanning, rotation, or multi-column quality.
- Retrieval currently targets the new collection directly. Hybrid dense +
  sparse fusion tuning, citation rendering, and the PDF viewer highlight
  overlay (spec stages 4–5) are deferred follow-up work tracked in
  `PDF_DOCLING_TASKS.md`.
- VLM figure descriptions and ColPali visual retrieval (spec stages 6–7)
  are optional later experiments.
