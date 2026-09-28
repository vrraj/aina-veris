# RAG Indexing and Citation Strategy

How a document becomes searchable chunks, and how an answer links back to the
exact passage it came from. This is the strategy map — implementation details
live in the docs linked at the bottom.

```
document → extract → chunk (+ metadata/provenance) → embed (dense + sparse)
        → index (Qdrant) → hybrid search → answer → structured sources
        → deep link → cited page/section/region
```

## 1. Ingestion paths (routing by document type)

| Document type | Endpoint | Extractor | Why |
|---|---|---|---|
| Simple PDFs — headings, prose, simple tables | `POST /pdf` | pymupdf4llm (+ PyMuPDF fallback) | Fast markdown extraction; cheap; adequate when layout/table structure is simple |
| Datasheets & dense technical PDFs — multicolumn, complex tables, figures, footnotes | `POST /index-pdf-docling` | Docling (layout analysis + TableFormer) | Preserves table structure, section hierarchy, and per-item bounding-box provenance |
| HTML pages | HTML ingestion route | `html_extractor.py` | DOM-aware; heading `id`s become `#section` anchors in chunk URLs |
| MediaWiki pages | MediaWiki ingestion route | `mediawiki_extractor.py` | Same anchor behavior via section URLs |

Notes:

- The Docling path is **additive** — it writes a dedicated collection
  (`*_docling_v1`) and never touches legacy collections.
- Docling persists both an extraction artifact (JSON with full provenance)
  and the raw PDF beside it, which powers served citations later.
- **Future options** (deferred): Docling picture-description enrichment
  (VLM figure captions) for image-heavy docs, and ColPali-style visual
  retrieval over page images. The payload/`regions` plumbing already
  supports both.

## 2. Indexing and metadata

**Chunking** (per path):

- Legacy PDF: markdown-aware section chunks.
- Docling: structure-aware chunks — prose sentence packing with section
  breadcrumbs, table row groups with repeated headers, plus row-level
  key/value chunks for large tables.
- HTML/wiki: section-scoped chunks keyed by heading anchors.

**Key payload fields** (citation-critical):

- `document_id`, `source`/`url`, `title`, `section`, `section_path`
- `regions` — per-item `{page_number, bbox_norm}` where `bbox_norm` is the
  normalized `[x0, y0, x1, y1]` rectangle on the page
- `page_numbers`, `citation_label`, `artifact_uri`

**Vectors:**

- Dense: `BAAI/bge-base-en-v1.5` (768-dim, local) or hosted (OpenAI/Gemini,
  1536-dim) — semantic similarity
- Sparse: `prithivida/Splade_PP_en_v1` (local SPLADE) — learned lexical
  matching; catches part numbers, units, and exact test conditions that
  dense vectors blur
- Named-vector layout (`dense` + `sparse`) with **domain profiles**:
  `legacy-dense`, `hosted-dense`, `local-dense`, `hosted-hybrid`,
  `local-hybrid` — see `prompts/domain_embedding_config.yaml`

**Idempotency:** stable point IDs derived from
`(domain, pipeline_version, source_key, chunk_id)`; re-indexing replaces
points transactionally, so re-ingesting a doc never duplicates.

## 3. Search and citations

**Retrieval:** hybrid dense + sparse fused with Reciprocal Rank Fusion
(`search_mode: hybrid` on hybrid domains). Domain selection routes to the
right collection — e.g. `semiconductor_datasheets_docling` searches the
Docling collection.

**Citations:** chat responses carry a structured `sources` array (full
payloads). The UI renders a clickable list and the redundant plain-text
"Sources:" tail is stripped.

**Deep-link matrix:**

| Source | Link built | Lands on |
|---|---|---|
| `file://` PDF + `regions` | `/pdf-viewer.html?doc=…&page=N&bbox=x0,y0,x1,y1` | Vendored pdf.js viewer renders the page and draws the cited region (yellow box) |
| `file://` PDF, no regions | `/docling-document/{id}#page=N` | Raw PDF at cited page |
| `http(s)` PDF | `url#page=N` | Browser PDF viewer at page |
| `http(s)` HTML/wiki | `url[#id]:~:text=…` | Scroll-to-text fragment: whole chunk ≤8 words, else first-4..last-4 word range; heading `id` anchor preserved |

Text fragments are computed at render time from `payload.text` — they work
on already-indexed documents with no reindex. `file://` links require the
PDF persisted at index time, so documents indexed before persistence must
be re-indexed (no fallback paths).

## 4. Decision guide for a new corpus

- Plain reports/articles with headings and simple tables → `/pdf` + a
  `hosted-dense` or `local-dense` domain.
- Datasheets, spec docs, anything where table cells/figures/units matter →
  `/index-pdf-docling` + `local-hybrid` (SPLADE catches exact part numbers).
- HTML/wiki corpora → standard ingestion; deep links come free from
  heading anchors + text fragments.
- Re-indexing a corpus? Use the same domain/pipeline so stable IDs swap
  points cleanly; migrate legacy collections off `legacy-dense` only when
  re-indexing anyway.

## References

- [Docling PDF pipeline](docs/pdf-docling-pipeline.md) — endpoint, payload
  schema, configuration, operational notes
- `prompts/domain_embedding_config.yaml` — domain profiles and validation
  rules (header comment)
- [README — Domain configuration](README.md) — profile matrix and when to
  use each
- `PDF_DOCLING_TASKS.md` — implementation work log
