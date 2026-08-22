# System Architecture

This document provides a top-down view of the entire system architecture, including all major components, data flows, and integration points.

## High-Level Overview

The system is a **Tool-Assisted Retrieval-Augmented Generation (RAG) framework** that orchestrates multiple pipeline stages to generate grounded, cited responses from structured and unstructured knowledge sources.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Client Layer                                   │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │  Chat UI     │  │  Search UI   │  │  Ingestion   │  │  Embedded    │   │
│  │  (chat.html) │  │ (search.html)│  │   UI         │  │  Widget      │   │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘   │
│         │                  │                  │                  │          │
│         └──────────────────┴──────────────────┴──────────────────┘          │
│                              │                                               │
└──────────────────────────────┼───────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            API Layer (FastAPI)                               │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  • POST /chat                  • POST /chat/{session_id}            │  │
│  │  • POST /search                • POST /ingest/batch                 │  │
│  │  • GET  /api/ui/runtime-context• SSE /chat/stream/stages            │  │
│  │  • GET  /domain-embedding-config • Tool endpoints                  │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────┬───────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Chat Orchestration Layer                              │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  ChatManager (backend/chat/chat_manager.py)                         │  │
│  │  • Session management  • Pipeline orchestration                      │  │
│  │  • Context assembly   • Tool coordination                            │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                              │                                               │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  Pipeline Stages (backend/chat/pipeline/stages/)                     │  │
│  │  1. Turn Resolution (includes Query Rewrite)                         │  │
│  │  2. Retrieval         3. ColBERT Late Interaction (optional)         │  │
│  │  4. Cross-Encoder Rerank (optional)  5. Context Assembly             │  │
│  │  6. LLM Inference     7. Tool Execution                              │  │
│  │  8. Final Response    9. Post-Processing                            │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────┬───────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Tool & Integration Layer                             │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  Tool Registry (prompts/tool_registry.yaml)                         │  │
│  │  • Local tools (web search, weather, airports)                      │  │
│  │  • MCP tools (external tool servers)                                │  │
│  │  • Artifact configuration (SVG, images)                              │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                              │                                               │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  Tool Executors (backend/tools/)                                    │  │
│  │  • Local tool executors    • MCP tool executor                      │  │
│  │  • Artifact synthesis      • Result normalization                     │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                              │                                               │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  MCP Client (backend/integrations/mcp/)                              │  │
│  │  • MCP Adapter (HTTP JSON-RPC)                                       │  │
│  │  • Tool discovery & runtime merging                                  │  │
│  │  • Server-level result adapters & normalized sources/artifacts       │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────┬───────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Retrieval & Storage Layer                             │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  RetrievalEvalService (backend/retrieval/)                          │  │
│  │  • Domain-aware collection routing                                   │  │
│  │  • Embedding model selection (hosted/local)                          │  │
│  │  • ColBERT late interaction (optional)                               │  │
│  │  • Cross-encoder reranking (optional)                               │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                              │                                               │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  Qdrant Vector Database (qdrant-server)                              │  │
│  │  • Dense vectors (OpenAI, Gemini, BGE-M3)                           │  │
│  │  • Sparse vectors (lexical)                                          │  │
│  │  • Late interaction vectors (ColBERT)                                │  │
│  │  • Multi-collection support                                         │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────┬───────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Ingestion Pipeline Layer                             │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  Source Parsers (backend/ingestion/parsers/)                        │  │
│  │  • PDF parser    • MediaWiki parser  • HTML parser                   │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                              │                                               │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  Chunking & Metadata (backend/ingestion/)                            │  │
│  │  • Smart chunking    • Structure preservation                         │  │
│  │  • Metadata augmentation    • Noise filtering                        │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                              │                                               │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  Embedding Service (backend/retrieval/)                              │  │
│  │  • Hosted embeddings (OpenAI, Gemini)                               │  │
│  │  • Local embeddings (BGE-M3 via FastEmbed)                           │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────┬───────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         LLM Provider Layer                                   │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  vrraj-llm-adapter                                                   │  │
│  │  • OpenAI integration  • Gemini integration                           │  │
│  │  • Model registry     • Cost tracking                                 │  │
│  │  • Stage-specific model selection                                     │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                              │                                               │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  External LLM APIs                                                   │  │
│  │  • OpenAI API (GPT-4, GPT-4o, etc.)                                   │  │
│  │  • Google AI API (Gemini Pro, etc.)                                  │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Component Breakdown

### 1. Client Layer
- **Chat UI** (`frontend/chat.html`): Main chat interface with stateless history management
- **Search UI** (`frontend/search.html`): Direct search interface without LLM synthesis
- **Ingestion UI** (`frontend/`): Document upload, batch ingestion, and management
- **Embedded Widget** (`frontend/chat-embed.html`, `static/embed-loader.js`): Embeddable chat for external sites

### 2. API Layer (FastAPI)
- **Chat endpoints**: Stateless (`/chat`) and stateful (`/chat/{session_id}`) chat
- **Search endpoint**: Direct retrieval without inference
- **Ingestion endpoints**: Batch ingestion, document management
- **UI runtime context**: Domain switching, active domain retrieval
- **SSE streaming**: Real-time pipeline stage updates via `/chat/stream/stages`

### 3. Chat Orchestration Layer
- **ChatManager**: Central orchestrator that coordinates all pipeline stages
- **Pipeline Stages**: Deterministic sequence of processing steps
  - Turn Resolution: Detects follow-ups, rewrites ambiguous queries, handles clarification
  - Retrieval: Vector search via Qdrant with optional [compound query expansion](docs/compound-queries.md)
  - ColBERT Late Interaction: Token-level rescoring (optional)
  - Cross-Encoder Rerank: Local reranking (optional)
  - Context Assembly: Builds prompts from retrieved context
  - LLM Inference: Generates responses
  - Tool Execution: Calls local and MCP tools
  - Final Response: Formats output with citations
  - Post-Processing: Markdown to HTML conversion

### 4. Tool & Integration Layer
- **Tool Registry** (`prompts/tool_registry.yaml`): Central configuration for all tools
  - Local tools: Web search, weather, airports, stock price history
  - MCP tools: External tool servers (e.g., Agis Markets)
  - Artifact configuration: SVG, images, custom artifacts
- **Tool Executors** (`backend/tools/`): Execution logic for each tool type
  - Local executors: Direct function calls
  - MCP executor: Delegates to MCP client
  - Artifact synthesis: Generates SVGs, images from tool outputs
- **MCP Client** (`backend/integrations/mcp/`):
  - MCP Adapter: HTTP JSON-RPC communication with MCP servers
  - Tool discovery: Automatic discovery of tools from MCP servers
  - Runtime merging: Combines discovered tools with registry definitions
  - Result adapters (`backend/integrations/mcp/adapters/`): Parse documented server payloads into `NormalizedToolResult(data, sources)` without coupling to mutable tool names
  - Artifact synthesis: Converts structured data to SVGs, etc.

### 5. Retrieval & Storage Layer
- **RetrievalEvalService**: Domain-aware retrieval orchestration
  - Collection routing based on active domain
  - Embedding model selection (hosted vs local)
  - Search mode selection (dense, sparse, hybrid)
  - [Compound query expansion](docs/compound-queries.md) for multi-faceted questions
  - ColBERT late interaction integration
  - Cross-encoder reranking
- **Qdrant Vector Database**: Multi-vector storage
  - Dense vectors: Semantic embeddings
  - Sparse vectors: Lexical representations
  - Late interaction vectors: ColBERT token grids
  - Multi-collection support for domain isolation

### 6. Ingestion Pipeline Layer
- **Source Parsers**: Extract content from various formats
  - PDF parser: Text extraction with structure preservation
  - MediaWiki parser: Wiki markup to structured text
  - HTML parser: Web content extraction
- **Chunking & Metadata**: Intelligent content segmentation
  - Smart chunking based on document structure
  - Metadata augmentation (source, type, domain)
  - Noise filtering (headers, footers, navigation)
- **Embedding Service**: Vector generation
  - Hosted embeddings: OpenAI, Gemini
  - Local embeddings: BGE-M3 via FastEmbed

### 7. LLM Provider Layer
- **vrraj-llm-adapter**: Provider abstraction layer
  - OpenAI integration: GPT models
  - Gemini integration: Google AI models
  - Model registry: Capabilities, pricing, policies
  - Cost tracking: Per-stage token accounting
- **External LLM APIs**: Actual model providers
  - OpenAI API: GPT-4, GPT-4o, etc.
  - Google AI API: Gemini Pro, etc.

## Data Flow

### Chat Request Flow
1. **Client** sends chat request with message, history, and parameters
2. **API Layer** validates request and passes to **ChatManager**
3. **ChatManager** orchestrates pipeline stages:
   - Turn Resolution: Detects follow-ups, rewrites queries, handles clarification
   - Retrieval: Searches Qdrant via RetrievalEvalService
   - ColBERT (optional): Token-level rescoring
   - Cross-Encoder (optional): Local reranking
   - Context Assembly: Builds prompt from retrieved context
   - LLM Inference: Generates response via vrraj-llm-adapter
   - Tool Execution: Calls tools if needed (local or MCP)
   - Final Response: Formats output with citations
   - Post-Processing: Converts Markdown to HTML
4. **API Layer** returns response to client
5. **SSE Stream** emits stage updates in real-time

### Ingestion Flow
1. **Client** uploads documents or provides URLs
2. **API Layer** validates and queues ingestion
3. **Ingestion Pipeline** processes each source:
   - Source Parser: Extracts content
   - Chunking: Segments into chunks
   - Metadata Augmentation: Adds metadata
   - Embedding Service: Generates vectors
   - Qdrant: Stores vectors and metadata
4. **API Layer** returns ingestion status

### MCP Tool Execution and Result Adapters
1. **Tool Registry** configures MCP server transport, authentication, and a stable `integration` identifier.
2. **MCP Client** discovers available tools through JSON-RPC. Tool names are used to invoke the remote server, not to select a payload parser.
3. **Result Adapter Dispatcher** selects an adapter from the server integration identifier. Each adapter validates and parses that provider's documented response schema.
4. **Normalized Tool Result** carries structured data and citation-ready source records; artifact rendering remains on the deterministic artifact pipeline:

   ```text
   MCP response → server adapter → data + sources
   ```

5. **Tool Synthesis** receives compact data plus `[tool-N]` source tokens. Raw SVG/image payloads are removed from LLM context.
6. **Final Response Stage** injects validated artifacts and appends cited tool URLs to `Sources:`. This works even when vector retrieval returns zero results.

Known adapters include Tavily (structured result URLs) and AGIS Markets (stock-history data rendered as SVG). Unknown integrations retain their plain MCP content rather than being heuristically parsed.

## A2A Agent-to-Agent Integration

Veris also exposes domain-scoped research capabilities through the A2A JSON-RPC protocol. This is a service boundary: an external application submits a research request, while Veris owns routing to the appropriate prompts, collections, and retrieval configuration.

### Published agents

| Agent | Identifier | Fixed domain | AgentCard | Task endpoint |
|---|---|---|---|---|
| Mountains Research Agent | `veris-mountains-research-agent` | `mountains` | `/agents/veris-mountains-research-agent/.well-known/agent-card.json` | `/agents/veris-mountains-research-agent/` |
| Finance Research Agent | `veris-finance-research-agent` | `finance` | `/agents/veris-finance-research-agent/.well-known/agent-card.json` | `/agents/veris-finance-research-agent/` |

`backend/a2a/config.py` defines this small, explicit set of published agents. `backend/a2a/router.py` registers an AgentCard route and an A2A task route for each one at application startup. The AgentCard is built by `backend/a2a/agent_card.py`; execution is handled by `backend/a2a/executor.py` and `backend/a2a/service.py`.

### Discovery and execution contract

The configured agent endpoint is a bootstrap address, not a separately maintained protocol URL. An A2A client derives the AgentCard address by appending `/.well-known/agent-card.json`, validates the card, and sends `SendMessage` to `supportedInterfaces[0].url` advertised by that card.

```text
Caller registry: base URL + agent path
        ↓
AgentCard discovery and validation
        ↓
AgentCard supportedInterfaces[0].url
        ↓
A2A SendMessage task request
        ↓
Scoped Veris research pipeline
        ↓
TASK_STATE_COMPLETED + one Markdown artifact + sources
```

The task executor supplies the agent's configured domain as both `active_domain` and `prompt_domain`. The remote caller can provide a research prompt, but cannot select a different Veris domain through task parameters. This keeps a Finance Research Agent request isolated from the Mountains domain even while an interactive browser session is using another active domain. A2A tasks are stateless and do not share browser chat history.

The result contract is deliberately compact: a completed A2A task contains one Markdown text artifact and source metadata. Consumers can render the Markdown and preserve the source URLs without depending on Veris's internal response shape.

### Reachable public task URL

`A2A_VERIS_PUBLIC_BASE_URL` controls the absolute task URL written into each AgentCard. It does not configure the FastAPI listener. It must be reachable from the A2A caller:

- A host client can use `http://localhost:8100`.
- A Dockerized sibling application on macOS can use `http://host.docker.internal:8100`.

This distinction matters because the caller follows the URL in `supportedInterfaces[0].url`; a card that advertises `localhost` is not reachable from another container.

### Current consumer and extension boundary

AINA Markets currently consumes the Finance Research Agent through its `call_veris_finance_research_agent` tool. Its registry supplies the bootstrap endpoint, and the Markets A2A client performs discovery, verifies the expected agent identity and artifact contract, then hands the result to Markets for synthesis.

The reverse Veris-to-Markets path remains a separate future integration. It should publish a Markets AgentCard and client contract rather than coupling either application's internal tool registry or chat state to the other.

## Configuration Files

- `prompts/tool_registry.yaml`: Tool definitions and artifact configuration
- `prompts/prompt_registry.yaml`: Prompt templates per stage and domain
- `prompts/domain_embedding_config.yaml`: Domain-to-embedding mappings
- `prompts/local_models_registry.yaml`: Local model configurations
- `.env`: Environment variables (API keys, MCP server URLs, A2A public task URL)
- `docker-compose.yml`: Container orchestration

## Key Design Principles

1. **Modularity**: Each layer is independently testable and replaceable
2. **Domain Isolation**: Separate collections, embeddings, and prompts per domain
3. **Stage-Specific Models**: Different LLMs per pipeline stage for cost optimization
4. **Artifact Support**: Rich media (SVGs, images) from tool outputs
5. **Observability**: Real-time streaming of pipeline stages and metrics
6. **Extensibility**: Easy to add new tools, parsers, embedding models, and LLM providers
