# A2A Integration — AINA Veris ↔ AINA Markets

## Current architecture

Veris hosts two domain-scoped A2A protocol 1.0 agents through the existing
FastAPI service on port 8100. Both use JSON-RPC 2.0 and return a completed A2A
Task with one Markdown research artifact.

| Veris agent | Fixed retrieval domain | AgentCard | Task endpoint |
| --- | --- | --- | --- |
| `veris-mountains-research-agent` | `mountains` | `/agents/veris-mountains-research-agent/.well-known/agent-card.json` | `/agents/veris-mountains-research-agent/` |
| `veris-finance-research-agent` | `finance` | `/agents/veris-finance-research-agent/.well-known/agent-card.json` | `/agents/veris-finance-research-agent/` |

The generic `veris-research-agent` card has been replaced. Every consuming
application selects a scoped Veris agent; it cannot override that agent's
retrieval domain in an A2A request.

## Markets → Veris (implemented)

Markets exposes the direct LLM tool:

```text
call_veris_finance_research_agent(prompt)
```

Its existing `tool_registry.yaml` entry supplies the bootstrap endpoint:

```yaml
agent_owner: veris-finance-research-agent
protocol: A2A_JSONRPC
base_url: http://host.docker.internal:8100
path: /agents/veris-finance-research-agent/
```

The Markets A2A client combines `base_url` and `path`, fetches the AgentCard
at `<agent endpoint>/.well-known/agent-card.json`, validates the advertised
agent name against `agent_owner`, and then sends the text prompt to the
AgentCard's JSON-RPC interface.

Markets uses its LLM only to select the A2A tool. After selection, it replaces
the LLM-generated tool argument with the original user prompt before sending
the task to Veris. This preserves instructions such as a requested timeframe,
source preference, output format, or `Use Tavily web search if needed`.
Markets must not paraphrase or otherwise reinterpret a natural-language A2A
research request; Veris is the agent responsible for that research work.

The client requires:

- matching agent identity;
- JSON-RPC transport and text input/output support;
- `TASK_STATE_COMPLETED`;
- exactly one Markdown text artifact;
- source metadata or Markdown URLs when Veris supplies them.

Veris returns structured `sources` in the artifact metadata and keeps the
Markdown response intact for Markets synthesis. Sources are optional for a
successful A2A task: a completed Markdown artifact without sources is returned
to Markets with `sources: []`, rather than being converted to a system error.

When the original request explicitly asks for web research, Veris's enabled
Tavily MCP tools are available to the Finance Research Agent. The Veris model
decides whether to invoke them; Markets' responsibility is to preserve that
instruction in the delegated prompt.

## Domain ownership

Browser chat and A2A are intentionally separate:

- Browser chat may send `active_domain` / `prompt_domain` per request.
- A2A callers send only a text prompt.
- Each Veris A2A executor supplies its own fixed domain to the existing chat
  pipeline as both `active_domain` and `prompt_domain`.

For example, a Markets call to the finance card always uses `finance`, even
while a browser session is using `mountains` or `backpacking`.

## Network configuration

`A2A_VERIS_PUBLIC_BASE_URL` is used only when Veris builds AgentCards. It
controls the absolute JSON-RPC endpoint advertised in
`supportedInterfaces[].url`; it does not control Veris's listening port.

For the local Docker Desktop setup, Veris advertises an address reachable from
the Markets API container:

```env
A2A_VERIS_PUBLIC_BASE_URL=http://host.docker.internal:8100
```

`host.docker.internal` is necessary because `localhost` inside the Markets
container refers to Markets itself, not the Mac host running Veris.

## Veris implementation

```text
backend/a2a/
  config.py       # two named agents and server-owned A2A limits
  agent_card.py   # scoped AgentCard construction
  router.py       # routes/cards for both agents
  executor.py     # one executor per scoped agent
  service.py      # isolated call into the existing RAG pipeline
```

Inbound A2A requests have no browser history, caller-controlled domain, or
caller-controlled retrieval policy. Server-owned limits include prompt size,
timeout, concurrency, retrieval result count, output tokens, and tool-call
count.

## Verification completed

- Veris A2A tests verify both AgentCards, JSON-RPC task routes, Markdown
  artifact construction, source metadata, guardrails, and finance domain
  injection.
- Markets tests verify AgentCard/result validation, optional-source handling,
  verbatim A2A prompt delegation, the finance tool adapter, direct-tool
  registry resolution, and mocked tool selection/synthesis.
- A live Markets → Veris call was verified with a sourced research response.

Useful checks after restarting Veris:

```bash
curl -s http://localhost:8100/agents/veris-mountains-research-agent/.well-known/agent-card.json | jq -r .name
curl -s http://localhost:8100/agents/veris-finance-research-agent/.well-known/agent-card.json | jq -r .name
```

## Pending work

1. Add a live finance-domain smoke test using finance-indexed content.
2. Add the Markets snapshot A2A agent (`markets-snapshot-agent`) and its
   stable market-data artifact contract.
3. Implement the reverse Veris → Markets typed client and LLM tool only after
   the Markets agent contract passes.
4. Add peer authentication before any non-local exposure.
5. Add production observability/readiness checks and explicit failure-path
   tests for peer unavailability, timeouts, and malformed artifacts.
