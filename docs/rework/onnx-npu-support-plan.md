# ONNX via NPU — Support Plan for hal0

> **Date:** 2026-07-22 · **Source:** user directives + AMD Ryzen AI docs + Ryzen AI Software 1.7.0 + `spec-hw-slot-ownership.md`
>
> ONNX models on the NPU via Vitis AI Execution Provider are an entirely different path from FLM (FastFlowLM).
> FLM = proprietary binary, single-process Chat+STT+Embed, its own model format and pull mechanism.
>
> There are **two** ONNX-on-NPU approaches:
> 1. **Raw ONNX Runtime + Vitis AI EP** — standard `onnxruntime.InferenceSession`, any ONNX model, manual tokenizer/decoder.
> 2. **ONNX Runtime GenAI (OGA)** — higher-level LLM-specific API (`Model`, `Generator`, `Tokenizer`), pre-built inference loop, Quark UINT4/AWQ quantization, hybrid NPU+CPU mode, architecture-aware (Llama, Qwen, Gemma, Phi, Mistral).
>
> This plan covers all three NPU paths (FLM, Raw ONNX, OGA) and how to design support.
>
> **⚠️ NOTE:** All ONNX NPU work is **post-v1.0.0** — this is a design/planning document, not a v1.0 deliverable. FLM is the only NPU path shipping in v1.0.

---

## 1. Three NPU Paths — Architecture Diffs

| Axis | FLM | ONNX (Raw) | OGA (GenAI) |
|------|-----|------------|-------------|
| **Runtime** | `flm serve` binary | `onnxruntime.InferenceSession` + Vitis AI EP | `onnxruntime-genai` (`Model`, `Generator`, `Tokenizer`) + Vitis AI EP |
| **API Level** | High — single binary | Low — raw tensor I/O, manual tokenizer/decoder | High — LLM-specific (generate, encode, decode) |
| **Model Format** | FLM proprietary kernels | ONNX (any model) | ONNX (LLM archs only: Llama, Qwen, Gemma, Phi, Mistral) |
| **Quantization** | W4ABF16 (built-in) | AMD Quark: INT8, BF16, UINT4 | AMD Quark: UINT4 (AWQ), hybrid or NPU-only |
| **Model Prep** | `flm pull <tag>` | PyTorch → Quark → ONNX export | PyTorch → Quark UINT4 AWQ → OGA model builder → ONNX |
| **Chat** | ✅ `flm serve <tag>` | ✅ via custom wrapper | ✅ via `Generator` with pre-built KV cache |
| **STT** | ✅ Built-in | ✅ Separate whisper ONNX model | ❌ Not supported by OGA (LLM-only) |
| **Embed** | ✅ Built-in | ✅ Separate embed ONNX model | ❌ Not supported by OGA (LLM-only) |
| **Process Model** | One process = 3 roles | One ONNX model = one slot | One ONNX model = one slot |
| **NPU Exclusivity** | Single-tenant | Single-tenant (shared with FLM) | Single-tenant (shared) |
| **GPU Fallback** | None | Hybrid: NPU + CPU/GPU | Hybrid mode (NPU + CPU fallback layers) |
| **Linux Support** | ✅ | ✅ (Ryzen AI SW ≥1.2) | ✅ (Ryzen AI SW ≥1.2) |
| **hal0 Slot Model** | FLM Provider | Raw ONNX Provider | OGA Provider |

---

## 2. ONNX NPU Stack — What hal0 Needs

There are two ONNX-on-NPU paths with different stack requirements.

### 2A. Raw ONNX Runtime + Vitis AI EP

For **non-LLM models** (embedding, STT, image) and any ONNX model that doesn't fit the OGA architecture list.

**Toolbox / Container Image**

A new toolbox image for ONNX NPU inference:

```
ghcr.io/hal0ai/hal0-toolbox-onnx-npu:latest
```

**Contents:**
- Ryzen AI Software 1.7.0 (drivers: XRT, XDNA, Vitis AI EP)
- ONNX Runtime 1.21+ with Vitis AI EP
- AMD Quark tools (for on-box quantization — optional; most models ship pre-quantized)
- Python 3.12 (provider language)
- `hal0-onnx-npu-server` — thin FastAPI/Flask wrapper around `onnxruntime.InferenceSession`
  - `POST /v1/chat/completions` (OpenAI-compatible)
  - `GET /health` (model loaded check)
  - `POST /v1/embeddings` (for embed models)
  - `POST /v1/audio/transcriptions` (for ASR models)

### 2.2 Model Store

ONNX models live under `/var/lib/hal0/models/onnx/<model_id>/`:

```
/var/lib/hal0/models/onnx/
├── meta-llama3.2-1b-instruct-u4/
│   ├── model.onnx
│   ├── model.onnx.data
│   ├── tokenizer.json
│   └── tokenizer_config.json
├── whisper-v3-turbo-q8/
│   ├── encoder_model.onnx
│   ├── decoder_model.onnx
│   └── ...
└── bge-small-en-u8/
    ├── model.onnx
    └── tokenizer.json
```

### 2.3 hal0 Runner Entry

```python
# src/hal0/runners/__init__.py
"onnx-npu": Runner(
    "onnx-npu",
    "ghcr.io/hal0ai/hal0-toolbox-onnx-npu:latest",
    "onnx-npu",           # runtime_family
    RunnerSupports(),
    "npu",                # device_class
    None,                 # backend
    None,                 # manifest_key
    supported_backends=("npu",),
    format_arch="onnx",
),
```

### 2.4 Provider (`src/hal0/providers/onnx_npu.py`)

```python
class OnnxNpuProvider(ContainerProvider):
    """ONNX models on Ryzen AI NPU via Vitis AI EP."""

    runtime_family = "onnx-npu"
    device_class = "npu"

    def image_ref(self, slot_cfg: dict) -> str:
        return resolve_runner_image(get_runner("onnx-npu"))

    def container_spec(self, slot_cfg, ...) -> ContainerSpec:
        model = slot_cfg["model"]["default"]  # model_id → resolves to ONNX path
        model_dir = f"{store_root}/onnx/{model}"
        return ContainerSpec(
            image=self.image_ref(slot_cfg),
            mounts=[(model_dir, "/models:ro")],
            devices=["/dev/accel/accel0", "/dev/dri/renderD128"],
            env={"ONNX_MODEL_DIR": "/models"},
            ...
        )
```

---

## 3. Model Lifecycle

### Pull / Prepare

1. **Pre-quantized ONNX hub:** hal0 serves curated ONNX models from `Hal0ai/hal0-onnx-models` (GitHub Releases or HF repo). These are pre-quantized with Quark and ready to deploy.
2. **Pull flow:** `hal0 model pull onnx://meta-llama3.2-1b-instruct-u4` → downloads ONNX artifacts → validates `model.onnx` loads with `onnxruntime.InferenceSession` using CPU EP (cheap pre-flight).
3. **On-box quantize (advanced):** `hal0 model quantize <gguf_model> --target onnx-npu --quant u4` → extracts GGUF weights → Quark quantize → ONNX export → stores under `onnx/` in model store.

### Slot Lifecycle

ONNX NPU slots follow the same slot lifecycle as llama-server slots:

- **Create:** `hal0 slot create --type llm --device npu --runner onnx-npu --model meta-llama3.2-1b-instruct-u4`
- **HW Grid:** `device = "npu"`, `binary = "onnx-npu"`, `threads = 0`, `n_gpu_layers = 0`
- **Profiles:** Device-agnostic profile templates still apply (sampler params, ctx length, etc.). Copy-on-stamp into model flags.

### Exclusive NPU Access

FLM and ONNX NPU share the same NPU hardware. Rule: **only ONE NPU process at a time across all NPU solts.** The slot manager must enforce mutual exclusion:

- If `hal0-slot@flm` is running → any ONNX NPU slot creation/launch is blocked until FLM stops
- If an ONNX NPU slot is running → FLM launch is blocked
- The NPU exclusivity checker (`src/hal0/providers/npu_exclusivity.py`) needs to cover both providers

---

## 4. hal0 Integration Points

### Models Page

ONNX NPU models get the same treatment as slot-mode specific type:

- **"NPU / ONNX" tab** (alongside "NPU / FLM" tab) in models page
- Same icon scheme: ⬇️ download for not-installed, ✅ for installed
- 3-dot menu: edit model settings, assign to slot, delete

### Slot Edit / HW Grid

When `device = "npu"` and `binary = "onnx-npu"`:

```
┌─ NPU (ONNX) ──────────────────────────────────────────┐
│                                                         │
│  Device:   NPU                                          │
│  Runner:   onnx-npu                                     │
│  Model:    [meta-llama3.2-1b-instruct-u4     ▾]        │
│  Port:     8089                                         │
│  Context:  8192                                         │
│                                                         │
│  [Save Slot]                                            │
└─────────────────────────────────────────────────────────┘
```

### Dispatch

ONNX NPU slots go through the **standard dispatcher** — unlike FLM, there's no trio model.
The ONNX NPU server exposes an OpenAI-compatible `/v1/chat/completions` endpoint, so the
dispatcher routes to it identically to a llama-server slot. No `NpuTrioRouter` needed.

---

## 5. Implementation Phases

### Phase 0 — Feasibility (pre-code, 1-2 hrs)

| Step | What |
|------|------|
| **0.1** | Verify Ryzen AI SW 1.7.0 runs in a podman container on hal0 (LXC 105 / 143 / 150). XRT driver passthrough for NPU. |
| **0.2** | Test ONNX Runtime + Vitis AI EP loads an ONNX model (Llama 3.2 1B UINT4) on the Strix Halo NPU. |
| **0.3** | Benchmark: tok/s vs FLM on same model (if FLM supports it); measure NPU occupancy. |
| **0.4** | Assess: is ONNX NPU performance competitive enough to ship? Document results in `docs/rework/onnx-npu-feasibility.md`. |

**Gate:** If step 0.2 fails (driver/containerization issue) or step 0.4 shows <50% of FLM performance → defer to post-v1.0.

### Phase 1 — Toolbox & Provider (2-3 days)

| Step | What |
|------|------|
| **1.1** | Build `hal0-toolbox-onnx-npu` container image (Dockerfile: Ryzen AI SW + ONNX Runtime + hal0 server wrapper) |
| **1.2** | Publish to `ghcr.io/hal0ai/hal0-toolbox-onnx-npu` |
| **1.3** | Implement `OnnxNpuProvider` in `src/hal0/providers/onnx_npu.py` |
| **1.4** | Add runner entry in `src/hal0/runners/__init__.py` |
| **1.5** | Implement `onnx-npu-server` wrapper (FastAPI, `/v1/chat/completions`, `/health`) |
| **1.6** | Wire NPU exclusivity to cover onnx-npu |

### Phase 2 — Model Lifecycle (1-2 days)

| Step | What |
|------|------|
| **2.1** | ONNX model store integration (discovery, scan, register) |
| **2.2** | Pull flow: `hal0 model pull onnx://<model>` |
| **2.3** | Curated ONNX model catalog (first wave: Llama 3.2 1B/3B, Qwen 2.5 1.5B, whisper-v3-turbo, bge-small-en) |
| **2.4** | Models page: "NPU / ONNX" tab |

### Phase 3 — UI & Polish (1 day)

| Step | What |
|------|------|
| **3.1** | NPU slot create/edit drawer: ONNX mode (different from FLM) |
| **3.2** | Model drawer: ONNX model settings (ctx length, sampler overrides) |
| **3.3** | Profile: `profile.onnx-chat` (device-agnostic template for ONNX models) |

---

## 6. Key Architectural Decisions

| Decision | Rationale |
|----------|-----------|
| **Separate runner from FLM** | ONNX models have different lifecycle, format, and process model than FLM. Shared `device_class="npu"` but distinct `runtime_family`. |
| **Standard slot model (not trio)** | ONNX Runtime doesn't bundle Chat+STT+Embed. Each ONNX model is its own slot — follows inference-slot lifecycle. |
| **NPU exclusivity across providers** | FLM and ONNX NPU share one physical NPU. Only one can be active. |
| **Pre-quantized model hub** | hal0 ships curated ONNX models. On-box quantization via Quark is an advanced feature (post-v1.0). |
| **Containerized, not host-or-native** | Like FLM, the ONNX NPU runtime runs in a podman container. Benefits: version pinning, dependency isolation, clean teardown. |

---

## 7. Deferred (Post-v1.0.0)

| Item | Reason |
|------|--------|
| On-box Quark quantization | Complex integration, Python 3.12-only, needs torch. |
| Hybrid NPU+GPU dispatch | Complex routing, needs multi-slot coordination. |
| Vision/image-gen via ONNX on NPU | Needs Stable Diffusion ONNX pipeline — separate effort. |
| Multi-NPU device support (XDNA multi-column) | Requires FLM/XRT multi-column API maturity. |
