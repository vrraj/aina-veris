# Aina-Veris — v2.0.0 "Docling Ingestion & Citation Provenance"

A feature release centered on a second PDF ingestion pipeline for technical
documents, multi-shard hybrid retrieval, and end-to-end citation provenance
that deep-links chat sources into a highlighted PDF viewer.

## Highlights

### Ingestion

- **Docling technical-PDF pipeline** — `POST /index-pdf-docling` (plus an SSE
  variant at `/index-pdf-docling/stream` with live stage progress and cancel).
  Layout analysis (`docling-layout-heron`) and TableFormer table structure
  feed a structure-aware chunker that preserves section hierarchy and
  per-item bounding-box provenance. Writes to a dedicated `*_docling_v1`
  shard — additive, never touches legacy collections.
- **Opt-in figure enrichment** — `PDF_DOCLING_PICTURE_DESCRIPTION*` sends
  detected picture regions to a captioning VLM (SmolVLM-256M default; any
  HF repo id supported); OCR (`PDF_DOCLING_DO_OCR`) and higher-DPI crops
  (`PDF_DOCLING_IMAGES_SCALE`) are similarly opt-in. Docling conversion is
  instrumented stage-by-stage and indexed incrementally for speed.
- **Index PDF form toggle** — "Use Docling pipeline" on the upload page
  routes a submission through Docling without touching the API directly.
- **Selectable batch pipeline** — `pipeline` on `/batch/process_docs`
  (`"pymupdf"` default, `"docling"` for datasheet-style PDFs), set at batch
  level with per-item overrides. Also fixes batch dispatch, which had been
  failing every item on a request-context bug since the initial release.
- **Legacy `/pdf` upgrades** — chunks now carry citation regions (line/table
  bounding boxes), and the source PDF is persisted alongside Docling
  artifacts so `file://` and `uploaded://` citations can open the document.

### Retrieval

- **Multi-shard domains** — search, chat, and evals fan out across a domain's
  collections and merge candidates with reciprocal rank fusion; writes are
  exclusive to one shard per document, and document admin operations are
  shard-aware.
- **Local hybrid profile** — `profile: local-hybrid` runs dense
  (bge-base-en-v1.5) + sparse (SPLADE) retrieval fused via Qdrant RRF per
  shard; domain definitions gain a `profile` shorthand so only
  `collection_name` + `profile` are required.
- **Retrieval evaluations** — labeled dataset store, run-set endpoint, and
  an evals UI for measuring retrieval quality independent of generation.

### Citations

- **Clickable chat sources** — persisted source PDFs are served back
  (`GET /docling-document/{id}`); citations deep-link into a vendored pdf.js
  viewer at the cited page with the cited region highlighted in yellow
  (all regions of the source chunk are drawn). HTML/MediaWiki sources link
  with `#:~:text=` scroll fragments.
- **Structured citation payloads** — `document_id`, `artifact_uri`,
  `page_number(s)`, `regions`, `highlight_status`, and `citation_label`
  carried on every indexed chunk (Docling precise; legacy best-effort).

### Operations

- **Manage Models** (`/models.html`) — inspect every model cache: idle time,
  on-disk vs in-memory status, with Eject / Reload / Eject-all actions
  backed by `/models/cache*` admin endpoints.
- **Timer-based idle eviction** — all model caches (embeddings, rerankers,
  Docling converters) are swept by a shared background thread on TTL
  expiry rather than only on next access; models still load lazily on
  first use and reload transparently after eviction. Knobs:
  `MODEL_CACHE_IDLE_TTL_SECONDS` (300 s) and
  `INGESTION_MODEL_CACHE_IDLE_TTL_SECONDS` (900 s); `0` pins a cache.
  `PDF_DOCLING_WARMUP_ON_STARTUP` pre-downloads the Docling model stack so
  first index doesn't pay the fetch cost; HF cache persists in the models
  volume.
- **MCP tool-call timeouts** — external MCP tools are bounded by
  `MCP_TOOL_TIMEOUT_SECONDS` (default 30 s).
- **Origin/Host validation fix** — a valid Host header can no longer
  rescue a forged `Origin` on state-changing endpoints.
- **UI polish** — Manage Models card actions, nav alignment, metadata
  viewer layout, and config-page domain selector clarifications.
- **Architecture documentation** — `RAG_INDEXING_AND_CITATION_STRATEGY.md`
  is the canonical reference for ingestion paths, indexing, retrieval,
  citations, and the model lifecycle.

## Upgrade notes

- Docling adds PyTorch and ONNX-runtime dependencies; CPU-only wheels are
  the default (`PyTorch`/`TORCH_*` settings, `mps` supported natively).
- Existing documents must be re-indexed to gain citation regions and
  persisted sources — legacy points have no geometry to retrofit.
- No data migrations; `*_docling_v1` collections are created on demand.

---

# Aina-Veris — Initial Release

Aina-Veris is a **domain-aware RAG and research framework** for building and exposing
grounded research capabilities to agents and applications.

## Highlights

- **A2A research agents** with AgentCard discovery and execution.
- **MCP server** support over Streamable HTTP and stdio, exposing domain-scoped research tools.
- **MCP client** support for discovering and using external MCP tools during research.
- **Domain-isolated knowledge bases** with independent Qdrant collections,
  embedding models, retrieval policies, prompts, and model configuration.
- **REST/OpenAPI, web UI, embeddable chat, and SSE interfaces** built on the same
  research runtime.
- PDF, URL/HTML, and MediaWiki ingestion, including **batch cost estimation and ingestion**; source, title, section, and document-type **metadata** are retained.
- **Configurable** dense, sparse, and hybrid RRF retrieval, with optional ColBERT
  and cross-encoder reranking, compound-query expansion, and coverage-aware
  context assembly.
- Grounded **responses with citations**, tool-backed sources, and deterministic
  **artifacts** such as SVG charts.
- Versioned YAML repositories for domain, model, **prompt registry, and tool registry**;
  global prompts support domain-specific pipeline-stage overrides.
- **Retrieval evaluation** independent of generation, with stage-level **SSE events**,
  latency, token usage, and **inference-cost tracking**.

>Aina-Veris also provides human operators a workspace to configure, inspect,
test, and evaluate the same research runtime used by A2A agents, MCP clients,
and applications.

## Deployment Note

Aina-Veris is a reference framework and does not include a built-in identity
provider, user store, or tenant authorization model. Deployments should apply authentication, authorization, rate limits, and audit logging appropriate to
their REST, A2A, MCP, ingestion, SSE, and embedded-chat interfaces. See
[Security](SECURITY.md) for deployment guidance.
