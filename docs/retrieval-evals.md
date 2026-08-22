# Retrieval Evaluation

[← Documentation home](index.md)

Retrieval can be evaluated independently of final generation through
`POST /retrieval-evals/run` and the retrieval-evals interface. This makes it
possible to inspect candidate quality before changing prompts or inference
models.

Evaluation output can show:

- retrieval queries and compound-query decomposition;
- dense, sparse, or hybrid/RRF candidate sets;
- ColBERT and cross-encoder or hosted reranking results;
- coverage across subqueries and evidence metadata.

Tune a domain through `prompts/domain_embedding_config.yaml`, then re-index if
the embedding model, vector configuration, or collection changes. Keep a small
evaluation set for each domain so retrieval changes can be compared before they
reach an agent or application.

See [Architecture](architecture.md) and [Compound queries](compound-queries.md).
