# Aina-Veris — Initial Release

Aina-Veris is a domain-aware research framework for building and exposing
grounded research capabilities to agents and applications.

## Highlights

- Domain-isolated knowledge bases with independent Qdrant collections,
  embedding models, retrieval policies, prompts, and model configuration.
- PDF, URL/HTML, and MediaWiki ingestion, including batch planning and cost
  estimation; source, title, section, and document-type metadata are retained.
- Fixed-domain A2A research agents with AgentCard discovery, plus MCP server
  support over Streamable HTTP and stdio. External MCP tools can be discovered
  and used during research.
- REST/OpenAPI, web UI, embeddable chat, and SSE interfaces built on the same
  research runtime.
- Configurable dense, sparse, and hybrid RRF retrieval, with optional ColBERT
  and cross-encoder reranking, compound-query expansion, and coverage-aware
  context assembly.
- Grounded responses with citations, tool-backed sources, and deterministic
  artifacts such as SVG charts.
- Versioned YAML registries for domain, model, prompt, and tool configuration;
  global prompts support domain-specific pipeline-stage overrides.
- Retrieval evaluation independent of generation, with stage-level SSE events,
  latency, token usage, and inference-cost tracking.

## Deployment Note

Aina-Veris is a reference framework and does not include a built-in identity
provider, user store, or tenant authorization model. Deployments should apply
authentication, authorization, rate limits, and audit logging appropriate to
their REST, A2A, MCP, ingestion, SSE, and embedded-chat interfaces. See
[Security](SECURITY.md) for deployment guidance.
