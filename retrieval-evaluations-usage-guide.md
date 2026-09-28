# Retrieval Evaluations — Usage Guide

How to measure retrieval quality before it reaches an answer. The workbench
lives at `/retrieval-evals` (local dev: `http://localhost:8100/retrieval-evals`)
and has three tabs.

## The three tabs

| Tab | What it does |
|---|---|
| **Query Evaluation** | Single-query sandbox: inspect decomposition, dense/sparse/hybrid candidates, ColBERT and cross-encoder reranks, subquery coverage. Use it to debug one query. |
| **Evaluations** | Batch runner: run every query in a labeled dataset against one or more domains and get judged results + aggregate metrics. |
| **Datasets** | Editor for the labeled YAML sets under `evals/` — create, edit, delete queries in the UI. |

## Judgment semantics (fuzzy by design)

Retrieved chunks almost never contain the expected answer verbatim, so a
dataset case declares *expectations*, not exact strings:

```yaml
queries:
  - query: "output swing range for the SiT1534?"
    expected_document: sit1534          # substring on hit's source/url/document_id/title
    expected_page: 6                    # optional — must appear in hit's page_numbers
    must_contain: ["250 mV", "800 mV"]  # optional — substrings on chunk text
    must_contain_mode: any              # any (default) | all
```

All matching is whitespace-normalized and case-insensitive. A hit reports:

- **document hit** — the expected doc appeared in the ranked results (and its rank)
- **page hit** — only when `expected_page` is set; checks `page_numbers`
- **content hit** — only when `must_contain` is set; substring presence in the chunk text

Page and content checks apply to the **first document hit** — a chunk from the
right document but the wrong section counts as a doc hit with `p✗`/`text✗`.

## Running an evaluation

1. **Evaluations** tab → pick a dataset.
2. Tick one or more domains. Comparisons are only meaningful across domains
   that contain the *same documents* (e.g. `semiconductor_datasheets` vs
   `semiconductor_datasheets_docling` — same PDFs, two pipelines).
3. Set the retrieval knobs (search mode, top-k, compound split, cross-encoder,
   exact match). They apply to every query in the run — that's how you isolate
   "does hybrid / decomposition / rerank help."
4. **Run Evaluation Set**. Each query runs the full orchestration pipeline;
   expect tens of seconds per query on CPU when the cross-encoder is on.

## Reading results

Cell format per query×domain: `hit@1 (retr@3) p✓ text✓`

- `hit@N` — rank of the first hit from the expected document in the final list
- `retr@N` — its rank *before* reranking, shown when rerank moved it
- `p✓/✗` — page expectation met/missed · `text✓/✗` — content expectation met/missed
- `miss` — expected document absent from the final results · `error:` — the query failed

Aggregate row per domain: doc hits `n/N (%)`, MRR, page-hit %, content-hit %, error count.
Click any cell to load that query into **Query Evaluation** for drill-down.

## Useful comparisons

- **Pipeline A/B**: same dataset × legacy collection vs `_docling_v1` collection
- **Mode sweep**: run once with `dense`, again with `hybrid` — the delta is what SPLADE adds
- **Rerank value**: compare `hit@N` vs `retr@N` — if rerank consistently pushes
  hits up, the cross-encoder is earning its latency

## Building a good dataset

- 15–30 queries per corpus is enough to compare configurations
- Mine real spec questions: table cells ("duty cycle min max"), units, part
  numbers, prose questions ("can it be cleaned ultrasonically")
- Set `expected_document` to a filename/product substring, not a full URL
- Only add `expected_page`/`must_contain` where you're confident — missing
  fields are simply not judged, over-strict fields create noise
- Datasets are YAML under `evals/`, so they diff and review in git like code
- Re-label when a legitimate retrieval change reshuffles chunks — a `text✗`
  with `hit@1` usually means the right doc surfaced via a different chunk
  than the one you labeled

## Endpoints (for scripting)

```bash
GET    /api/retrieval-evals/datasets
GET    /api/retrieval-evals/datasets/{name}
PUT    /api/retrieval-evals/datasets/{name}   # create/update
DELETE /api/retrieval-evals/datasets/{name}
POST   /api/retrieval-evals/run-set           # {dataset, domains[], knobs...}
```

See [docs/retrieval-evals.md](docs/retrieval-evals.md) for the feature
reference and [RAG_INDEXING_AND_CITATION_STRATEGY.md](RAG_INDEXING_AND_CITATION_STRATEGY.md)
for how retrieval fits the ingestion→citation pipeline.
