# Troubleshooting

[← Documentation home](index.md)

## The service does not start

```bash
docker compose ps
docker compose logs webapp
docker compose logs qdrant
```

Confirm that `.env` contains at least one configured model-provider key for
generation and that the service can reach Qdrant at its Docker-network host.

## No retrieval results

1. Check the collection at `http://localhost:6335/collections`.
2. Confirm the request's `active_domain` maps to the intended collection in
   `prompts/domain_embedding_config.yaml`.
3. Verify that the corpus was indexed with the same domain configuration.
4. Use retrieval evaluation before changing answer-generation prompts.

## A2A or MCP cannot discover a capability

Confirm the configured agent name and restart the service after editing
`backend/a2a/config.py`. Verify the AgentCard endpoint:

```text
/agents/<agent-name>/.well-known/agent-card.json
```

For MCP, inspect `http://localhost:8100/mcp` with the MCP Inspector and check
enabled MCP servers and credentials in `prompts/tool_registry.yaml` and `.env`.

## Local checks

```bash
.venv/bin/python -m pytest tests/mcp/test_research_server.py
.venv/bin/python -m pytest tests/a2a/test_veris_agent.py
.venv/bin/python -m pytest tests/test_chat_context.py
```

For public deployment problems, review [Security](security.md) and
[Deployment](deployment.md) before exposing an endpoint.
