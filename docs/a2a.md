# A2A Research Agents

[← Documentation home](index.md)

Aina-Veris publishes domain-scoped research capabilities through the
Agent-to-Agent (A2A) protocol. An external system discovers an AgentCard, sends
a JSON-RPC research task, and receives a grounded Markdown research artifact
with source metadata.

## Agent endpoints

For an agent named `<agent-name>`:

```text
GET  /agents/<agent-name>/.well-known/agent-card.json
POST /agents/<agent-name>/
```

Set `A2A_VERIS_PUBLIC_BASE_URL` to the public base URL used in generated cards.

## Add an agent-backed domain

1. Declare the domain, collection, embedding configuration, and retrieval mode
   in `prompts/domain_embedding_config.yaml`.
2. Optionally add domain-stage prompt overrides in `prompts/prompt_registry.yaml`.
3. Ingest the corpus into the configured domain.
4. Add a `VerisA2AAgent` entry to `VERIS_A2A_AGENTS` in
   `backend/a2a/config.py` with a stable `name`, fixed `domain`, description,
   and capability IDs.
5. Restart the service.

The definition creates the AgentCard, task endpoint, and corresponding MCP
research tool at startup. A caller supplies a research question; it does not
choose the agent's domain.

## Example

The Semiconductor Research Agent uses `semiconductor_memory` as its fixed
domain. A caller can ask a technical datasheet question; Aina-Veris applies the
configured retrieval, reranking, prompt, tool, and output policy before
returning grounded research.

For request shapes and integration examples, see the
[A2A integration guide](https://github.com/vrraj/aina-veris/blob/main/README_A2A.md).
