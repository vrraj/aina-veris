# Development

[← Documentation home](index.md)

## Local prerequisites

- Docker Desktop with Docker Compose
- Python 3.10 or later for local tests and scripts
- An OpenAI and/or Gemini API key in an untracked `.env`

## Run the stack

```bash
docker compose up -d --build
docker compose ps
```

The application is available at `http://localhost:8100`; Qdrant is available
from the host at `http://localhost:6335`.

For local Python tests or scripts:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest tests/mcp/test_research_server.py
```

The Docker container supplies its own Python environment, so `.venv` is not
required for a Docker-only run.

## Quality checks

The maintained test suite is under `tests/`. Useful focused checks are:

```bash
.venv/bin/python -m pytest tests/test_chat_context.py
.venv/bin/python -m pytest tests/mcp/test_research_server.py
.venv/bin/python -m pytest tests/a2a/test_veris_agent.py
gitleaks detect --source . --no-git --redact
```

Do not commit `.env`, `.venv`, Qdrant storage, logs, model caches, or provider
credentials.
