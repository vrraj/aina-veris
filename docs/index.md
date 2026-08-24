---
layout: default
title: "Aina-Veris"
description: "Domain-aware research runtime with A2A, MCP, configurable retrieval, and tool-assisted research."
---

# Aina-Veris

**Domain-aware research runtime for agents, applications, and tool-assisted RAG.**

Aina-Veris combines domain-specific knowledge with configurable retrieval,
tools, models, and prompts in a shared research runtime.

Research can be accessed through **A2A, MCP, REST/OpenAPI, the Web UI, and
embeddable chat**.

## Research Flows

<p>
Aina-Veris can combine domain knowledge with external capabilities during research,
and expose its own domain research capabilities to other agents and applications.
</p>

<h3>Compound, Tool-Assisted Research</h3>

<blockquote>
  <strong>
    "Who are NVDA's largest shareholders? Show me a 6-month price-history chart
    and research current analyst recommendations for NVDA."
  </strong>
</blockquote>

<ul>
  <li>
    <strong>Largest shareholders</strong> →
    Finance domain → Qdrant retrieval
  </li>
  <li>
    <strong>6-month price history</strong> →
    MCP tool → structured market data → SVG chart artifact
  </li>
  <li>
    <strong>Analyst recommendations</strong> →
    MCP client → Tavily web research
  </li>
  <li>
    <strong>Final response</strong> →
    context assembly + grounded inference → citations + artifacts
  </li>
</ul>

<p>
The price-history chart is generated from structured time-series data using
<code>timeseries-sparklines</code>
and inserted into the response during post-processing.
</p>

<p align="center">
  <a href="https://raw.githubusercontent.com/vrraj/aina-veris/main/docs/aina-veris-nvda-research.png">
    <img
      src="https://raw.githubusercontent.com/vrraj/aina-veris/main/docs/aina-veris-nvda-research.png"
      alt="Aina-Veris tool-assisted NVDA research"
      style="max-width: 75%; height: auto;"
    />
  </a>
</p>

<p align="center"><em>Tool-assisted NVDA research outcome. Click image to view full size.</em></p>

<h3>A2A Research Provider</h3>

<ul>
  <li>
    <strong>Aina Markets</strong> <em>(private repository)</em> → AI agent
  </li>
  <li>
    <strong>AI agent</strong> → A2A → Aina-Veris Finance Research Agent
  </li>
  <li>
    <strong>Aina-Veris</strong> → Finance domain + shared research pipeline
  </li>
  <li>
    <strong>Result</strong> → grounded research + sources → Aina Markets
  </li>
</ul>

<p align="center">
  <a href="https://raw.githubusercontent.com/vrraj/aina-veris/main/docs/aina-markets-a2a-veris-research.png">
    <img
      src="https://raw.githubusercontent.com/vrraj/aina-veris/main/docs/aina-markets-a2a-veris-research.png"
      alt="Aina Markets using Aina-Veris as its Finance-domain A2A research agent"
      style="max-width: 75%; height: auto;"
    />
  </a>
</p>

<p align="center"><em>Aina Markets delegating Finance-domain research through A2A. Click image to view full size.</em></p>

<p>
In this flow, <strong>Aina Markets</strong> uses Aina-Veris as its
Finance-domain A2A research agent, delegating domain research and receiving the
grounded result through A2A.
</p>

## One runtime for agents, applications, and operators

Aina-Veris exposes the same domain-scoped research runtime through A2A agents,
MCP tools over Streamable HTTP and stdio, REST APIs, and embeddable chat.
Human operators can use the included workspace to configure domains, prompts,
models, and tools; inspect pipeline execution; test research behavior; and
evaluate retrieval independently of answer generation.

## What Happens: Research Pipeline

<p align="center">
  <img src="https://raw.githubusercontent.com/vrraj/aina-veris/main/docs/aina-veris-pipeline.png" alt="Aina-Veris research pipeline" style="max-width: 100%; height: auto;" />
</p>

<p align="center"><em>The shared pipeline turns a request into a grounded response with citations.</em></p>

Read [Architecture](architecture.md) for the runtime design, retrieval stages,
tools, observability, and security boundary.

## How the System Is Built

<p align="center">
  <img src="https://raw.githubusercontent.com/vrraj/aina-veris/main/docs/aina-veris-architecture.png" alt="Aina-Veris technical architecture" style="max-width: 100%; height: auto;" />
</p>

<p align="center"><em>Interfaces invoke a shared research runtime backed by domain-isolated knowledge and configurable tools.</em></p>

The access surface is separate from the research runtime. A server-owned agent
or domain configuration controls the collection, prompt policy, retrieval
strategy, model path, tools, and response constraints.

<h2>Evaluation &amp; Observability</h2>

<p>
Aina-Veris provides real-time visibility into research execution through
<strong>Server-Sent Events (SSE)</strong>, exposing pipeline stages as they execute.
</p>

<p align="center">
  <img
    src="https://raw.githubusercontent.com/vrraj/aina-veris/main/docs/aina-veris-sse-pipeline.png"
    alt="Aina-Veris SSE pipeline execution showing retrieval, reranking, context assembly, MCP tool calls, and inference"
    style="max-width: 100%; height: auto;"
  />
</p>

<p align="center"><em>Stage events make research execution visible as it happens.</em></p>

<p>
The execution trace follows the request through
<strong>query resolution, retrieval, reranking, context assembly, tool calls, and inference</strong>,
including individual MCP calls such as <code>mcp:get_stock_price_history</code>
and <code>mcp:tavily_search</code>. Stage-level metrics also provide visibility into
<strong>latency, token usage, and inference cost</strong>.
</p>

<p>
  <a href="server-sent-events.md">SSE execution</a>
  &nbsp;·&nbsp;
  <a href="retrieval-evals.md">Retrieval evaluation</a>
</p>

## Build a domain knowledge base

<p align="center">
  <img src="https://raw.githubusercontent.com/vrraj/aina-veris/main/docs/aina-veris-ingestion-pipeline.png" alt="Aina-Veris ingestion pipeline" style="max-width: 100%; height: auto;" />
</p>

<p align="center"><em>Source material is processed into domain-specific knowledge for the shared research runtime.</em></p>

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

## Related repositories

- [Aina-Veris](https://github.com/vrraj/aina-veris) — source and setup overview.
- [timeseries-sparklines](https://github.com/vrraj/timeseries-sparklines) — SVG time-series chart rendering.
- [Aina Markets](https://github.com/vrraj/aina-markets) — private repository, accessible to authorized collaborators.
