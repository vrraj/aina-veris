# AINA Veris Technical Architecture

Brief reference for the technologies, model layers, and primary modules in
AINA Veris. See [README.md](README.md) for product/setup documentation,
[docs/architecture.md](docs/architecture.md) for the detailed system design,
and [README_A2A.md](README_A2A.md) for the A2A consumer contract.

## Application and API

| Category | Technology | Role |
| --- | --- | --- |
| Runtime | Python | Backend, retrieval, ingestion, integrations, and orchestration |
| Web framework | FastAPI | REST API, static UI hosting, and A2A route host |
| ASGI server | Uvicorn | Runs the FastAPI service (`webapp`) on port `8100` |
| Frontend | Static HTML and Vanilla JavaScript ES modules | Browser chat, search, ingestion, and admin interfaces |
| Real-time updates | Server-Sent Events (SSE) via `sse-starlette` | Streams chat pipeline stages through `/chat/stream/stages` |

Veris currently uses SSE for server-to-browser progress updates; it does not
use WebSockets for this flow.

## RAG and data layer

| Category | Technology / module | Role |
| --- | --- | --- |
| Vector store | Qdrant | Stores document vectors and metadata; runs as a separate service |
| Domain routing | `backend/core/config.py`, `backend/retrieval/` | Maps an active domain to collection, embedding model, and retrieval settings |
| Retrieval orchestration | `backend/retrieval/orchestration.py`, `retrieval_eval_service.py` | Dense, sparse, or hybrid retrieval; compound-query handling and coverage |
| Reranking | ColBERT late interaction and cross-encoder paths | Optional retrieval quality improvements |
| Ingestion | `backend/extractor/`, `backend/crawler/`, `backend/embeddings/` | Extracts PDFs/HTML/MediaWiki, chunks content, embeds it, and indexes it |

Domains can use isolated Qdrant collections and different embedding/vector
settings. The current deployment uses one Qdrant service, with collection
selection performed per request.

## Models and inference

| Layer | Technology / configuration | Role |
| --- | --- | --- |
| Provider abstraction | `vrraj-llm-adapter` | Selects models and providers by pipeline stage; tracks tokens and cost |
| Hosted LLMs | OpenAI and Google Gemini | Query rewriting, planning, answer synthesis, and tool use as configured |
| Hosted embeddings | OpenAI and Gemini embeddings | Embedding generation for configured hosted domains |
| Local embeddings | FastEmbed / BGE-M3-compatible configuration | Local dense embeddings for configured domains |
| Local ranking | ColBERT and cross-encoder models | Late interaction and pairwise reranking where enabled |
| Model configuration | Prompt/model registries in `prompts/` | Keeps model and prompt selection outside application logic |

The configured model is stage-specific, not global: retrieval embeddings,
reranking, and final answer generation can use different models.

## Agent and integration protocols

| Category | Technology / module | Role |
| --- | --- | --- |
| A2A | `a2a-sdk[http-server]`, `backend/a2a/` | Publishes fixed-domain Veris research agents via A2A protocol 1.0 and JSON-RPC |
| A2A discovery | AgentCard | Advertises agent identity, text modes, skills, and authoritative task URL |
| A2A execution | `executor.py`, `service.py` | Converts an A2A task into a bounded, stateless Veris RAG request |
| MCP | `backend/integrations/mcp/` | Discovers and invokes external Model Context Protocol tools over JSON-RPC |
| HTTP client | HTTPX | Outbound HTTP transport, including consuming remote A2A agents |
| Web research | Configured MCP tools, including Tavily when enabled | Optional external research capability under Veris server policy |

A2A is Veris's application-to-application research boundary. MCP is its
tool-to-tool integration boundary; they serve different purposes.

## Core modules

| Module | Responsibility |
| --- | --- |
| `backend/main.py` | FastAPI application composition and route registration |
| `backend/chat/` | Stateless chat/RAG orchestration and pipeline stages |
| `backend/retrieval/` | Domain-aware retrieval, fusion, reranking, and evaluation |
| `backend/embeddings/` | Embedding providers, collection management, and indexing support |
| `backend/db/` | Qdrant access layer |
| `backend/a2a/` | Domain-scoped A2A agents, cards, routes, task execution, and isolation |
| `backend/integrations/mcp/` | MCP transport, discovery, execution, and provider-result adapters |
| `backend/tools/` | Local and MCP-backed tool execution |
| `backend/stream_*.py` | SSE event registry and stage emission |
| `backend/extractor/` and `backend/crawler/` | Source acquisition, parsing, and document preparation |

## Deployment

| Service | Technology | Port |
| --- | --- | --- |
| `webapp` | Uvicorn + FastAPI | `8100` |
| `qdrant` | Qdrant | `6333` internal REST; `6335` host REST |
| Container orchestration | Docker Compose | Runs the application and Qdrant services |
