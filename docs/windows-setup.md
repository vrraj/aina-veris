# Windows setup

[← Documentation home](index.md)

Use this guide to run Aina-Veris on Windows without installing Python, Make, or
WSL.

## Before you start

- Install and start [Docker Desktop](https://www.docker.com/products/docker-desktop/).
- Install [Git for Windows](https://git-scm.com/download/win).
- Have an OpenAI or Gemini API key ready. Aina-Veris cannot start without one.

## Install

Open PowerShell, then clone the repository:

```powershell
git clone https://github.com/vrraj/aina-veris.git
cd aina-veris
```

Run the installer:

```powershell
.\scripts\rag_windows_setup.bat
```

On its first run, the installer copies `.env.example` to `.env`, securely
prompts for an OpenAI or Gemini key, starts Docker services, and seeds the
bundled reference data. It opens Aina-Veris at
[http://localhost:8100](http://localhost:8100) when finished.

The `.env` file contains your key and is intentionally untracked. Do not share
or commit it.

## Run it again

To start an existing installation after Docker Desktop is running:

```powershell
docker compose up -d
```

To stop it:

```powershell
docker compose down
```

Run `rag_windows_setup.bat` again only if you want it to check the setup and
seed collections that are missing. Existing collections are never overwritten
by the Windows installer.

## Existing Qdrant instance

Before running the installer, set `DOCKER_QDRANT_HOST` and
`DOCKER_QDRANT_PORT` in `.env` to a Qdrant server reachable from the container.
For a Qdrant service on the Windows host, use `host.docker.internal` and port
`6335`.
