# API Surfaces

[← Documentation home](index.md)

The running service publishes its complete OpenAPI contract at
[`/docs`](http://localhost:8100/docs). The endpoints below are the stable entry
points for applications.

| Capability | Endpoint | Notes |
|---|---|---|
| Stateless chat | `POST /chat` | Message, optional history, and runtime parameters. |
| Session chat | `POST /chat/session`, `POST /chat/{session_id}` | Server-managed in-memory history. |
| Search | `POST /search` | Direct retrieval without answer generation. |
| Ingestion | `POST /index`, `/pdf`, `/mediawiki/url`, `/embed` | Source-specific ingestion into a selected domain. |
| Retrieval evaluation | `POST /retrieval-evals/run` | Inspect retrieval and reranking independently. |
| Stage stream | `GET /chat/stream/stages?query_id=...` | SSE progress, metrics, and keepalives. |
| A2A | `/agents/<agent-name>/` | Fixed-domain JSON-RPC research tasks. |
| MCP | `/mcp` | Streamable HTTP research tools. |

REST and browser callers supply `active_domain` where supported. A2A and MCP
research tools use the domain fixed by their server-side agent definition.

The supplied session store is in-memory. Applications that need durable history
should own that state or replace the store.
