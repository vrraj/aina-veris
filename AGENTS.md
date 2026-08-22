# AGENTS.md - Project Standards & System Instructions

## 1. Environment & Infrastructure
- **App Service:** `webapp` (Container: `uvicorn-app`) runs on **port 8100**.
- **Vector DB Service:** `qdrant` (Container: `qdrant-server`) runs on **port 6333** (internal REST), **port 6334** (internal gRPC), **port 6335** (external REST), and **port 6336** (external gRPC).
- **Internal Networking:** The App connects to Qdrant via `qdrant:6333`.
- **Database Access:** To query Qdrant manually from the host terminal, use:
  `curl http://localhost:6335/collections` or access the Qdrant dashboard at `http://localhost:6335/dashboard`
- **Persistent Storage:** Qdrant data persists in `./qdrant_storage`, app logs in `./logs`.
- **Development Mode:** Uses `run.py` with hot reload. Production uses `start.py`.
- **Config Source:** Refer to `docker-compose.yml` and `.env` for environment variables. Never hardcode secrets.
- **Virtual Environment:** This project relies on a virtual environment located at `.venv`.
- **Dependencies:** All imports and package resolutions must be interpreted relative to the `.venv` directory. Please ensure that any pathing, linting, or code analysis tasks are configured to acknowledge this environment as the primary source for project dependencies.

## 2. Backend Architecture (FastAPI/Python)
- **Lean Routes:** DO NOT place business logic inside `app/main.py` or any `routes.py`. Routes are for transport only.
- **Modularity:** Logic must be partitioned into `/services`, `/crud`, or `/utils`.
- **Service Framework:** Build tools as decoupled services. Every service must be callable via:
  1. A **Language Model prompt** (Agentic tool use)
  2. A standard **REST API endpoint**

## 3. Frontend Architecture (Vanilla JS & Static HTML)
- **Tech Stack:** Vanilla JS (ES Modules) and static HTML files. No frontend frameworks.
- **app.js:** Reserved ONLY for global/common functions.
- **Page-Specific JS:** logic for a particular page must live in its own JS file (e.g., `chat.js`) and be included only in that page's HTML.

## 4. Operation & Safety Protocols
- **DRY (Don't Repeat Yourself):** Strictly avoid duplicating logic. Refactor shared code into `/utils` or `/services` to prevent code drift.
- **Confirmation Gates:** For any task affecting multiple files or changing **function signatures**, you MUST:
  1. Propose the architectural plan first.
  2. Wait for explicit user approval before writing code.
- **Context Awareness:** Always check `app/main.py` or routes to verify the API responses and data structures before editing HTML/JS.
- **Exception Handling:** Don't catch exceptions when we don't expect them to be normally raised. Let the code fail when something unexpected happens so that the problem can be fixed instead of silently misbehaving.

## 5. Real-Time Logic
- **State Management:** SSE uses queue-based system via `stream_registry` and `stream_emit.py` for enqueuing stage events.
- **Reliability:** Implement heartbeats and reconnection logic in SSE client implementations using EventSource API.

## 6. Final Response format
- **Direct Execution**: Provide code logic immediately. No conversational filler or "step-by-step" walk-throughs unless requested.
- **Minimalist Summaries**: After any fix (FastAPI routes, html, DB queries), summarize in less than  3 bullets unless more explanation is necessary.
