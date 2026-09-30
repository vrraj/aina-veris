# Domain Configuration and Ingestion

[← Documentation home](index.md)

`prompts/domain_embedding_config.yaml` declares how a domain is indexed and
retrieved. A declaration selects its primary Qdrant collection, embedding
provider, vector type, and retrieval mode — and can declare additional
pipeline shards under `collections` (see below).

## Add a domain

1. Add the domain and its collection, embedding configuration, vector type, and
   search mode to `prompts/domain_embedding_config.yaml`.
2. Optionally add prompt overrides in `prompts/prompt_registry.yaml`. Global
   instructions remain in effect unless a domain overrides a stage.
3. Ingest content with that domain selected.

REST and browser clients select `active_domain`. No route code is required.
To make the domain independently callable by agents or MCP clients, also add a
fixed-domain agent definition as described in [A2A](a2a.md).

## Ingestion surfaces

| Source | Endpoint |
|---|---|
| URL or HTML | `POST /index` |
| Uploaded PDF | `POST /pdf` |
| Complex PDF (datasheets, figures, dense tables) | `POST /index-pdf-docling` |
| MediaWiki page | `POST /mediawiki/url` |
| Application-supplied document | `POST /embed` |

Each source passes through parsing, metadata preservation, chunking, configured
embedding, and indexing into the shard its pipeline owns within the domain.

For repeatable corpora, use `scripts/batch/process_docs.py` with an input file
such as `scripts/batch/input/sample_batch_input.json`. Its estimate mode plans
chunk and embedding cost before indexing; use `--no-estimate` only when ready to
write vectors. The `"pipeline"` field routes pdf items: a batch-level value
(`"pymupdf"` or `"docling"`) is the default and per-item values override it.

## Multi-pipeline domains (shards)

A domain is the searchable knowledge boundary; its Qdrant collections are
pipeline shards. `collection_name` is the primary shard — the `collections`
list declares additional shards that are searched together with it:

```yaml
domains:
  semiconductor_datasheets:
    collection_name: document_index_semi_ds
    profile: local-hybrid
    collections:
      - name: document_index_semi_ds_docling_v1
        pipeline: docling
```

- **Fan-out read**: search queries every existing shard with the search mode
  its vector layout supports and merges candidates with reciprocal-rank
  fusion. `/search`, chat, retrieval evaluation, and admin document
  operations all resolve the domain's shard set through the same service.
- **Exclusive write**: a document lives in only one shard. Indexing a document
  that already exists in another shard refuses with the conflicting collection
  named; resubmitting with `force_delete=true` migrates it (new version indexed
  first, then the stale shard's points are retired).
- **Lazy creation**: declared shards are created on first index, so adding the
  declaration is safe before any document uses that pipeline.

A domain with no `collections` behaves exactly as a single-collection domain.
See [Domain shards](../RAG_INDEXING_AND_CITATION_STRATEGY.md#domain-shards-multi-collection-domains)
for the architecture.

Changing a collection, embedding model, vector shape, or chunking policy
requires re-indexing the affected corpus.
