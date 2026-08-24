# Domain Configuration and Ingestion

[← Documentation home](index.md)

`prompts/domain_embedding_config.yaml` declares how a domain is indexed and
retrieved. A declaration selects its Qdrant collection, embedding provider,
vector type, and retrieval mode.

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
| MediaWiki page | `POST /mediawiki/url` |
| Application-supplied document | `POST /embed` |

Each source passes through parsing, metadata preservation, chunking, configured
embedding, and indexing into the selected Qdrant collection.

For repeatable corpora, use `scripts/batch/process_docs.py` with an input file
such as `scripts/batch/input/sample_batch_input.json`. Its estimate mode plans
chunk and embedding cost before indexing; use `--no-estimate` only when ready to
write vectors.

Changing a collection, embedding model, vector shape, or chunking policy
requires re-indexing the affected corpus.
