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
  `kill-uvicorn` targets exist). `run.py` auto-reloads on `backend/` file
  changes (code edits restart mid-ingest — use `start.py` or the container
  for uninterrupted long runs). `.env` is *not* watched, but each worker
  spawn re-reads it — a code-change reload or `make stop-uvicorn` (the
  supervisor respawns a fresh worker) both pick up env changes.

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

## Model cache lifecycle — how RAM is managed

All heavyweight local models share one eviction mechanism:
`TTLModelCache` (`backend/retrieval/model_cache.py`), a process-wide
per-model idle cache.

**What lives in it**

| Cache label | Holds | TTL knob | Default |
| --- | --- | --- | --- |
| `retrieval` | Dense + sparse embedding models (BGE-M3 / bge-base + SPLADE), ColBERT, cross-encoder | `MODEL_CACHE_IDLE_TTL_SECONDS` | 300 s |
| `ingestion` | The Docling `DocumentConverter` (layout-heron + TableFormer + OCR + caption VLM) | `INGESTION_MODEL_CACHE_IDLE_TTL_SECONDS` | 900 s |

**Lifecycle of one model entry**

```
first get()          model loads (device bound here — see GPU table),
                     timestamp = now
each get()           timestamp refreshed → an actively-used model
                     NEVER evicts, even mid-conversation
idle > TTL           entry dropped, gc.collect() frees ONNX/PyTorch RAM
next get()           cold reload (~1–3 s ONNX, ~10 s BGE-M3, ~30 s+ Docling)
```

**The sweep mechanics (why eviction actually happens).** Eviction is
decided per-model by *last-use time* — never by system traffic. Two
triggers run it:

- **Timer** — every live cache registers itself in a shared `WeakSet`;
  one daemon thread sweeps all caches every 60 s, so an idle model
  releases RAM at TTL expiry *even if zero requests ever arrive*.
- **On access** — each `get()` also sweeps first, so stale sibling models
  (e.g. a model you switched away from) are dropped opportunistically.

Setting a TTL to `0` disables eviction — the model stays resident for
the process lifetime.

**Runtime operations.** The TTLs are live-tunable on **Admin → Veris
Configuration** (`/veris-config.html`): a change propagates into running
caches via `set_idle_timeout_for_label`, so no restart is needed. The
**Models** page (`/models.html`, backed by `model_cache_admin.py`)
lists every resident model with its bound accelerator/provider, and can
`eject` a single entry (immediate unload) or `reload` it (evict + reload
with the recorded loader — e.g. to pick up a changed provider config
without restarting).

**Why two TTLs.** Embedding models serve queries *and* indexing, reload
in seconds, and sit between conversational bursts — a short TTL is safe.
The Docling converter is ingestion-only and costs 10–60 s to rebuild, so
it gets a longer window to survive the gap between batch jobs — but it
still unloads on the timer when ingest stops entirely.

## BGE-M3 single-pass embeddings (experimental)

The default local stack runs **two** models per chunk: bge-base (dense,
768-d, CoreML/ONNX) + SPLADE (sparse). An optional unified path replaces
both with a single BGE-M3 forward pass via FlagEmbedding/PyTorch:

```
Stage A (indexing):  BGE-M3 one pass → dense (1024-d) + sparse
                     → named vectors {"dense", "sparse"} in Qdrant

Stage B (retrieval): dense+sparse prefetch → RRF fusion
                     → optional ColBERTv2 MaxSim rescore (existing path)
```

**Why `m3`, not `hybrid`:** `hybrid` already names the dense+sparse *search
mode* in domain config. The model entry is `local:m3_default`; the profile
is `local-bgem3` (structurally `local-hybrid`, different default model key).
`emits: [dense, sparse]` in `local_models_registry.yaml` declares the
capability — when a domain's `embedding_model_key` resolves to an emits
model, indexing makes one encode call per batch instead of two.

**Why a separate collection:** M3 dense is 1024-d (vs 768) and its sparse
vectors live in M3's own vocabulary — SPLADE query vectors are meaningless
against them. `_sparse_embedding_spec()` resolves the sparse model from the
domain's `embedding_model_key`, so M3-indexed collections are always
queried with M3 sparse. The experiment domain is
`semiconductor_datasheets_m3` → `semi_datasheets_m3_docling_v1`.

**Why no stored ColBERT:** M3 also emits a ColBERT matrix — a 1024-d vector
*per token*, ~1 MB/chunk, ~230 MB/doc. We don't store it; Stage-B rescoring
reuses the existing ColBERTv2 MaxSim reranker (`use_colbert`), which
re-encodes fused candidates at query time at zero storage cost.

**Measured on Apple M2 (MPS):** ~0.25 s/text vs ~1.2 s/chunk for the
bge-base+SPLADE pair — roughly 4–5× embedding throughput — plus 8,192-token
context vs 512 (datasheet chunks no longer truncate). Costs: ~2.3 GB model
download (cached at `${LOCAL_MODELS_CACHE_PATH}/flagembed_cache`), ~5 min
cold load on first fetch, ~10 s warm load; the model participates in the
same idle-TTL model cache.

**Try it:** point a domain at `profile: local-bgem3` (or
`embedding_model_key: local:m3_default`) in `domain_embedding_config.yaml`,
then ingest via the normal Docling route. Requires `FlagEmbedding` (in
`requirements.txt`). Docker users: FlagEmbedding pulls the PyTorch
stack — the CPU-pinned wheels keep the image small, but there is no ONNX
path for M3, so CoreML provider config does not apply to it.

**M3 knobs** (on the `m3` registry entry — the retrieval-spec `device`/`batch_size`
defaults are ONNX-era globals and do *not* pin M3):

| Key | Default | When to set |
|---|---|---|
| `device` | unset → auto `mps` → `cuda` → `cpu` | `cpu` to keep M3 off GPU, `cuda:N` to pick a card |
| `batch_size` | 64 | Lower on <16 GB hosts; `EMBED_BATCH_SIZE_OVERRIDE` still wins when set |
| `use_fp16` | false | `true` halves MPS/CUDA memory + speeds encode; keep false on CPU |
| `max_length` | 8192 | Lower only if your chunks never approach it |

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
