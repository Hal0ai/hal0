# ONNX on the Strix Halo NPU — research & integration plan for hal0

*Research handoff, 2026-07-04. Sources: Riallto notebooks 5.1/5.2, AMD Ryzen AI
Software 1.7.1 docs (modelrun / linux / relnotes), onnxruntime VitisAI EP docs,
amd/RyzenAI-SW issues #178/#319/#366, FastFlowLM & Lemonade docs, plus a full
sweep of the hal0 tree (providers, profiles, registry, dispatcher, packaging).*

---

## 1. The technology, bottom to top

### 1.1 The NPU itself (XDNA2 on Strix Halo)

The Ryzen AI Max+ 395 ("Strix Halo", STX-H) carries a second-generation XDNA2
NPU: a spatial dataflow array of **32 AIE-ML v2 tiles arranged as 4 rows × 8
columns** (~50 TOPS INT8, with native BF16 in the XDNA2 generation). Workloads
are compiled into binaries loaded onto column partitions; the device is
effectively **single-tenant per partition** — this is why hal0's FLM trio packs
three model roles into one process, and why `src/hal0/providers/npu_columns.py`
reads `xrt-smi examine -r aie-partitions` to report column occupancy.

On Linux the device stack is:

| Layer | Component |
|---|---|
| Kernel | `amdxdna` driver (mainlined; Ryzen AI docs require kernel ≥ 6.10, hal0's FLM image documents ≥ 6.11 + firmware ≥ 1.1.0.0) |
| Device node | `/dev/accel/accel0` (DRM accel class) |
| Userspace | XRT (Xilinx Runtime) + `xrt_plugin…amdxdna` shim |
| Compiler/EP | Vitis AI Execution Provider inside a vendored ONNX Runtime build, or (for LLMs) FastFlowLM's own runtime, or the open MLIR paths (IREE AMD-AIE, Riallto/IRON) |

hal0 already ships the bottom three layers inside
`packaging/toolbox/flm.Dockerfile` (XRT 2.21.75, paired with Ryzen AI 1.7.1)
and passes `/dev/accel/accel0` + `/dev/dri/renderD128` into the slot container
with `memlock=-1` — i.e. **containerized NPU access is proven in-tree**.

### 1.2 ONNX and how models are *developed for* the NPU

ONNX is the interchange format AMD standardized on: "Ryzen AI Software supports
models in ONNX format and leverages ONNX Runtime as the primary mechanism to
load, compile and run models" (opset 17 recommended). The canonical developer
pipeline, which the Riallto notebooks teach in miniature:

1. **Train / fine-tune in PyTorch** (Riallto 5.2: ResNet-50 transfer-learned to
   CIFAR-10 — swap the classifier head, retrain).
2. **Export**: `torch.onnx.export(...)` (Riallto uses opset 13; Ryzen AI now
   recommends 17).
3. **Quantize** for the NPU:
   - Riallto era: `vai_q_onnx.quantize_static()` producing QDQ U8S8
     (activations QUInt8, weights QInt8, PowerOfTwoMethod.MinMSE calibration).
   - Current era: **AMD Quark** (superseded `vai_q_onnx` from Ryzen AI 1.3)
     unifies PyTorch/ONNX quantization. Supported NPU formats today:
     **XINT8, A8W8, A16W8** (INT8 class) and **BF16** on STX/KRK-class devices
     — BF16 often skips calibration entirely, which matters for a
     user-friendly "bring your own model" story.
4. **Run**: create an `onnxruntime.InferenceSession` with
   `VitisAIExecutionProvider`. The EP **auto-partitions the graph** — subgraphs
   with NPU-supported operators compile to the NPU, everything else falls back
   to CPU, transparently. First load compiles; subsequent loads hit a cache
   (`cache_dir`/`cache_key` provider options for dev; **ONNX Runtime EP
   context cache**, optionally AES256-encrypted, for production
   ship-precompiled deployment).

The Riallto notebooks (5.1 inference, 5.2 re-train) are the pedagogical version
of this flow but target **first-gen XDNA (Phoenix) on Windows only**, with the
old `1x4.xclbin` firmware selection via `XLNX_VART_FIRMWARE` and
`vaip_config.json`. The concepts transfer 1:1; the specific tooling does not.
Do not build against the Riallto stack — build against Ryzen AI ≥ 1.7 + Quark.

### 1.3 The Linux situation (the part that matters for hal0)

This changed materially in mid-2026 and is the reason this research is timely:

- **Ryzen AI 1.7/1.7.1 (June 2026) shipped a Linux installer** —
  `ryzen_ai-1.7.1.tgz` + `install_ryzen_ai.sh`, Ubuntu 24.04 LTS + Python
  3.12 only, kernel ≥ 6.10, XRT + amdxdna plugin debs. On Linux users can now
  **compile and run**: CNNs (INT8/BF16), NLP models (BF16), and LLMs via an
  "NPU-only flow".
- **Strix Halo entered the supported-processor list in 1.7** (docs group "Strix
  and Strix Halo as STX", and the STX/KRK class uses the default X2 backend).
  *Caveat:* the Linux install page still says "STX and KRK platforms" without
  naming Strix Halo, and amd/RyzenAI-SW#366 (Ryzen AI MAX+ 395 owner) reports
  the Linux installer rejecting STX-H as "unsupported platform", with no AMD
  response yet. Treat official Linux-on-STX-H VitisAI support as
  **emerging, not settled** — it must be probed at runtime, never assumed.
- **The VitisAI EP is not redistributable in the normal way.** It is not a pip
  package; it arrives via AMD's EULA'd installer tarball. A hal0 toolbox image
  cannot simply `pip install` it, and pre-baking it into a public GHCR image
  has license implications. (Precedent: hal0 builds FastFlowLM *from source*
  in the FLM Dockerfile; the VitisAI EP has no source to build. An
  installer-time fetch on the user's machine — the FLM-host-binary pattern —
  or an AMD-hosted base image are the plausible routes.)
- **For LLMs specifically, ONNX is not the Linux path.** The OGA
  (onnxruntime-genai) "hybrid" NPU+iGPU flow remains Windows-only. On Linux the
  working NPU LLM stack is **FastFlowLM** (closed-source, its own FLM model
  format, Linux support since March 2026) — which is exactly what hal0 already
  ships as the FLM trio — with AMD's **Lemonade** server wrapping FLM for
  OpenAI-compatible serving. hal0 deliberately removed Lemonade (ADR-0008
  superseded) because hal0-api *is* the OpenAI-compatible control plane.

**Conclusion of the landscape scan:** ONNX-on-NPU support in hal0 is *not*
about chat models — FLM owns that and is the right tool. ONNX's value to hal0
is (a) **non-LLM modalities** on NPU/iGPU/CPU — STT, TTS, embeddings, vision,
custom CNN/transformer models — and (b) a **model-creation story**: letting
users export/quantize their own PyTorch models and serve them from a slot.

---

## 2. What hal0 has today (relevant seams)

- **NPU serving = FLM trio only** (`src/hal0/providers/flm.py`): one
  `flm serve` container on port 8088 serving chat + `--embed` + `--asr`;
  shadow roles routed by `dispatcher/npu_trio.py`; gating in
  `api/routes/v1.py` `_is_npu_trio_request()`. No VitisAI/onnxruntime/OGA
  anywhere in the serving path.
- **ONNX already runs in two toolboxes, ad hoc, CPU-only**: Kokoro TTS
  (kokoro-onnx) and Moonshine STT (`useful-moonshine-onnx`, CPU EP only —
  `PLAN.md:118` and `catalog._RUNTIME_TO_HOST_BACKENDS["moonshine"] = ("cpu",)`
  is the existing precedent for constraining a runtime to the EPs it truly
  supports).
- **Adding a runtime family is a well-worn groove** (qwen3tts was added this
  way): a `Provider` subclass returning a `RuntimeLaunchPlan`
  (`providers/base.py`), registered in `providers/__init__.py:_PROVIDERS`, a
  branch in `container.py:_spec_provider_for()`, a `RuntimeFamily` +
  classification in `profiles/__init__.py:_runtime_family()` /
  `_supported_slot_types()`, a `SEED_PROFILES` entry (`config/schema.py`), a
  seed slot TOML, and catalog wiring in `capabilities/catalog.py`. Systemd/
  podman argv assembly is centralized in `container.py:_render_unit_from_plan`
  — a new provider never touches it.
- **Model registry & pulls generalize cleanly**: `registry/pull.py:run_pull`
  already does streamed HF download + incremental SHA-256 + atomic rename +
  registry upsert; `metadata.runtime` / `backends` fields exist (FLM pulls set
  `runtime="flm"`, `backends=["npu"]`). ONNX entries fit without schema
  surgery: `runtime="onnx"`, plus metadata for opset, quant format
  (XINT8/A8W8/A16W8/BF16), and required EP.
- **Hardware probe** already detects the NPU (`/dev/accel/*` or
  `/sys/module/amdxdna` → `platform="strix-halo"`), and
  `available_backends()` already gates NPU on the FLM image being present —
  the same gate pattern extends to "VitisAI EP available".

---

## 3. Recommendation: how hal0 should support ONNX

### Phase 1 — a first-class `onnx` runtime family (CPU/iGPU), generalizing what exists

Ship `packaging/toolbox/onnx.Dockerfile`: a small FastAPI/uvicorn server (same
shape as `packaging/toolbox/kokoro/kokoro_server.py`) around **onnxruntime**,
exposing the OpenAI-compatible endpoints hal0 already dispatches — start with
`/v1/embeddings` and `/v1/audio/transcriptions`, then `/v1/audio/speech` and a
vision/classification surface. EPs in phase 1: **CPUExecutionProvider**
everywhere, **ROCm/MIGraphX EP** where the probe says the iGPU has compute.

- New `OnnxProvider` (`src/hal0/providers/onnx.py`, template: `qwen3tts.py`),
  `RuntimeFamily "onnx"`, `[profile.onnx-cpu]` / `[profile.onnx-rocm]` seeds.
- Registry entries for curated ONNX models (Whisper/Moonshine-class STT,
  BGE/GTE-class embedders, Kokoro) with `hf_repo`/`sha256` like every other
  model — pulled by the existing `run_pull`.
- This immediately de-specializes Kokoro/Moonshine: they become "ONNX models on
  the onnx runtime" instead of bespoke providers, and it works on **every**
  platform hal0 supports, not just Strix Halo.

*Why first: zero licensing risk, zero dependence on AMD's Linux SKU decisions,
and it builds the exact chassis the NPU variant slots into.*

### Phase 2 — the NPU variant (VitisAI EP), probe-gated and honest

Add an `onnx-npu` profile whose toolbox layers Ryzen AI Linux components (XRT
is already built in the FLM image — reuse that stage) plus AMD's vendored
onnxruntime-with-VitisAI-EP. Because the EP is EULA-distributed:

- **Acquire at install time on the host** (mirror the host-`flm`-binary
  pattern): `hal0 setup` offers "NPU ONNX support", downloads
  `ryzen_ai-<ver>.tgz` from AMD with user acceptance, and either bind-mounts
  the EP into the container or builds a local-only image layer. Never push the
  EP to GHCR.
- **Probe, don't assume**: gate on kernel ≥ 6.10 + `amdxdna` + firmware version
  + a real `InferenceSession` smoke test with `VitisAIExecutionProvider`, the
  way FLM health requires a real 1-token completion. Surface the result on
  `/api/hardware` so the dashboard can say *why* NPU-ONNX is unavailable
  (given the STX-H-on-Linux ambiguity in RyzenAI-SW#366, expect this gate to
  fail on some driver/firmware combos — hal0's "UI honesty" principle applies).
- **Arbitrate the single-tenant NPU**: the FLM trio and a VitisAI session
  contend for AIE columns. `npu_columns.py` occupancy is already read;
  slot-load should warn or serialize when both an FLM slot and an `onnx-npu`
  slot are active. This is the one genuinely novel piece of engineering.
- **Cache compiled models**: mount a persistent volume for the EP cache
  (`cache_dir`) so first-load compilation cost is paid once per model per
  driver version; consider EP-context-cache export for curated models later.

### Phase 3 — model *creation*: `hal0 convert`

The Riallto 5.2 workflow (train → export → quantize → deploy), productized as
a toolbox, not a serving slot:

- `hal0 convert <model.pt|hf-repo> --quant bf16|xint8` runs a one-shot podman
  job (`packaging/toolbox/quark.Dockerfile`: PyTorch + `torch.onnx.export`
  opset 17 + **Quark**) that emits a quantized `.onnx` into
  `/var/lib/hal0/models/` and registers it (`runtime="onnx"`, quant + opset in
  metadata). BF16 first — no calibration dataset needed, and it's the format
  AMD is pushing on XDNA2; INT8+calibration as the advanced path.
- `hal0 slot create --model <converted-id> --device npu|gpu-rocm|cpu` then
  serves it through the phase-1/2 runtime. That's the end-to-end "create and
  use ONNX models inside hal0" loop.

### Explicitly out of scope / anti-recommendations

- **Do not route LLM chat through ONNX/OGA on Linux** — Windows-only hybrid
  flow; FLM (ADR-0009) remains the NPU chat/embed/ASR answer. Revisit only if
  AMD ships the OGA hybrid flow for Linux on STX-H.
- **Do not reintroduce Lemonade** as a serving layer — hal0-api already is the
  OpenAI-compatible plane; Lemonade would duplicate the dispatcher (ADR-0008
  stands). Watch it as an upstream reference implementation instead.
- **Do not build on Riallto/IRON or IREE AMD-AIE** for serving — they are the
  right tools for custom kernels and education, wrong altitude for a model
  appliance.

---

## 4. Key references

- Riallto 5.1 (PyTorch→ONNX NPU inference): https://riallto.ai/notebooks/5_1_pytorch_onnx_inference.html
- Riallto 5.2 (re-train + quantize): https://riallto.ai/notebooks/5_2_pytorch_onnx_re-train.html
- Ryzen AI model run (VitisAI EP, quant formats, caching): https://ryzenai.docs.amd.com/en/latest/modelrun.html
- Ryzen AI Linux install (1.7.1): https://ryzenai.docs.amd.com/en/latest/linux.html
- Ryzen AI release notes (Linux/STX-H/Quark timeline): https://ryzenai.docs.amd.com/en/latest/relnotes.html
- onnxruntime VitisAI EP: https://onnxruntime.ai/docs/execution-providers/Vitis-AI-ExecutionProvider.html
- STX-H Linux SKU request: https://github.com/amd/RyzenAI-SW/issues/366
- FastFlowLM: https://github.com/FastFlowLM/FastFlowLM · Lemonade on FLM/Linux: https://lemonade-server.ai/flm_npu_linux.html
