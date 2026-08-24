# MCP Integration

[← Documentation home](index.md)

Aina-Veris uses MCP in both directions:

- **MCP client:** discovers and invokes configured external servers during
  research, including Tavily web search.
- **MCP server:** exposes fixed-domain Aina-Veris research tools over Streamable
  HTTP at `/mcp` and through the stdio server.

## Exposed research tools

Configured A2A agents automatically create matching MCP research tools. The
current examples are `research_mountains`, `research_finance`, and
`research_semiconductor`. Each applies its server-owned domain and limits; a
caller cannot provide an arbitrary domain.

Run the stdio server with:

```bash
python -m backend.integrations.mcp.server
```

For an MCP host configuration, use the repository's virtual environment and
set the working directory to the repository root:

```json
{
  "mcpServers": {
    "aina-veris": {
      "command": "/Users/raj/Documents/Raj/aina-veris/.venv/bin/python",
      "args": ["-m", "backend.integrations.mcp.server"],
      "cwd": "/Users/raj/Documents/Raj/aina-veris"
    }
  }
}
```

For HTTP inspection:

```bash
npx @modelcontextprotocol/inspector http://localhost:8100/mcp
```

## External MCP servers

Declare enabled servers in `prompts/tool_registry.yaml`. The Tavily integration
uses Streamable HTTP and reads `TAVILY_API_KEY` from the environment. Secrets
belong in `.env` or a deployment secret manager, never in the registry.

At startup Aina-Veris discovers tools from enabled servers and combines them
with configured local tools. Tool results are normalized before compact evidence
and source metadata enter the research pipeline.

## Deployment security

`/mcp` is unauthenticated in the reference framework. A public MCP endpoint
requires an authentication and authorization layer, HTTPS, rate limits, audit
logging, and appropriate Origin validation. See [Security](security.md).
