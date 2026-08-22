# Aina-Veris --- Initial Release

Aina-Veris is a modular, domain-aware RAG research framework designed for
direct use and integration with AI agents and applications.

## Highlights

-   Domain-isolated knowledge, ingestion, retrieval, prompts, and model
    configuration.
-   Individual and batch ingestion for PDFs, web URLs, and MediaWiki sources.
-   Global prompt instructions with optional domain-specific overrides.
-   A2A domain research agents with AgentCard discovery.
-   MCP server and client support for inbound research access and
    external tool integration.
-   Registry-driven local, REST, and external MCP tools.
-   Configurable dense, sparse, hybrid RRF, ColBERT, and cross-encoder
    retrieval and reranking.
-   Compound-query decomposition and coverage-aware retrieval.
-   Local and hosted models with stage-specific configuration.
-   Grounded responses with citations, tool-backed sources, and
    deterministic artifacts.
-   REST, SSE, MCP, A2A, Web UI, and embeddable chat interfaces.
-   Retrieval evaluation, stage-level observability, token and
    inference-cost tracking.
-   Refactored modular architecture for clearer separation of services,
    configuration, integrations, and improved maintainability.

## Deployment Note

Aina-Veris is a reference framework and does not include a built-in identity
provider, user store, or tenant authorization model. Deployments should apply
authentication, authorization, rate limits, and audit logging appropriate to
their REST, A2A, MCP, ingestion, SSE, and embedded-chat interfaces. See
[Security](SECURITY.md) for deployment guidance.
