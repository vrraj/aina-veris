# Aina-Veris Contributor Guide

This file defines repository-level working conventions for human contributors
and coding agents. It is intentionally tracked: do not put credentials,
personal paths, or machine-specific configuration here.

## Environment

- The FastAPI service is `webapp` (`uvicorn-app`) on port `8100`.
- Qdrant is `qdrant` (`qdrant-server`): use `qdrant:6333` from the app
  container and `http://localhost:6335` from the host.
- Qdrant storage is `./qdrant_storage`; application logs are in `./logs`.
- Use the repository `.venv` for all Python imports, tests, and tooling.
- Read `docker-compose.yml`, `.env.example`, and the local untracked `.env`
  for configuration. Never hardcode or commit secrets.
- `run.py` is the hot-reload development entry point; `start.py` is the
  production entry point. See `docs/development.md` for setup details.

## Architecture

- Keep FastAPI routes transport-focused. Put reusable business logic in the
  appropriate service, CRUD, or utility module rather than `backend/main.py`
  or route modules.
- A capability intended for agent use should have a clear service boundary and
  an appropriate REST surface; do not duplicate the underlying logic.
- The frontend is vanilla ES modules and static HTML. Keep `app.js` for shared
  behavior only; page-specific behavior belongs in that page's JavaScript file.
- Preserve the established SSE queue flow (`stream_registry` and
  `stream_emit.py`). SSE clients need heartbeat and reconnection behavior.

## Change Discipline

- Inspect the relevant route, request/response model, and existing tests before
  changing API-connected frontend code.
- Preserve unrelated working-tree changes. Do not stage, revert, or reformat
  files outside the requested scope.
- Prefer small, focused changes and shared helpers over duplicated logic.
- Let unexpected failures surface; catch exceptions only when the code can
  handle them deliberately and safely.
- Propose a brief plan and wait for approval before API-contract changes,
  database/data migrations, destructive operations, or broad cross-cutting
  refactors. Small, scoped fixes and documentation updates do not need a gate.

## Validation and Security

- Run the most relevant tests; run `.venv/bin/python -m pytest` for broad
  backend changes when practical.
- Keep CI and Secret Scan passing. Do not weaken secret detection or commit
  `.env`, tokens, private keys, generated logs, or Qdrant data.
- Treat public deployment as a separate concern from publishing this source:
  REST, A2A, MCP, ingestion, and SSE require deployment-specific
  authentication, authorization, rate limits, and audit logging. Follow
  `SECURITY.md`.
- Update documentation when a public API, configuration, operational workflow,
  or integration behavior changes.

## Handoff

- Report the outcome directly, list material files changed, and state the
  validation performed.
