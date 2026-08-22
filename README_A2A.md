# AINA Veris A2A

AINA Veris exposes domain-scoped research agents through the Agent-to-Agent
(A2A) protocol. A consuming application discovers an agent through its
AgentCard, sends it a text research task over JSON-RPC, and receives a
completed Markdown research artifact.

This document is the integration contract for applications that consume Veris.
See [a2a.md](a2a.md) for the implementation history and the Markets-specific
integration notes.

## Veris: multi-domain RAG, published through A2A

Veris is a multi-domain RAG framework. A domain is a self-contained research
configuration: it has a named corpus, a Qdrant collection, an embedding-model
configuration, and retrieval/vector-storage settings. The chat and ingestion
paths route work by domain. Domains can use separate collections for retrieval
isolation, or deliberately share a collection where their corpus is shared.

A2A is the external, domain-safe interface to that framework. Rather than
exposing a generic agent with caller-controlled retrieval settings, Veris
publishes one named A2A research agent per domain. The agent's identity is
therefore both a capability declaration and a routing boundary:

```text
Veris multi-domain RAG framework
  ├─ mountains domain → mountains Qdrant collection → mountains A2A agent
  ├─ finance domain   → finance Qdrant collection   → finance A2A agent
  └─ future domain    → its Qdrant collection       → its A2A agent
```

The current implementation uses one Qdrant service with separate collections
per domain. It does not currently configure one Qdrant *database/service* per
domain. A deployment can isolate a domain further with a separate Qdrant
instance, but that would require explicit connection-routing configuration;
it is not implied merely by adding a domain or A2A agent.

## What Veris provides

Veris is a research provider. Each published agent answers free-text questions
using the agent's fixed Veris knowledge domain, the Veris retrieval and
synthesis pipeline, and any server-enabled research tools.

| Agent | Domain | AgentCard | Task endpoint |
| --- | --- | --- | --- |
| `veris-mountains-research-agent` | `mountains` | `/agents/veris-mountains-research-agent/.well-known/agent-card.json` | `/agents/veris-mountains-research-agent/` |
| `veris-finance-research-agent` | `finance` | `/agents/veris-finance-research-agent/.well-known/agent-card.json` | `/agents/veris-finance-research-agent/` |

Choose the agent that owns the domain required by the request. A caller sends
only the research prompt; it cannot select or override the retrieval domain.

## Adding a new domain-facing A2A agent

Extending Veris follows this sequence:

1. Define the domain in `prompts/domain_embedding_config.yaml`, including its
   unique `collection_name`, embedding model, and vector-storage settings.
2. Ingest that domain's content using the same domain so it is indexed into
   that collection.
3. Add a `VerisA2AAgent(name=..., domain=...)` entry to
   `backend/a2a/config.py`.
4. The shared AgentCard, router, executor, and service automatically publish
   the new agent with the fixed domain; no duplicate pipeline implementation is
   needed.
5. Add contract tests for the AgentCard, task endpoint, domain injection, and
   artifact shape before making the agent available to a consumer.

Give a domain a unique collection name when it needs retrieval isolation;
sharing a collection is an explicit choice for domains that use the same
corpus. The agent name should make the domain and capability obvious, following the existing
`veris-<domain>-research-agent` convention.

## A2A contract

Veris implements A2A protocol version `1.0` over JSON-RPC. Each AgentCard
advertises the authoritative task URL and these capabilities:

- text input and text output;
- non-streaming task execution;
- no push notifications;
- one completed research task per request.

### Input

Send one non-empty text prompt. Include all requirements in that prompt, such
as the question, timeframe, requested structure, preferred source types, and
whether web research is appropriate.

Do not send browser-chat history, a domain selector, internal retrieval
parameters, or tool policy. Those are owned by Veris.

### Successful output

A successful request returns a task in `TASK_STATE_COMPLETED` with exactly one
artifact. That artifact contains:

- one non-empty Markdown text part—the research answer;
- optional `sources` in artifact metadata.

Source metadata may be absent when no sources are available. Consumers should
accept this as a successful result and may extract URLs from the Markdown for
display purposes.

### Failure output

Do not treat a task that is not `TASK_STATE_COMPLETED`, an empty answer, or an
artifact that does not meet the preceding shape as usable research. Surface it
as an integration failure, preserving the task identifier and status for logs.

## How Veris processes a task

```text
Consumer
  → discovers AgentCard
  → sends A2A JSON-RPC text task
Veris A2A executor
  → applies server-owned A2A limits
  → fixes the agent's retrieval domain
  → invokes the stateless Veris research pipeline
  → returns one Markdown artifact and optional sources
Consumer
  → validates the task and artifact contract
  → renders or synthesizes the research result
```

The A2A boundary is deliberately isolated from browser chat. An A2A task has
no browser conversation history or caller-controlled session, domain, search
mode, retrieval count, tool policy, or generation limits. Veris injects the
agent's domain as both `active_domain` and `prompt_domain` before invoking its
existing pipeline.

## Server implementation

The integration lives in `backend/a2a/` and is registered in the existing
FastAPI application on port `8100`.

| Module | Responsibility |
| --- | --- |
| `config.py` | Agent identities, fixed domains, public URLs, and bounded server limits |
| `agent_card.py` | Builds the public A2A AgentCard for each agent |
| `router.py` | Registers AgentCard discovery and JSON-RPC routes using the A2A SDK |
| `executor.py` | Validates, limits, executes, times out, and completes A2A tasks |
| `service.py` | Adapts a stateless A2A task to Veris's existing chat/RAG pipeline |

## Configuration and networking

Set `A2A_VERIS_PUBLIC_BASE_URL` to the address from which the consuming
application can reach Veris. This setting only determines the absolute task
URL advertised in AgentCards; it does not change the FastAPI listening port.

For a local consumer running in Docker Desktop while Veris runs on the host:

```env
A2A_VERIS_PUBLIC_BASE_URL=http://host.docker.internal:8100
```

Available server-owned controls are `A2A_VERIS_MAX_PROMPT_CHARS`,
`A2A_VERIS_TIMEOUT_SECONDS`, `A2A_VERIS_MAX_CONCURRENT_REQUESTS`,
`A2A_VERIS_MAX_RETRIEVAL_RESULTS`, `A2A_VERIS_MAX_OUTPUT_TOKENS`,
`A2A_VERIS_MAX_TOOL_CALLS`, and `A2A_VERIS_ENABLE_TOOLS`.

## Consumer implementation guidelines

1. Discover the AgentCard from the configured agent endpoint; do not duplicate
   its protocol details in client code.
2. Validate the AgentCard's expected identity, text input/output modes, and
   JSON-RPC interface before submitting work.
3. Delegate the original user prompt verbatim. If an LLM selects the A2A tool,
   use the LLM only for selection and replace its generated prompt argument
   with the original request.
4. Validate `TASK_STATE_COMPLETED`, exactly one artifact, and exactly one
   non-empty Markdown text part before consuming the answer.
5. Keep the Markdown unchanged when passing it to presentation or downstream
   synthesis; preserve structured sources when Veris includes them.
6. Use a client timeout longer than Veris's configured execution deadline.
7. Do not expose a non-local Veris agent until peer authentication, transport
   security, readiness checks, and failure-path observability are in place.

## Calling Veris from Python

The following is the production pattern used by AINA Markets. It uses the A2A
Python SDK to resolve the AgentCard, submit a user text message, and obtain the
final task.

```python
import asyncio

import httpx
from a2a.client.client import ClientConfig
from a2a.client.client_factory import create_client
from a2a.helpers.proto_helpers import new_text_message
from a2a.types.a2a_pb2 import Role, SendMessageRequest, TaskState


async def research(prompt: str) -> str:
    endpoint = "http://host.docker.internal:8100/agents/veris-finance-research-agent/"
    expected_name = "veris-finance-research-agent"

    async with httpx.AsyncClient(timeout=100.0) as http_client:
        client = await create_client(
            endpoint,
            client_config=ClientConfig(streaming=False, httpx_client=http_client),
            resolver_http_kwargs={"timeout": 100.0},
        )
        try:
            if client._card.name != expected_name:
                raise RuntimeError("Unexpected Veris agent identity")

            request = SendMessageRequest(
                message=new_text_message(prompt, role=Role.ROLE_USER),
            )
            task = None
            async for response in client.send_message(request):
                if response.HasField("task"):
                    task = response.task

            if task is None or task.status.state != TaskState.TASK_STATE_COMPLETED:
                raise RuntimeError("Veris research did not complete")
            if len(task.artifacts) != 1:
                raise RuntimeError("Unexpected Veris artifact count")

            parts = [
                part.text.strip()
                for part in task.artifacts[0].parts
                if part.HasField("text") and part.text.strip()
            ]
            if len(parts) != 1:
                raise RuntimeError("Expected one Markdown research artifact")
            return parts[0]
        finally:
            await client.close()


answer_markdown = asyncio.run(research("Research the company's latest earnings outlook."))
```

AINA Markets wraps this pattern in
`app/integrations/a2a/veris_client.py`, which also validates the AgentCard
capabilities and reads optional source metadata.

## Verify an agent is available

After Veris starts, inspect the published cards:

```bash
curl -s http://localhost:8100/agents/veris-mountains-research-agent/.well-known/agent-card.json | jq -r .name
curl -s http://localhost:8100/agents/veris-finance-research-agent/.well-known/agent-card.json | jq -r .name
```

## Current scope and next steps

The implemented direction is Markets → Veris research. A reverse Veris →
Markets integration will be added only after Markets publishes a stable,
typed market-data artifact contract. Peer authentication, production
observability, readiness checks, and explicit unavailable-peer/malformed-task
tests are required before non-local exposure.
