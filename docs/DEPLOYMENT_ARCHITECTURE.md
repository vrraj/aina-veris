# Deployment Architecture

How Aina Veris can run, what accelerates where, and which knobs control it.
The application is model-heavy (Docling layout/TableFormer for ingestion,
local dense + sparse embedders for hybrid domains), so the deployment mode
decides both GPU access and memory headroom.

## Deployment modes at a glance

| Mode | Command | GPU access | Memory story | When to use it |
| --- | --- | --- | --- | --- |
| Full Docker | `make start` | **None on macOS** — Docker Desktop's VM cannot see Metal | Shares the Docker Desktop VM (~8 GB default) with every other container | Production-parity runs, hands-off operation |
| Hybrid (native app + Docker Qdrant) | `make start-hybrid` / `make stop-hybrid` | **MPS + CoreML on Apple Silicon** | App uses full host RAM; no VM ceiling | macOS dev, GPU benchmarks, memory-bound ingestion |
| Linux + NVIDIA Docker | `docker compose --gpus` (toolkit required) | **CUDA** | Container sees host GPU; VM ceiling doesn't exist on native Linux | GPU server deployment |
| Fully native | `.venv/bin/python run.py` + a host-managed Qdrant | MPS + CoreML | Full host RAM | No Docker at all; you manage both processes |

## Starting and stopping

```bash
# CPU — full Docker (Linux, or macOS where the VM cannot see the GPU)
make start                 # docker compose up -d (webapp + qdrant)
make rebuild               # after code changes
make stop

# GPU on Apple Silicon — hybrid: native app (MPS/CoreML) + Docker Qdrant
make start-hybrid          # starts Docker, qdrant only, then run.py in .venv
make stop-hybrid           # stops qdrant + SIGTERMs uvicorn cleanly

# GPU on Linux + NVIDIA — same compose topology with the toolkit and a
# GPU image variant; see the CUDA section below.
docker compose up -d

# Development niceties (venv only)
make start-debug           # foreground uvicorn, auto-reload, debug logs
make stop-uvicorn          # SIGTERM anything on :8100
```

`.env` differences between the modes: the app is host-side in hybrid mode,
so it must use the published Qdrant port (`QDRANT_HOST=localhost`,
`QDRANT_PORT=6335`) — containerized `webapp` uses the compose-network
`qdrant:6333` automatically via `DOCKER_QDRANT_HOST/PORT` defaults.

## Mode details

### Full Docker (`make start`)

`docker compose up -d` runs `webapp` (`start.py`, no reload) and `qdrant`
together on the compose network. The app reaches Qdrant as `qdrant:6333`.

- **GPU:** unavailable on macOS — Docker Desktop runs a Linux VM with no
  Metal passthrough, regardless of `PDF_DOCLING_ACCELERATOR_DEVICE`. On
  Linux hosts with NVIDIA + `nvidia-container-toolkit`, GPU is available
  (see below).
- **Memory:** bounded by Docker Desktop's VM allocation. A Docling convert
  plus dense+sparse model load can exceed ~8 GB on a busy VM — see
  [Memory pressure](#memory-pressure) below.

### Hybrid (`make start-hybrid`) — the macOS GPU path

Qdrant stays containerized; the app runs in `.venv` via `run.py` (hot
reload on `backend/` and `.env`). Required `.env` values — already present
for host-side tooling:

```bash
QDRANT_HOST=localhost      # host-side port, not the compose-network name
QDRANT_PORT=6335           # compose publishes 6335 -> container 6333
PDF_DOCLING_ACCELERATOR_DEVICE=mps
```

- **GPU:** Docling layout runs on MPS; dense + sparse embeddings route to
  `CoreMLExecutionProvider` via the registry `extra.providers` list
  (falls back to CPU EP when CoreML isn't compiled in — e.g. inside
  Linux containers, where the same YAML is harmless).
- **Memory:** the process competes with host apps, not a VM budget —
  the Veris Configuration page (`/veris-config.html`) shows live
  available/RSS numbers.
- **Caveats:** you manage the process (`nohup`; `stop-uvicorn` /
  `kill-uvicorn` targets exist). `run.py` reloads on `backend/` and `.env`
  changes, so code edits restart mid-ingest — use `start.py` or the
  container for uninterrupted long runs.

### Linux + NVIDIA (CUDA)

Same compose topology plus the NVIDIA container toolkit on the host and a
GPU-capable image variant:

```yaml
# docker-compose override
services:
  webapp:
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
```

- Swap `onnxruntime` -> `onnxruntime-gpu` for that image (requirements
  are CPU-pinned for size; see `requirements.txt` header).
- Registry `extra.providers`: `[CUDAExecutionProvider, CPUExecutionProvider]`.
- `PDF_DOCLING_ACCELERATOR_DEVICE=cuda`, and reinstall torch from a CUDA
  wheel index (the base image deliberately ships `+cpu` wheels).

### Fully native

Both app and Qdrant on the host (`QDRANT_HOST/PORT` pointed at wherever
Qdrant listens). No Docker layer at all; you own lifecycle and persistence.

## Configuration knobs — what, why, when

Runtime-tunable live via **Admin → Veris Configuration** (`/veris-config.html`,
`POST /config/runtime`); all also settable as env vars in `.env` to persist.

| Knob | Default | Why it exists | When to change it |
| --- | --- | --- | --- |
| `PDF_DOCLING_ACCELERATOR_DEVICE` | `auto` | Selects Docling's inference device (`auto`/`cpu`/`mps`/`cuda`/`cuda:N`/`xpu`) | `mps` for hybrid macOS; `cuda` on NVIDIA; `cpu` to skip accelerator init entirely |
| `PDF_DOCLING_FREE_CONVERTER_MB` | `4096` | After conversion, eject the Docling converter when free RAM is below this floor — embeddings load next and a resident converter can OOM a tight host | Raise if ingests die between conversion and embedding; lower/disable (0) on roomy hosts to keep converters cached across batches |
| `EMBED_BATCH_SIZE_OVERRIDE` | `0` (registry default: dense 32 / sparse 16) | Smaller embedding batches lower peak memory during indexing | Drop to 8–16 when a large doc OOMs mid-embed; restore for throughput |
| `MODEL_CACHE_IDLE_TTL_SECONDS` | `300` | Retrieval models idle-evict so RAM returns between requests | Raise to keep models hot across bursts; lower on memory-starved hosts |
| `INGESTION_MODEL_CACHE_IDLE_TTL_SECONDS` | `900` | Docling converter stays warm across batched docs | Lower when ingestion hosts are tight; raise for bulk Docling batches |
| `MCP_TOOL_TIMEOUT_SECONDS` | `30` | Hard cap on external MCP tool calls | Tune to the slowest legitimate tool you depend on |
| `top_k` | `20` | Default retrieval breadth | Higher recall at more context cost; a few raw-search helpers bind it at startup — restart to guarantee |
| `QDRANT_HOST` / `QDRANT_PORT` | `localhost` / `6333` | App→Qdrant address; `6335` on the host maps to container `6333` | Hybrid/native mode: `localhost:6335`. Containerized app uses compose-network `qdrant:6333` automatically |
| registry `extra.providers` | CoreML then CPU | Chooses the ONNX Runtime execution provider per embedding spec | `[CoreMLExecutionProvider, CPUExecutionProvider]` on macOS; `[CUDAExecutionProvider, CPUExecutionProvider]` on Linux+NVIDIA |

Not runtime-tunable: `PDF_DOCLING_ACCELERATOR_DEVICE` and `extra.providers`
are bound when models load — change them in `.env`/registry and restart
(or eject the cached models to re-load with new providers).

## GPU acceleration — what actually moves

| Component | Framework | macOS (native) | Linux + NVIDIA | Inside macOS Docker |
| --- | --- | --- | --- | --- |
| Docling layout | PyTorch | MPS | CUDA | — |
| Docling TableFormer | PyTorch + CPU cell-matching | partial (table structure recovery is largely CPU) | partial | — |
| Dense embed (bge-base) | fastembed → ONNX Runtime | CoreML EP | CUDA EP (`onnxruntime-gpu`) | — |
| Sparse embed (SPLADE) | fastembed → ONNX Runtime | CoreML EP | CUDA EP | — |
| Rerankers (ColBERT / cross-encoder) | fastembed → ONNX Runtime | CoreML EP via same `extra.providers` pattern | CUDA EP | — |

GPU buys **speed, not memory** — Apple Silicon unified memory means MPS
tensors come from the same RAM pool. The OOM mitigations stay relevant in
every mode.

## Known limitations

- **No GPU inside Docker Desktop on macOS.** The Linux VM has no Metal
  passthrough; `PDF_DOCLING_ACCELERATOR_DEVICE=mps` in the container is a
  no-op. Hybrid mode is the GPU path on this platform.
- **Docling formula enrichment disables MPS.** Upstream behavior: enabling
  formula enrichment forces CPU due to Metal-backend model compatibility.
  We don't enable it today; if you do, expect the layout stage to stop
  using the GPU.
- **MPS `float64` errors after `transformers` bumps.** MPS is `float32`-only;
  some `transformers` releases have produced `float64` type errors on Mac.
  `requirements.lock` pins the tree — re-bench a real datasheet after any
  `make lock` regeneration.
- **TableFormer stays CPU-heavy.** Its cell-matching stage doesn't
  accelerate on MPS; conversion speedup is mostly the layout pass.
- **PyTorch wheels are CPU-pinned** (`--extra-index-url .../whl/cpu`) to
  keep the Linux image ~200 MB instead of ~4 GB. The standard macOS arm64
  wheel still includes MPS; only CUDA needs a reinstall from a CUDA index.

## Memory pressure

The verified failure shape: Docling conversion succeeds, then dense +
sparse models load and the process is SIGKILLed mid-embed when the shared
Docker VM runs out. Mitigations, in order of effort:

1. Runtime knobs on the Veris Configuration page — raise
   `PDF_DOCLING_FREE_CONVERTER_MB`, lower `EMBED_BATCH_SIZE_OVERRIDE`.
2. `docker stats` during ingest to see which containers hold the RAM.
3. Raise Docker Desktop's VM memory (Settings → Resources), or stop
   co-tenant containers.
4. Run hybrid/natively so host RAM is the only budget.
