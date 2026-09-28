# Retrieval Evaluation

[← Documentation home](index.md)

Retrieval can be evaluated independently of final generation at
`/retrieval-evals`. The page has three tabs:

## Query Evaluation

Single-query inspection sandbox (`POST /retrieval-evals/run`). Shows query
decomposition, dense/sparse/hybrid candidate sets, ColBERT and cross-encoder
reranking, and subquery coverage. Use it to drill into a query that failed in
a batch run — the results table links back here.

## Evaluations

Batch runner over a labeled dataset (`POST /api/retrieval-evals/run-set`).
Pick a dataset and one or more domains; every query runs through the same
retrieval pipeline used by the single-query form, so dense/sparse/hybrid,
compound splitting, and reranking are controlled variables.

Each query is judged fuzzily — retrieved chunks rarely match expected text
verbatim:

- **document hit** — `expected_document` is a substring (normalized,
  case-insensitive) of the hit's `source`/`url`/`document_id`/`title`
- **page hit** — `expected_page` appears in the hit's `page_numbers`
- **content hit** — `must_contain` strings appear in the chunk text
  (`any` or `all`)

The results table reports first-hit rank per query plus aggregate
hit-rate, MRR, page-hit rate, and content-hit rate per domain. Comparing the
same dataset across domains (e.g. a legacy collection vs its `_docling_v1`
twin) is the intended A/B for pipeline/index changes; it is only meaningful
when the domains contain the same documents.

## Datasets

Editor for labeled YAML datasets stored under `evals/`
(`GET/PUT/DELETE /api/retrieval-evals/datasets/{name}`). Rows: query,
expected document substring, optional expected page, optional must-contain
strings and any/all mode.

```yaml
description: SiT1534 oscillator + LM358 op-amp datasheets
queries:
  - query: "output swing range for the SiT1534?"
    expected_document: sit1534      # substring on source/url/document_id
    expected_page: 6                # optional — checked in page_numbers
    must_contain: ["250 mV", "800 mV"]  # optional — substring on chunk text
    must_contain_mode: any          # any (default) | all
```

Keep a small labeled set per corpus so retrieval changes (models, chunking,
index layouts, rerankers) can be compared before they reach an agent or
application. See [Architecture](architecture.md),
[Compound queries](compound-queries.md), and the
[RAG indexing and citation strategy](../RAG_INDEXING_AND_CITATION_STRATEGY.md).
