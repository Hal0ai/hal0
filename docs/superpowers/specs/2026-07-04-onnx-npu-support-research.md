# hal0 + ONNX / Strix Halo NPU — Research & Support Design

**Date:** 2026-07-04
**Status:** Research / proposal — not yet scheduled. Grounds a decision about whether and how hal0 should support ONNX model creation and use, especially on the AMD XDNA NPU.
**Scope:** External landscape (ONNX runtimes on Ryzen AI, XDNA/XDNA2, the Riallto + Ryzen AI toolchains) + hal0's current internals + a concrete, staged integration proposal keyed to hal0's real extension points.

---

## TL;DR

- **ONNX-on-NPU is a Windows-first story.** AMD's ONNX Runtime + Vitis AI Execution Provider (general ONNX) and the OGA / OnnxRuntime-GenAI LLM path are *currently supported only on Windows*. On Linux there is **no stable ONNX Runtime + VitisAI EP** (source build only), and cache dirs are invalid across driver/EP versions.
- **hal0's Linux NPU path is FastFlowLM (FLM)** — an NPU-native, non-ONNX runtime — and hal0 already uses it (the NPU trio). This is the same runtime **Lemonade 10.0** adopted for Linux NPU LLMs.
- **hal0 already consumes ONNX weights** (Kokoro TTS, Moonshine STT) but via bespoke per-model containers, **not** a generic ONNX runtime. The registry detects **GGUF by magic bytes**; `.onnx` falls through to a filename-only heuristic. Runtime families are `llama-server` / `flm` / `kokoro` / `comfyui` — there is **no** generic ONNX / VitisAI / OGA family and **no** `npu` path other than FLM.
- **Recommendation:** the highest value-per-risk move is a **generic non-NPU `onnxruntime` runtime family** (CPU / iGPU-ROCm) + an **ONNX convert/quantize creation flow**. That unlocks the ONNX vision/embedding ecosystem *today*, unifies the existing ONNX voice models, and leaves a clean, experimental `onnx-npu` seam for when AMD's Linux VitisAI EP / OGA matures.

---

## Part 1 — How ONNX + the Strix Halo NPU fit together

### ONNX is a format, not a runtime
You train in PyTorch/TF, **export** to ONNX (a protobuf graph of ops + weights), **quantize**, then **run** it through an *execution provider* (EP) that maps ops onto hardware. On the AMD NPU that EP is the **Vitis AI Execution Provider (VitisAI EP)** inside **ONNX Runtime**, which partitions the graph — NPU-supported ops run on the NPU, the rest fall back to CPU.

### The XDNA / XDNA2 NPU
A spatial array of AI Engine (AIE) tiles arranged in **columns** (Strix Point = 4 rows × 8 columns). A workload is mapped onto a hardware overlay called an **xclbin** (e.g. `AMD_AIE2P_4x4_Overlay.xclbin` for STX/KRK; the older `1x4` / `Nx4` overlays are deprecated). Only **one AMDXDNA hardware context exists per host** — the exact constraint hal0 already encodes in its FLM-trio exclusivity rule.

### The developer workflow (from the Riallto notebooks)
1. Train / fine-tune in PyTorch (the notebook does transfer-learning on ResNet-50).
2. Export: `torch.onnx.export(..., opset_version=13)` (Ryzen AI docs recommend **opset 17**).
3. Quantize: `vai_q_onnx.quantize_static(...)` (the Vitis AI Quantizer, now folded into **Quark**) → **QDQ** format, **U8S8** (activations `QUInt8`, weights `QInt8`) with a calibration dataset. VitisAI EP accepts **INT8 or BF16**; FP32 CNN/transformer inputs are auto-converted to BF16.
4. Run: `onnxruntime.InferenceSession(model, providers=['VitisAIExecutionProvider'], provider_options=[{...}])`. First load **compiles to the NPU format and caches it** (keyed by MD5, under `cache_dir/cache_key`).

### The critical caveat: Windows-first
- The Ryzen AI Software (ONNX Runtime + VitisAI EP, and the ONNX-based **OGA / OnnxRuntime-GenAI** LLM path) is *"currently supported only on Windows."*
- On **Linux**, AMD does **not** ship a stable ONNX Runtime + VitisAI EP — you build from source (complex) — and VitisAI cache dirs are **invalid across driver/EP versions** (a real footgun for hal0's auto-update flow).
- What *is* Linux-ready: the **amdxdna** driver is mainline (kernel ≥ 6.10 / 6.11), installed via **XRT + the `xrt_plugin…amdxdna` package**, verified with `xrt-smi examine`. STX/KRK confirmed.
- **NPU LLMs on Linux run through FastFlowLM (FLM)** — NPU-native, not ONNX — which is what Lemonade 10.0 adopted and what hal0 already uses.

### Three NPU runtimes, not interchangeable

| Runtime | Format | Where it runs | Linux? | hal0 today |
|---|---|---|---|---|
| **FLM** (FastFlowLM) | `.flm` NPU-native | NPU-only, ≤4K ctx | ✅ yes | ✅ **in use** (the trio) |
| **OGA** (OnnxRuntime-GenAI) | ONNX | NPU prefill + iGPU decode (hybrid), >4K ctx | ❌ Windows-only | ✗ |
| **VitisAI EP** (general ONNX) | ONNX (QDQ INT8 / BF16) | NPU + CPU fallback (CNN / vision / BERT) | ⚠️ source-build only | ✗ |

---

## Part 2 — Where hal0 sits today

- hal0 is **already an FLM-based NPU platform** (the NPU trio: one `flm serve` hosting chat + STT + embed).
- hal0 **already uses ONNX weights** — Kokoro (TTS) and Moonshine (STT) ship as `.onnx` bundles (`src/hal0/registry/curated.py`) — but served by bespoke containers (`kokoro-server`, `moonshine-server`), **not** a generic ONNX runtime.
- **Registry** detects **GGUF by magic bytes** (`src/hal0/registry/detect.py`); a `.onnx` file falls through to a filename-only heuristic with no opset / IO-shape parsing.
- **Runtime families** are `llama-server`, `flm`, `kokoro`, `comfyui` (`src/hal0/providers/container.py:413`). There is **no** generic ONNX / VitisAI / OGA family, and **no** `npu`-device path other than FLM.

**Conclusion:** "supporting ONNX" is a genuinely new capability — a new runtime family + a registry format + device/profile wiring — not a tweak.

---

## Part 3 — What "support ONNX in hal0" should mean (pick deliberately)

Three very different products hide under "ONNX support," ordered by value-per-risk on hal0's Linux + podman reality:

### ① Generic ONNX Runtime slot (non-NPU) — do this first
A new `onnxruntime` runtime family serving arbitrary ONNX models on **CPU or iGPU** (ROCm / MIGraphX EP). Unlocks the large ecosystem of ONNX vision classifiers, embedding models, rerankers, and small transformers — and lets hal0 **unify Kokoro / Moonshine** (already ONNX) under one runtime instead of two bespoke containers. Cross-platform, low risk, no driver coupling. The pragmatic "use ONNX models" surface.

### ② ONNX *creation* flow — high leverage, pairs with ①
Ship a toolbox image (`torch` + `onnx` + `onnxruntime` + `Quark` / `vai_q_onnx`) and expose a `hal0 model convert <model> --onnx [--quantize u8s8]` CLI + admin-MCP tool producing a registry-registered ONNX artifact. This is the "creation" half of the ask — hal0 becomes a place people *make* models, not just run them.

### ③ ONNX-on-NPU via VitisAI EP — experimental, gated
Add an `onnx-npu` profile flagged experimental, gated on the Linux VitisAI-EP build maturing. **Do not** make it a headline path: the Windows/Linux parity gap and driver-versioned cache invalidation are real. OGA hybrid LLM (NPU prefill + iGPU decode) is the most exciting NPU story but is Windows-only today — track Lemonade for when it lands on Linux.

---

## Part 4 — Concrete integration design (keyed to hal0's real extension points)

1. **Provider + family.** Add `OnnxRuntimeProvider(Provider)` returning a `ContainerSpec` (sibling to `KokoroProvider` / `ComfyUIProvider`), wired into `_spec_provider_for` (`src/hal0/providers/container.py:408`) under a new `runtime_family = "onnxruntime"`. A small `onnx-server` container exposing OpenAI-shaped endpoints (`/v1/embeddings`, `/v1/rerank`, and a generic `/v1/onnx/infer` for vision / classifiers).

2. **Registry detection.** Extend `src/hal0/registry/detect.py` to parse ONNX protobuf (read `graph`, `opset_import`, IO tensor shapes) → `confidence="high"` like GGUF, deriving type / labels from graph IO (e.g. image-in / logits-out → `image` / `vision`). Handle `.onnx` **and** multi-file bundles — which also lets Kokoro / Moonshine fold into this path.

3. **Profiles & devices.** Add `onnx-cpu` and `onnx-rocm` profiles (+ experimental `onnx-npu`) with `runtime_family="onnxruntime"`, and wire `DEVICE_DEFAULT_PROFILES` / `derive_profile`.
   ⚠️ **Intersects the platform review:** the device→profile derivation is already forked three ways (finding PS-4) and `DEVICE_DEFAULT_PROFILES` is stale (PS-1). Fix those first or the ONNX profile inherits the same incoherence. See `handoffs/platform-review-2026-07-03.md`.

4. **NPU exclusivity must extend to ONNX-NPU.** If `onnx-npu` ever ships, it contends with FLM for the **single** AMDXDNA context. hal0's exclusivity rule currently only reasons about `device=npu, type=llm` FLM slots and only at config-write time (review finding DR-5). An ONNX-NPU slot must join that same single-context arbitration, enforced at **load** time.

5. **Capabilities + UI.** New capability cards ("Vision classifier", "Custom ONNX") mapping to onnx slot types; the model modal handles `.onnx` import + the convert / quantize flow; the slot drawer surfaces EP / opset / quantization provenance (fits the existing provenance-badge pattern).

---

## Risks & caveats

- **Linux VitisAI EP instability** — source-build only; no AMD-shipped stable package as of Ryzen AI SW 1.7.1.
- **Driver ↔ cache coupling** — VitisAI compiled-model caches are invalid across driver / EP versions; hal0's update flow would need to invalidate them on driver bumps.
- **Single AMDXDNA context** — ONNX-on-NPU and FLM cannot both hold the NPU; arbitration required (ties to DR-5).
- **NPU model-library narrowness** — the NPU path (FLM or ONNX) has a much smaller model library than GGUF; NPU-only LLMs cap at ~4K context.

---

## The strategic read

hal0's Linux-native, container-per-slot design means the **ONNX-on-NPU dream is not yet a Linux reality** — AMD keeps that on Windows (VitisAI EP + OGA), and the Linux NPU path is FLM, which hal0 already rides. The highest-value, lowest-risk move is a **generic non-NPU `onnxruntime` runtime family + an ONNX convert / quantize creation flow**: it unlocks the whole ONNX vision / embedding ecosystem today, unifies hal0's existing ONNX voice models, and leaves a clean `onnx-npu` experimental seam for when AMD's Linux VitisAI EP or OGA matures.

---

## Sources

- [Ryzen AI: Model Compilation & Deployment](https://ryzenai.docs.amd.com/en/latest/modelrun.html)
- [Riallto 5.1 — PyTorch→ONNX inference on the NPU](https://riallto.ai/notebooks/5_1_pytorch_onnx_inference.html)
- [Riallto 5.2 — PyTorch→ONNX retrain / quantize](https://riallto.ai/notebooks/5_2_pytorch_onnx_re-train.html)
- [Ryzen AI — Linux installation](https://ryzenai.docs.amd.com/en/latest/linux.html)
- [Ryzen AI — OGA (OnnxRuntime-GenAI) hybrid flow](https://ryzenai.docs.amd.com/en/latest/hybrid_oga.html)
- [Ryzen AI — LLM deployment overview](https://ryzenai.docs.amd.com/en/latest/llm/overview.html)
- [Vitis AI Execution Provider (onnxruntime.ai)](https://onnxruntime.ai/docs/execution-providers/Vitis-AI-ExecutionProvider.html)
- [Lemonade Linux NPU / FastFlowLM (Phoronix)](https://www.phoronix.com/news/AMD-Ryzen-AI-NPUs-Linux-LLMs)
- [Lemonade API — model compatibility & recipes](https://lemonade-server.ai/lemonade_api.html)
- [AMD NPU — Linux kernel docs (amdxdna)](https://docs.kernel.org/accel/amdxdna/amdnpu.html)
