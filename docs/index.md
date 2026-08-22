---
layout: default
title: "Aina-Veris Documentation"
description: "Documentation for the Aina-Veris domain-aware RAG runtime."
---

# Aina-Veris Documentation

Aina-Veris is a domain-aware research runtime for AI agents and applications.
It builds domain knowledge from source content and returns grounded research with
citations through A2A, MCP, REST, and embeddable chat.

```text
Sources → Ingestion → Domain Knowledge → Research Pipeline → Grounded Response + Citations
                                              ↑
                         A2A • MCP • REST • Embeddable Chat
```

## System at a glance

<p align="center">
  <img src="aina-veris-architecture.png" alt="Aina-Veris technical architecture" style="max-width: 100%; height: auto;" />
</p>

The access surface is separate from the research runtime. A server-owned agent
or domain configuration controls the collection, prompt policy, retrieval
strategy, model path, tools, and response constraints.

## Research pipeline

<p align="center">
  <img src="aina-veris-pipeline.png" alt="Aina-Veris research pipeline" style="max-width: 100%; height: auto;" />
</p>

Read [Architecture](architecture.md) for the runtime design, retrieval stages,
tools, observability, and security boundary.

## Build a domain knowledge base

<p align="center">
  <img src="aina-veris-ingestion-pipeline.png" alt="Aina-Veris ingestion pipeline" style="max-width: 100%; height: auto;" />
</p>

Domain configuration chooses the collection, embedding path, vector type, and
retrieval policy. See [Domain configuration and ingestion](configuration.md).

## Guides

| Goal | Guide |
|---|---|
| Add a domain or ingest source material | [Configuration and ingestion](configuration.md) |
| Publish a domain as an agent | [A2A research agents](a2a.md) |
| Expose or consume MCP tools | [MCP integration](mcp_specs.md) · [Tool registry](tool_registry.md) |
| Call REST, follow SSE stages, or embed chat | [API surfaces](api-reference.md) · [SSE](server-sent-events.md) · [Embeddable chat](embedded-chat.md) |
| Tune and evaluate retrieval | [Retrieval evaluation](retrieval-evals.md) · [Compound queries](compound-queries.md) |
| Run locally or deploy safely | [Development](development.md) · [Deployment](deployment.md) · [Security](security.md) |
| Diagnose a local installation | [Troubleshooting](troubleshooting.md) |

The repository [README](https://github.com/vrraj/aina-veris/blob/main/README.md)
is the concise product and setup overview.
