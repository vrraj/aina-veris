# Aina-Veris

[![License: GPLv3](https://img.shields.io/badge/license-GPLv3-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Qdrant](https://img.shields.io/badge/vector%20database-Qdrant-dc244c)](https://qdrant.tech/)

**A domain-aware RAG runtime with A2A agents, MCP tool integration, and configurable retrieval pipelines.**

Each research request keeps its domain, retrieval policy, source metadata, and
citations together from collection lookup through the final answer.

## System architecture

<p align="center">
  <img src="images/aina-veris-architecture.png" style="max-width: 100%; height: auto;" alt="Aina-Veris technical architecture showing A2A, REST, embedded chat and SSE, the research runtime, domain-isolated Qdrant collections, MCP and local tools, registries, observability, and retrieval evaluation" />
</p>

### 1. Domain-scoped A2A research agents

Aina-Veris exposes domain-scoped agents through the **Agent-to-Agent (A2A)**
protocol. Another application discovers an agent through its AgentCard, sends a
research task over JSON-RPC, and receives a Markdown research artifact with
sources.

Each agent owns a fixed knowledge domain; callers provide the question, while
Veris retains control of the collection, embedding model, prompts, retrieval
policy, tool limits, and output constraints.

```text
External AI system
  → discovers Veris AgentCard
  → calls a domain-specific A2A research agent
  → Veris applies the agent's fixed domain and pipeline policy
  → retrieval, reranking, tools, and synthesis
  → Markdown research artifact + optional source metadata
```

AINA Markets is an internal trading application and the reference consumer of
the Finance Research Agent. It submits finance research tasks to Aina-Veris,
validates the returned A2A artifact and sources, and uses that research in its
own synthesis flow.

#### Add an A2A AgentCard

The current implementation creates AgentCards dynamically; no per-agent
directory or JSON file is required. Adding an agent is a configuration and
content-indexing workflow:

1. Add the domain, collection, embedding model, and retrieval mode to
   `prompts/domain_embedding_config.yaml`.
2. Ingest the domain corpus with that domain selected so it is indexed into the
   configured Qdrant collection.
3. Add a `VerisA2AAgent` entry to `VERIS_A2A_AGENTS` in
   `backend/a2a/config.py`. Set its stable `name`, fixed `domain`, description,
   and capability IDs. For example, the semiconductor agent is named
   `veris-semiconductor-research-agent`.
4. Set `A2A_VERIS_PUBLIC_BASE_URL` to the address reachable by the consuming
   application, then restart the service.
5. Verify the generated AgentCard and task endpoint:

   ```text
   GET  /agents/<agent-name>/.well-known/agent-card.json
   POST /agents/<agent-name>/
   ```

`backend/a2a/agent_card.py` builds the AgentCard from the agent definition,
including its description, capabilities, supported input modes, and JSON-RPC
task URL. `backend/a2a/router.py` registers both routes for every entry at
startup; `backend/a2a/service.py` invokes the shared stateless research
pipeline with the agent's server-owned domain and limits. Add or update the
corresponding contract coverage in `tests/a2a/test_veris_agent.py`.

The scoped AgentCard URLs are HTTP routes, not files. Publishing a canonical
root `/.well-known/agent-card.json` endpoint is a separate routing decision for
the primary public agent.

[Explore the A2A integration →](README_A2A.md)

### 2. REST and SSE interfaces for external applications

The FastAPI service exposes the same chat and retrieval implementation to
external applications through REST endpoints and OpenAPI. The principal
integration paths are:

| Interface | Endpoints | Function |
|---|---|---|
| **Stateless chat** | `POST /chat` | Caller supplies the message, optional history, and pipeline parameters. |
| **Session chat** | `POST /chat/session`, `POST /chat/{session_id}` | Veris keeps server-side conversation history for the session. |
| **Vector search** | `POST /search` | Runs direct dense, sparse, or hybrid retrieval without answer generation. |
| **Ingestion** | `POST /index`, `POST /pdf`, `POST /mediawiki/url`, `POST /embed` | Indexes URLs, PDFs, MediaWiki content, or a supplied document in the selected domain. |
| **Retrieval evaluation** | `POST /retrieval-evals/run` | Runs retrieval and optional reranking stages independently of generation. |
| **Stage stream** | `GET /chat/stream/stages?query_id=...` | Streams pipeline events and keepalives over Server-Sent Events. |

OpenAPI documentation is available at [`/docs`](http://localhost:8100/docs).
The API and browser application invoke the same orchestration and retrieval
services; they are not separate implementations.

Origin and Host allowlists can be configured for selected critical routes.
They do not provide authentication or authorization. An internet-facing REST
deployment requires an authentication and authorization layer appropriate to
the calling applications.

Use the generated OpenAPI document as the current REST contract.

### 3. MCP tool integration

Aina-Veris uses the **Model Context Protocol (MCP)** to connect its agents to
external capabilities. Enable an MCP server in the tool registry and Veris
discovers its tools at runtime; local tools and MCP tools then enter the same
execution pipeline.

```text
Tool registry YAML
  → enabled local tools + MCP servers
  → runtime MCP tool discovery
  → LLM selects a capability
  → local or MCP execution
  → normalized data + sources
  → answer, table, chart, map, SVG, or other deterministic artifact
```

Server-level result adapters normalize documented provider payloads into a
data-and-sources contract. Parser selection is based on the configured server
integration identifier rather than a remote tool name. Tool-driven answers can
therefore include provider URLs when the adapter returns them.
For structured outputs, deterministic renderers can create artifacts without
putting raw visual payloads in model context.

MCP server configuration, local tool definitions, and artifact rules are held
in the tool registry rather than in API routes.

#### Integration examples

The repository includes examples at three integration boundaries:

| Boundary | Example | Transport and role |
|---|---|---|
| **Internal tools** | Weather, nearby airports, and stock-history rendering | Local executors invoked by the chat pipeline; stock history can produce an SVG chart artifact. |
| **Cross-application tools** | AINA Markets | The internal trading application calls the Veris Finance Research Agent through A2A and uses the returned research artifact in its own workflow. |
| **External tools** | Tavily Search | An external MCP server that provides web-search results through Streamable HTTP. |

These examples use different transports but demonstrate the same general
pattern: Veris accepts or invokes a capability through a defined contract, then
passes normalized output and sources into its shared pipeline.

To connect an external MCP server, add an enabled server entry to
`prompts/tool_registry.yaml`. The existing Tavily connection is configured as:

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

Set `TAVILY_API_KEY` in `.env`; credentials are not stored in the tool
registry. At startup, Veris discovers tools from enabled MCP servers and
merges them with the enabled local tool catalog.

#### Aina-Veris MCP server

Aina-Veris also publishes its own domain-scoped research tools through MCP.
Each tool uses the same server-owned domain, limits, and research service as
the corresponding A2A agent.

| MCP tool | Fixed knowledge domain |
|---|---|
| `research_mountains` | Mountains |
| `research_finance` | Finance |
| `research_semiconductor` | Semiconductor |

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

The current MCP endpoint does not add authentication. Put an authentication
layer in front of `/mcp` before exposing it outside a trusted network; see the
[MCP authentication deployment specification](docs/mcp-authentication.md).

[Read the MCP specification →](docs/mcp_specs.md) · [Configure tools →](docs/tool_registry.md)

### 4. Configurable vector retrieval

Qdrant is the retrieval layer, but Aina-Veris makes its strategy explicit and
configurable per domain. A domain defines its collection, embedding model,
vector type, and search mode; the request pipeline uses that configuration
automatically.

| Retrieval capability | What it adds |
|---|---|
| **Dense vectors** | Semantic similarity for concept-level matching. |
| **Sparse vectors** | Lexical precision for terms, identifiers, and exact language. |
| **Hybrid retrieval** | Qdrant fuses dense and sparse results with Reciprocal Rank Fusion (RRF). |
| **ColBERT late interaction** | Token-level comparison to improve precision on the retrieved candidate set. |
| **Cross-encoder reranking** | An additional scoring pass over the selected candidates. |
| **Compound-query RRF** | Independently reranked subqueries are fused so no single facet dominates a multi-part question. |

The retrieval pipeline is: query resolution → decomposition when needed →
retrieval → optional ColBERT/cross-encoder reranking → coverage-aware context
assembly. The retrieval evaluation UI exposes intermediate results for these
stages.

[See retrieval evaluation and RRF controls →](docs/retrieval-evals.md) · [Read the compound-query design →](docs/compound-queries.md)

### 5. Local and hosted model paths

Aina-Veris supports both **hosted and local** model paths, including
stage-specific selection for embeddings, reranking, query rewriting, and final
generation.

- Hosted providers can be selected for embedding, reranking, and generation.
- Local FastEmbed-backed components support dense, sparse, late-interaction,
  and reranking paths, including BGE-M3 configurations.
- Domain and stage settings can combine hosted and local components.

Model capabilities, parameters, and local-model settings are defined in
registries rather than embedded in route handlers.

### 6. Registry-driven configuration

Versioned YAML configuration defines prompts, tools, domains, and local-model
settings separately from application code.

| Registry | Controls |
|---|---|
| `prompts/domain_embedding_config.yaml` | Domains, Qdrant collections, embedding models, vector types, and search modes. |
| `prompts/local_models_registry.yaml` | Local embedding, late-interaction, and reranker model settings. |
| `prompts/prompt_registry.yaml` | Stage prompts and domain-specific prompt overrides. |
| `prompts/tool_registry.yaml` | Local tools, MCP servers, tool enablement, adapters, and artifact rules. |

These registries provide the configuration inputs used by the transport and
orchestration layers when selecting domains, prompts, models, and tools.

[Read the configuration reference →](docs/configuration.md)

## Integrations

Aina-Veris uses three inbound interfaces for consumers and a registry-driven
tool layer for outbound calls. The routes below are served by the same FastAPI
application and use the shared domain-aware research pipeline.

| Direction | Interface | Route or configuration | Examples |
|---|---|---|---|
| **Inbound** | A2A | `/agents/<agent-name>/` | AINA Markets calls `veris-finance-research-agent`; each agent publishes an AgentCard at `/agents/<agent-name>/.well-known/agent-card.json`. |
| **Inbound** | MCP | `/mcp` | MCP clients discover and call `research_mountains`, `research_finance`, and `research_semiconductor`. Supports Streamable HTTP and stdio. |
| **Inbound** | REST | `POST /chat`, `POST /search`, ingestion endpoints | External applications can request a cited answer, direct retrieval, or domain-scoped indexing. OpenAPI is available at `/docs`. |
| **Outbound** | External MCP servers | `prompts/tool_registry.yaml` → `mcp_servers` | Tavily Search is discovered through Streamable HTTP and supplies web-search results to the research pipeline. |
| **Outbound** | Local tools with REST dependencies | `prompts/tool_registry.yaml` → `tools` | Weather calls Open-Meteo and its geocoding service; nearby-airport lookup uses the local airport dataset with Nominatim geocoding. |
| **Outbound** | Deterministic artifact rendering | `prompts/tool_registry.yaml` → `artifact` | Configured stock-history output can be rendered as an SVG chart artifact before the final answer is assembled. |

### Adding an integration

- **New A2A research agent:** add a domain and collection, then add its
  `VerisA2AAgent` definition in `backend/a2a/config.py`.
- **New external MCP service:** add an enabled server with its transport,
  endpoint, integration identifier, and environment-backed credentials to
  `prompts/tool_registry.yaml`.
- **New local or REST-backed tool:** add a tool definition to the same registry
  and implement its executor outside API routes.
- **New REST consumer:** use the generated contract at `/docs`; the REST routes
  call the same shared services as A2A and MCP.

## Working concepts

### Domains, collections, and routing

A **domain** is the unit that keeps a knowledge base and its retrieval policy
together. Its definition in `prompts/domain_embedding_config.yaml` names the
Qdrant collection, embedding model, vector type, and search mode. For example,
Finance, Mountains, and Semiconductor can each use a separate collection and
different dense, sparse, or hybrid retrieval configuration.

REST and browser callers select a domain with `active_domain`. An A2A or MCP
research agent does not accept a caller-selected domain: its configured domain
is fixed by the server. This prevents a finance or semiconductor agent from
silently searching a different collection.

Changing an embedding model, vector shape, chunking policy, or collection
requires re-indexing the affected corpus. The old vectors are not compatible
with the new retrieval configuration simply because the domain name is the
same.

### Prompts, models, and tools

The system keeps three independent choices explicit:

| Concern | Configuration | Meaning |
|---|---|---|
| **Prompt** | `prompts/prompt_registry.yaml` | Instructions for individual pipeline stages and optional domain-specific overrides. |
| **Model path** | Domain configuration and `prompts/local_models_registry.yaml` | Hosted or local models for embedding, reranking, rewriting, and generation. |
| **Tool policy** | `prompts/tool_registry.yaml` | Which local tools and MCP servers are enabled, how their outputs are adapted, and which artifacts can be rendered. |

This separation lets a deployment change a prompt, use a local reranker, or
enable Tavily without adding that decision to a REST route. Configuration is
still code-adjacent and should be reviewed and deployed like application
configuration.

### Conversation state and pipeline visibility

`POST /chat` is stateless: the client sends the current message and any history
it wants considered. `POST /chat/session` creates a server-managed conversation
and `POST /chat/{session_id}` continues it. The supplied session store is
in-memory, so applications needing durable cross-restart history should own
that state or replace the store.

Each request is assigned a `query_id`. A client can subscribe to
`GET /chat/stream/stages?query_id=...` to receive SSE stage events and
keepalives while retrieval, reranking, tool calls, and generation run. The
stream is an observability view of the request; the REST response remains the
answer contract.

## Ingestion and embedded delivery

### Building a domain knowledge base

The ingestion endpoints accept different source shapes but feed one pipeline:
parse the source, preserve useful metadata, chunk the content, create the
configured vectors, and store them in the selected domain collection.

| Source | Endpoint | Use |
|---|---|---|
| URL or HTML | `POST /index` | Index a web page or fetched document. |
| Uploaded PDF | `POST /pdf` | Parse and index a PDF. |
| MediaWiki page | `POST /mediawiki/url` | Retrieve and index a MediaWiki article. |
| Supplied document | `POST /embed` | Index content provided directly by an application. |

For repeatable corpus work, `scripts/batch/process_docs.py` reads an input
file such as `scripts/batch/input/sample_batch_input.json`. Its estimate mode
plans chunks and cost before indexing; run it with `--no-estimate` only when
ready to process the sources. `make seed` provides a small local sample corpus
for a working environment rather than a production knowledge base.

### Embedding chat in another site

`chat-embed.html` is a small iframe client over the same `POST /chat` API. An
integrator can either host it directly or add the loader script, which creates
the iframe relative to its own URL.

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
Choose the domain and retrieval settings deliberately, and enforce any
authentication, framing, Origin, and content-security policy at deployment.
[The embedded-chat guide](docs/embedded-chat.md) documents the supported
parameters and direct-iframe option.

## RAG pipeline

Aina-Veris connects two deliberate workflows:

```text
INGESTION
Documents / URLs
  → parse and preserve structure
  → chunk and enrich metadata
  → create dense, sparse, or hybrid vectors
  → store in the domain's Qdrant collection

RESEARCH / ANSWERING
Question + conversation
  → resolve the current turn
  → decompose compound questions when useful
  → domain-aware retrieval + optional RRF fusion
  → optional ColBERT and cross-encoder reranking
  → assemble coverage-aware context
  → call local or MCP tools when needed
  → synthesize a cited answer or research artifact
  → stream stages, metrics, and cost
```

<p align="center">
  <img src="images/aina-veris-pipeline.png" style="max-width: 100%; height: auto;" alt="Aina-Veris research pipeline showing query resolution, domain retrieval and reranking, evidence-preserving context assembly, tool-aware inference, SSE stages, and a grounded response" />
</p>

## Interfaces and operational capabilities

- **Multi-domain isolation** — Separate domains can use distinct corpora,
  collections, prompts, embedding models, and retrieval policies.
- **Cited answers and source preservation** — Document and tool-backed
  responses retain their evidence path where available.
- **Pipeline visibility** — SSE provides stage-by-stage progress,
  keepalives, clarification requests, per-pair rerank visibility, and cost
  accounting.
- **Retrieval evaluation workspace** — Test the retrieval stack independently
  of generation and inspect intermediate candidates, fusion, and coverage.
- **Flexible state management** — Use stateless chat, server-managed sessions,
  or a domain-scoped A2A task.
- **Embeddable delivery** — Add a domain-aware assistant to another site by
  iframe or loader script.
- **REST-first integration** — The same shared services are available through
  FastAPI and OpenAPI for ingestion, chat, search, sessions, and operations.

## See it in use

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

## Quick start

**Prerequisites:** Docker Desktop with Docker Compose, Python 3.10+, and an
OpenAI and/or Gemini API key. Local retrieval models are optional and download
only when enabled.

```bash
git clone https://github.com/vrraj/aina-veris.git
cd aina-veris
bash scripts/rag_setup.sh
```

The setup script creates `.env`, starts Qdrant and the app, creates the local
virtual environment, and seeds sample data. Add at least one provider key to
`.env`, then open [http://localhost:8100](http://localhost:8100).

```dotenv
OPENAI_API_KEY=your_openai_api_key
# or
GEMINI_API_KEY=your_gemini_api_key
```

## Interfaces and documentation

| Interface | Start here |
|---|---|
| **Web UI and REST / OpenAPI** | [Generated OpenAPI documentation](http://localhost:8100/docs) |
| **Embeddable chat** | [Embedded chat guide](docs/embedded-chat.md) |
| **A2A research agents** | [A2A integration guide](README_A2A.md) |
| **MCP tools and registry** | [MCP specification](docs/mcp_specs.md) · [Tool registry guide](docs/tool_registry.md) |
| **Retrieval tuning** | [Retrieval evaluation guide](docs/retrieval-evals.md) · [Compound queries](docs/compound-queries.md) |
| **Operations and deployment** | [Development](docs/development.md) · [Troubleshooting](docs/troubleshooting.md) · [Architecture](docs/architecture.md) |

> Aina-Veris is a reference architecture for tool-assisted RAG. Domain
> isolation and Origin/Host allowlists are useful deployment controls, but a
> public deployment still needs appropriate authentication and authorization.

## Operations and verification

```bash
# Application lifecycle
make start          # start Qdrant and the web application
make rebuild        # rebuild the image and restart after dependency changes
make stop           # stop the application stack
make start-debug    # run FastAPI locally with reload

# Qdrant operations
make qdrant-status
make qdrant-collections
make qdrant-logs

# Seed and validate the local environment
make seed
make smoke-api

# MCP server contracts and interactive inspection
.venv/bin/python -m pytest tests/mcp/test_research_server.py
npx @modelcontextprotocol/inspector http://localhost:8100/mcp
```

## License

Aina-Veris is available under the [GNU GPLv3](LICENSE).

Commercial licensing is available for organizations that need proprietary product integration or custom deployments: `ai-musings99@gmail.com`.
