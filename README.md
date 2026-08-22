# Aina-Veris

[![License: GPLv3](https://img.shields.io/badge/license-GPLv3-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Qdrant](https://img.shields.io/badge/vector%20database-Qdrant-dc244c)](https://qdrant.tech/)

**A domain-aware RAG runtime with A2A agents, MCP tool integration, and configurable retrieval pipelines.**

Aina-Veris provides a shared research runtime for building domain-specific AI research systems. Each domain can maintain its own knowledge collection, corpus, embedding and retrieval strategy, and model configuration while using the same research pipeline and integration surfaces. Domain prompts inherit global research and citation instructions, with optional domain-specific overrides for individual stages.

**Adding a domain is configuration-led:** define its collection, embedding and retrieval settings, optional prompt overrides, and ingest the corpus. Add an agent definition to expose the domain through **A2A and MCP**; its AgentCard and domain-scoped research tool are created at startup.

Research can be accessed through **A2A agents, MCP, REST APIs, or embeddable chat**. The shared pipeline retains citations, artifacts, execution visibility, and stage-level metrics for the interfaces that expose them.

## What you can do with Aina-Veris

Aina-Veris provides domain-scoped research capabilities to AI agents and
applications. It indexes source content into domain knowledge bases and exposes
source-backed research responses with citations through A2A, MCP, REST, and
embeddable chat.

```text
Sources → Ingestion → Domain Knowledge → Research Pipeline → Grounded Response + Citations
                                              ↑
                         A2A • MCP • REST • Embeddable Chat
                              Agents and applications
```

### Domain-specific research

Ask a research agent a technical question and receive a grounded response from the knowledge base assigned to that agent.

```text
Semiconductor Research Agent
  → semiconductor_memory domain
  → domain retrieval + reranking
  → grounded research artifact + citations
```

Domains remain isolated: the server-owned agent configuration fixes its collection and research policy rather than accepting an arbitrary knowledge domain from the caller.

### Cross-application research with A2A

Applications and AI systems can discover Aina-Veris research agents through their AgentCards and submit research tasks using A2A.

AINA Markets is an internal trading-application repository and the reference
consumer of the Finance Research Agent. It uses Aina-Veris for market research
before applying its own application synthesis:

```text
AINA Markets
  → Finance Research Agent
  → finance knowledge + research pipeline
  → A2A research artifact + sources
  → application synthesis
```

AgentCards are published per agent:

```text
/agents/<agent-name>/.well-known/agent-card.json
```

To discuss the AINA Markets integration or a similar cross-application
research workflow, contact `ai-musings99@gmail.com`.

### Tool-assisted research

Research is not limited to indexed documents. During inference, Aina-Veris can combine retrieved knowledge with local and external capabilities.

```text
Question
  → domain retrieval
  → Tavily web search through MCP
  → source normalization
  → final inference
  → cited response
```

Local and REST-backed tools can return structured data and, where configured, deterministic SVG chart artifacts without placing raw SVG payloads in model context.

## System Architecture

<p align="center">
  <img src="images/aina-veris-architecture.png" style="max-width: 100%; height: auto;" alt="Aina-Veris technical architecture showing A2A, REST, embedded chat and SSE, the research runtime, domain-isolated Qdrant collections, MCP and local tools, registries, observability, and retrieval evaluation" />
</p>

> **Deployment security:** Aina-Veris defines the security boundary but does
> not provide a built-in identity or authorization system. See
> [Security](SECURITY.md) for authentication and public
> endpoint guidance.

Aina-Veris separates the interfaces used to invoke research from the runtime that executes it.

- **Domain-isolated knowledge** — each domain can use its own Qdrant collection, embedding configuration, prompts, and retrieval policy.
- **Multiple integration surfaces** — A2A, MCP, REST, SSE, and embeddable chat use the shared research runtime.
- **Configurable retrieval** — dense, sparse, hybrid, RRF, ColBERT, and reranking stages can be selected by domain and runtime configuration.
- **Tool-assisted inference** — local tools, external MCP servers, and REST-backed capabilities can participate in research.
- **Traceable execution** — citations, resolved queries, artifacts, SSE stages, token usage, and stage latency remain observable through the pipeline.

## Research Pipeline

A research request moves through four major stages:

**Query Resolution → Retrieval → Context Assembly → Inference**

<p align="center">
  <img src="images/aina-veris-pipeline.png" style="max-width: 100%; height: auto;" alt="Aina-Veris research pipeline showing query resolution, domain retrieval and reranking, evidence-preserving context assembly, tool-aware inference, SSE stages, and a grounded response" />
</p>

The pipeline preserves more than the final answer. A completed request can carry its resolved queries, internal and external citations, tools invoked, generated artifacts, and stage-level token and cost information.

SSE stage events provide real-time visibility while the pipeline is running.

## Building a Domain Knowledge Base

Bring a domain's source material into Aina-Veris so agents and applications can
retrieve it as cited evidence. Index individual PDFs, URLs, MediaWiki pages, or
application-supplied documents; use batch ingestion to plan and process larger
source sets. The domain configuration determines the collection, embedding
path, and retrieval policy used once that knowledge base is queried.

<p align="center">
  <img src="images/aina-veris-ingestion-pipeline.png" style="max-width: 100%; height: auto;" alt="Aina-Veris ingestion pipeline showing individual and batch source ingestion, metadata-preserving processing, domain-aware indexing, and Qdrant domain collections" />
</p>

- **Individual or batch sources** — Index a document directly or process a
  source manifest after estimating chunks and cost.
- **Evidence-preserving processing** — Retain source, document title, section,
  and document-type metadata while parsing and chunking content.
- **Domain-aware indexing** — The domain configuration selects the collection,
  embedding model, vector type, and retrieval path for the resulting knowledge
  base.

### Individual ingestion

The ingestion endpoints accept different source shapes but feed one pipeline:
parse the source, preserve useful metadata, chunk the content, create the
configured vectors, and store them in the selected domain collection.

| Source | Endpoint | Use |
|---|---|---|
| URL or HTML | `POST /index` | Index a web page or fetched document. |
| Uploaded PDF | `POST /pdf` | Parse and index a PDF. |
| MediaWiki page | `POST /mediawiki/url` | Retrieve and index a MediaWiki article. |
| Supplied document | `POST /embed` | Index content provided directly by an application. |

### Batch ingestion

For repeatable corpus work, `scripts/batch/process_docs.py` reads an input
file such as `scripts/batch/input/sample_batch_input.json`. Its estimate mode
plans chunks and cost before indexing; run it with `--no-estimate` only when
ready to process the sources.

### Domain configuration

A **domain** keeps a knowledge base and its retrieval policy together. Its
definition in `prompts/domain_embedding_config.yaml` names the Qdrant
collection, embedding model, vector type, and search mode. Prompt instructions
inherit global research and citation rules, with optional domain-specific
overrides in `prompts/prompt_registry.yaml`.

Changing an embedding model, vector shape, chunking policy, or collection
requires re-indexing the affected corpus.

### Publish a domain to A2A and MCP

REST and browser callers select a domain with `active_domain`. To publish a
domain as a callable research capability for agents and external applications,
add a `VerisA2AAgent` entry to `VERIS_A2A_AGENTS` in `backend/a2a/config.py`.
Set its stable `name`, fixed `domain`, description, and capability IDs, then
set `A2A_VERIS_PUBLIC_BASE_URL` and restart the service.

An A2A or MCP research agent does not accept a caller-selected domain; its
configured domain is fixed by the server.

Each configured agent produces an AgentCard, A2A task endpoint, and
domain-scoped MCP research tool:

```text
GET  /agents/<agent-name>/.well-known/agent-card.json
POST /agents/<agent-name>/
```

AgentCards are generated routes, not physical JSON files. Publishing a
canonical root `/.well-known/agent-card.json` endpoint is a separate routing
decision for a primary public agent.

## Core Capabilities

### Domain-scoped A2A research agents

Aina-Veris exposes domain-scoped agents through the **Agent-to-Agent (A2A)** protocol. Another application discovers an agent through its AgentCard, sends a research task over JSON-RPC, and receives a Markdown research artifact with sources.

Each agent owns a fixed knowledge domain. Callers provide the question while Aina-Veris retains control of the collection, embedding model, prompts, retrieval policy, tool limits, and output constraints.

```text
External AI system
  → discovers Veris AgentCard
  → calls a domain-specific A2A research agent
  → Veris applies the agent's fixed domain and pipeline policy
  → retrieval, reranking, tools, and synthesis
  → Markdown research artifact + optional source metadata
```

[Explore the A2A integration →](README_A2A.md)

### REST and SSE interfaces

The FastAPI service exposes the shared chat and retrieval implementation to external applications through REST endpoints and OpenAPI.

| Interface | Endpoints | Function |
|---|---|---|
| **Stateless chat** | `POST /chat` | Caller supplies the message, optional history, and pipeline parameters. |
| **Session chat** | `POST /chat/session`, `POST /chat/{session_id}` | Veris keeps server-side conversation history for the session. |
| **Vector search** | `POST /search` | Runs direct dense, sparse, or hybrid retrieval without answer generation. |
| **Ingestion** | `POST /index`, `POST /pdf`, `POST /mediawiki/url`, `POST /embed` | Indexes URLs, PDFs, MediaWiki content, or a supplied document in the selected domain. |
| **Retrieval evaluation** | `POST /retrieval-evals/run` | Runs retrieval and optional reranking stages independently of generation. |
| **Stage stream** | `GET /chat/stream/stages?query_id=...` | Streams pipeline events and keepalives over Server-Sent Events. |

OpenAPI documentation is available at [`/docs`](http://localhost:8100/docs).

The API and browser application invoke the same orchestration and retrieval services; they are not separate implementations.

The supplied server-managed session store is in-memory. Applications that need durable conversation history across restarts should own that state or replace the store.

Origin and Host allowlists can be configured for selected critical routes. They do not provide authentication or authorization; an internet-facing REST deployment requires an appropriate identity and access layer.

### MCP — server and client

Aina-Veris uses MCP in both directions.

**As an MCP client**, it can discover and invoke external MCP capabilities such as Tavily Search during research.

**As an MCP server**, Aina-Veris publishes each configured research-agent
definition as a domain-scoped MCP tool. The repository includes Mountains,
Finance, and Semiconductor Memory as reference agent and tool definitions.

**Included reference MCP research tools:**

| MCP tool | Fixed knowledge domain |
|---|---|
| `research_mountains` | Mountains |
| `research_finance` | Finance |
| `research_semiconductor` | Semiconductor |

Each tool uses the same server-owned domain, limits, and research service as the corresponding A2A agent.

Additional collections become callable research capabilities when their domain
is configured, corpus is indexed, and a `VerisA2AAgent` definition is added.

MCP clients can connect through Streamable HTTP at:

```text
http://<aina-veris-host>:8100/mcp
```

For stdio clients, run the server module from the repository environment:

```json
{
  "mcpServers": {
    "aina-veris": {
      "command": "/absolute/path/to/aina-veris/.venv/bin/python",
      "args": ["-m", "backend.integrations.mcp.server"],
      "cwd": "/absolute/path/to/aina-veris"
    }
  }
}
```

[Read the MCP specification →](docs/mcp_specs.md) · [Configure tools →](docs/tool_registry.md)

The MCP endpoint does not add authentication itself. Put an authentication and authorization layer in front of `/mcp` before exposing it outside a trusted network; see [Security](SECURITY.md).

### Configurable vector retrieval

Qdrant is the retrieval layer, but Aina-Veris makes its strategy explicit and configurable per domain.

| Retrieval capability | What it adds |
|---|---|
| **Dense vectors** | Semantic similarity for concept-level matching. |
| **Sparse vectors** | Lexical precision for terms, identifiers, and exact language. |
| **Hybrid retrieval** | Qdrant fuses dense and sparse results with Reciprocal Rank Fusion (RRF). |
| **ColBERT late interaction** | Token-level comparison to improve precision on the retrieved candidate set. |
| **Cross-encoder reranking** | An additional scoring pass over the selected candidates. |
| **Compound-query RRF** | Independently reranked subqueries are fused so no single facet dominates a multi-part question. |

The retrieval pipeline is query resolution → decomposition when needed → retrieval → optional ColBERT/cross-encoder reranking → coverage-aware context assembly.

[See retrieval evaluation and RRF controls →](docs/retrieval-evals.md) · [Read the compound-query design →](docs/compound-queries.md)

### Local and hosted model paths

Model selection extends across the pipeline rather than applying only to final generation.

Aina-Veris supports configurable models for:

- query embedding
- dense and sparse retrieval
- query rewriting
- ColBERT late interaction
- reranking
- final generation

Hosted and local components can be combined by stage. Local FastEmbed-backed components support dense, sparse, late-interaction, and reranking paths.

### Registry-driven runtime

Versioned YAML configuration keeps runtime decisions separate from application routes.

| Registry | Controls |
|---|---|
| `prompts/domain_embedding_config.yaml` | Domains, Qdrant collections, embedding models, vector types, and search modes. |
| `prompts/local_models_registry.yaml` | Local embedding, late-interaction, and reranker model settings. |
| `prompts/prompt_registry.yaml` | Stage prompts and domain-specific prompt overrides. |
| `prompts/tool_registry.yaml` | Local tools, MCP servers, tool enablement, adapters, and artifact rules. |

These registries provide the configuration inputs used by the transport and orchestration layers when selecting domains, prompts, models, and tools.

[Read the configuration reference →](docs/configuration.md)

## See It in Use

<table style="width:100%; border:none; table-layout:fixed;">
  <tr>
    <td width="50%" align="center" valign="top" style="border:none; padding:8px;">
      <strong>Stage-by-stage research</strong><br><br>
      <img src="images/chat-pipeline-rewrite-context-tools-inference.png" width="100%" alt="Chat pipeline with query rewrite, tool calls, and citations" />
    </td>
    <td width="50%" align="center" valign="top" style="border:none; padding:8px;">
      <strong>Embeddable chat options</strong><br><br>
      <img src="images/chat-embedding-options.png" width="100%" alt="Iframe and loader-script embedding options" />
    </td>
  </tr>
</table>

## Quick Start

**Prerequisites:** Docker Desktop with Docker Compose, Python 3.10+, and an OpenAI and/or Gemini API key. Local retrieval models are optional and download only when enabled.

```bash
git clone https://github.com/vrraj/aina-veris.git
cd aina-veris
bash scripts/rag_setup.sh
```

The setup script creates `.env`, starts Qdrant and the app, creates the local virtual environment, and seeds sample data. Add at least one provider key to `.env`, then open [http://localhost:8100](http://localhost:8100).

```dotenv
OPENAI_API_KEY=your_openai_api_key
# or
GEMINI_API_KEY=your_gemini_api_key
```

## Extending Aina-Veris

### Add an external MCP server

Add an enabled server entry to `prompts/tool_registry.yaml`.

The existing Tavily connection is configured as:

```yaml
mcp_servers:
  tavily-search:
    enabled: true
    transport: streamable_http
    url: https://mcp.tavily.com/mcp/
    integration: tavily
    auth:
      type: query_parameter
      parameter: tavilyApiKey
      env: TAVILY_API_KEY
```

Set `TAVILY_API_KEY` in `.env`; credentials are not stored in the tool registry. At startup, Veris discovers tools from enabled MCP servers and merges them with the enabled local tool catalog.

### Add local and REST-backed tools

Add a tool definition to `prompts/tool_registry.yaml` and implement its executor outside API routes.

The repository includes local tools for capabilities such as weather, nearby airports, and stock-history rendering. REST-backed dependencies can be normalized by the tool executor before their results enter the research pipeline.

Configured structured tool output can be mapped to deterministic SVG chart artifacts.

### Embed Aina-Veris in another application

`chat-embed.html` is a small iframe client over the same `POST /chat` API.

```html
<div id="support-chat"></div>
<script
  src="https://veris.example/static/embed-loader.js"
  data-target="#support-chat"
  data-active_domain="finance"
  data-top_k="5"
  data-height="450px"
></script>
```

The loader passes other `data-*` values through as embed query parameters.

[Read the embedded-chat guide →](docs/embedded-chat.md)

## Evaluation and Observability

Aina-Veris exposes retrieval independently from generation so retrieval quality can be inspected before evaluating the final answer.

Retrieval evaluation supports inspection of intermediate candidates, fusion, reranking, and coverage.

Each request is assigned a `query_id`. Clients can subscribe to:

```text
GET /chat/stream/stages?query_id=...
```

SSE stage events provide real-time visibility into retrieval, reranking, context assembly, tool calls, and inference. Request metrics provide stage-level latency, token usage, and cost traceability.

[Retrieval evaluation guide →](docs/retrieval-evals.md)

## Interfaces and Documentation

| Interface | Start here |
|---|---|
| **Web UI and REST / OpenAPI** | [Generated OpenAPI documentation](http://localhost:8100/docs) |
| **Embeddable chat** | [Embedded chat guide](docs/embedded-chat.md) |
| **A2A research agents** | [A2A integration guide](README_A2A.md) |
| **MCP tools and registry** | [MCP specification](docs/mcp_specs.md) · [Tool registry guide](docs/tool_registry.md) |
| **Retrieval tuning** | [Retrieval evaluation guide](docs/retrieval-evals.md) · [Compound queries](docs/compound-queries.md) |
| **Operations and deployment** | [Security](SECURITY.md) · [Development](docs/development.md) · [Troubleshooting](docs/troubleshooting.md) · [Architecture](docs/architecture.md) |

## Security

Aina-Veris is a reference framework and does not include a built-in identity
provider, user store, or tenant authorization model. Deployments should apply
authentication and authorization appropriate to their environment, such as an
API gateway, OAuth/OIDC, service-to-service tokens, or a private network
boundary.

Public REST, A2A, MCP, and ingestion endpoints should also use appropriate
rate limits and audit logging. Origin and Host allowlists are deployment
controls, not authentication. See [Security](SECURITY.md) for the full
deployment guidance, including MCP authorization and metadata requirements.

## Operations and Verification

```bash
# Application lifecycle
make start
make rebuild
make stop
make start-debug

# Qdrant operations
make qdrant-status
make qdrant-collections
make qdrant-logs

# Seed sample data into an empty local environment
make seed

# MCP server contracts and interactive inspection
.venv/bin/python -m pytest tests/mcp/test_research_server.py
npx @modelcontextprotocol/inspector http://localhost:8100/mcp
```

## License

Aina-Veris is available under the [GNU GPLv3](LICENSE).

Commercial licensing is available for organizations that need proprietary product integration or custom deployments: `ai-musings99@gmail.com`.
