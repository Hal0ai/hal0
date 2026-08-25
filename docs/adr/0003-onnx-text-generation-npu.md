# ADR-0003: ONNX text-generation via the XDNA2 NPU — defer; FLM remains the NPU lane

## Status

**ACCEPTED (operator, 2026-08-25).** Option C — defer with the trigger
conditions below; FLM remains the sole NPU lane.

Recommendation: **Option C — defer, with explicit trigger conditions.** No code
ships from this ADR. The evidence base is
`docs/research/onnx-npu-linux-2026-08.md` (30 cited primary sources, retrieved
2026-08-20).

Scope note: nothing here gates GA. This is 1.0.x-or-later scope, and it is
deliberately narrower than #1947 (vLLM runtime family) — see "What this ADR
does not decide".

## Context

### The question

Should hal0 add a runtime lane that serves **ONNX text-generation models on
the XDNA2 NPU** (Strix Halo)? The attraction is real: the ONNX Runtime GenAI
(OGA) path would bring bring-your-own-model portability (any supported
architecture, quantized with AMD's flow) and hybrid NPU-prefill/iGPU-decode —
both things the existing NPU lane cannot do.

### What hal0 has today (verified in-tree, 2026-08-20)

- **An NPU lane already exists: FLM (FastFlowLM).** `RuntimeFamily` is the
  closed literal `llama-server | flm | kokoro | qwen3tts | moonshine |
  comfyui` (`src/hal0/profiles/__init__.py:43`), dispatched by
  `_spec_provider_for` (`src/hal0/providers/container.py:1141`).
  `FLMProvider` (`src/hal0/providers/flm.py:336`) runs the trio —
  chat + embedding + transcription multiplexed on one `flm serve` process
  (`profiles/__init__.py:136-137`) — in the `hal0-toolbox-flm` image with
  `/dev/accel/accel0` passthrough, `/v1/models` health, and a one-shot
  `verify_inference` sentinel. The installer pins FLM 0.9.44 per-distro .debs
  with SHA-256 (`installer/install.sh:2129-2166`) and — notably — already
  sources the NPU userspace (`libxrt-npu2`) from the **lemonade-team PPA**
  (`installer/install.sh:2213-2230`). A seeded `flm.toml` slot ships with
  `device="npu"` (`installer/etc-hal0/slots/flm.toml`).
- **ONNX runs in auxiliary lanes only, always CPU EP:** Kokoro TTS
  (`providers/kokoro.py`), Moonshine STT multi-file bundles
  (`providers/moonshine.py`), and the catalog is explicit that "the kokoro
  CPU engine ships with onnxruntime CPU EP only (no Vulkan/ROCm EP)"
  (`capabilities/catalog.py:387`). The realtime VAD deliberately avoided
  onnxruntime as a heavyweight dependency (`realtime/vad.py`).
- **`.onnx` is classified non-text by design:** `_NONTEXT_MODEL_SUFFIXES`
  (`src/hal0/registry/fallback.py:79`) exists so the llama-server/FLM text
  providers are never handed a file they cannot serve. Any ONNX text-gen lane
  would have to unwind that assumption; this ADR concludes it should stand.
- **No RyzenAI/VitisAI/onnxruntime-genai references exist anywhere in the
  tree.** This would be a from-scratch lane, not an extension.
- **Hardware is ready.** Read-only probe of ct150 (10.0.1.150, 2026-08-20):
  `/dev/accel0` present, `amdxdna` 0.7.0 loaded, firmware
  `amdnpu/17f0_11/npu_7.sbin`, kernel 7.0.6-2-pve, `libxrt-npu2` 2.21.75
  installed, FLM v0.9.44 answering. The kernel/firmware/device layer is not
  the problem.

### What the world has today (external, cited)

The full survey with sources is `docs/research/onnx-npu-linux-2026-08.md`.
The load-bearing findings:

1. **The ONNX-on-NPU stack is Windows-only, per AMD's own documentation.**
   Ryzen AI Software 1.8.0 installs on Windows only; the ONNX Runtime
   Vitis AI EP support table lists Windows as the sole supported OS for the
   Ryzen AI family; upstream `onnxruntime-genai` has **no** VitisAI/RyzenAI
   hardware-acceleration entry at all; Lemonade's FAQ states "Ryzen AI SW's
   implementation of NPU and hybrid inference is currently supported only on
   Windows."
2. **Forcing it on Linux fails concretely, not theoretically.** Field reports
   (amd/RyzenAI-SW #341, #178) show a Linux-built Vitis AI EP initializing
   and then partitioning **zero** operators to the NPU — the `voe.passes`
   graph-optimization wheels are not published for `linux_x86_64` — and the
   prebuilt Python wheels segfault on C++11 ABI mismatch. Those reports are
   plain CNNs; LLM text-gen is strictly further out.
3. **Strix Halo is doubly excluded.** The OGA flow supports "Strix and
   Krackan Point processors" — Strix *Halo* is not on the list, and the
   Ryzen AI SDK installer rejects the STX-H SKU outright
   (amd/RyzenAI-SW #366, open).
4. **The one thing that does serve LLMs on the Linux XDNA2 NPU is FLM** —
   proprietary NPU kernels, FLM-native model format, not ONNX. That is
   exactly the lane hal0 already ships.
5. **Hybrid NPU-prefill/iGPU-decode is an OGA (Windows-only) feature.** The
   Linux "equivalent" is third-party speculative-decoding glue with no shared
   KV cache — prior art, not a supported mode.
6. **The NPU's realistic niche on Strix Halo is not peak throughput.**
   Independent head-to-head: Qwen3-8B on NPU decodes ~8 t/s vs ~20 t/s for
   the same class on the 8060S iGPU — but TTFT is 2.3× better on the NPU and
   power is single-digit watts vs ~25 W. The NPU wins as a low-power,
   always-on second accelerator (background summarization, classification,
   STT, embeddings) that doesn't steal iGPU cycles. FLM's trio already
   occupies precisely that niche.

One in-repo hygiene check came out clean: hal0's Strix Halo kernel-args guide
recommends `amd_iommu=on iommu=pt` (`docs/getting-started/drivers.mdx:102`),
which is NPU-safe. The community-common `amd_iommu=off` tweak silently
removes `/dev/accel` entirely; hal0 does not recommend it anywhere.

## Options considered

**Option A — new `onnx-genai` runtime family now.** Extend `RuntimeFamily`,
add a provider, registry `.onnx` text-gen suffix handling, bundle-fileset
pulls, capacity semantics, #1922 gate integration. **Reject.** There is no
runtime to wrap: the EP does not offload a single operator on Linux, the
wheels do not exist for `linux_x86_64`, and the SDK refuses the only SKU hal0
targets. Every hour spent here today produces a lane that serves nothing.
This is not a close call and no spike is warranted — the failure is
documented upstream by AMD, not hypothesized.

**Option B — integrate Lemonade (lemonade-server) as a managed local
upstream**, the #1947-shape-A move, hoping to inherit its NPU support.
**Reject.** Lemonade's own support matrix answers this: on Linux its
`ryzenai-llm | npu` (the ONNX path) row is Windows-only; its Linux NPU path
*is* FLM — the runtime hal0 already integrates natively, one layer lower and
with its own slot lifecycle, health, and installer pinning. Its remaining
Linux engines (llama.cpp Vulkan/ROCm/CPU, experimental vLLM) duplicate hal0's
main lane or belong to the #1947 decision. Adding Lemonade would add a
dependency layer and zero new capability.

**Option C — defer, with explicit trigger conditions.** Keep FLM as the NPU
lane. Keep `.onnx` in `_NONTEXT_MODEL_SUFFIXES`. Record precisely what would
reopen the question and what the integration would look like when it fires.
**Recommend.** The research note's "honest outcome" clause anticipated this:
when the platform does not exist, the correct ADR result is a documented
defer, not a speculative lane.

## Decision (proposed)

**Adopt Option C.** Concretely:

1. **No implementation.** No new runtime family, no registry or pull changes,
   no capability, no curated rows. `.onnx` remains a non-text suffix in
   `src/hal0/registry/fallback.py` — that classification is now *evidenced*,
   not just assumed, and this ADR is the citation.
2. **FLM remains hal0's NPU answer**, including for the background-workload
   niche the NPU is actually good at. Model-coverage gaps on the NPU are FLM
   catalog requests, not grounds for a parallel ONNX lane.
3. **The revisit contract is the trigger list** in
   `docs/research/onnx-npu-linux-2026-08.md` ("Trigger conditions to
   revisit", items 1–9). The single cheapest signal to poll: the
   `ryzenai-llm | npu` row in Lemonade's Supported Configurations table
   (github.com/lemonade-sdk/lemonade) flipping from "Windows" to
   "Windows, Linux". Second: Strix Halo appearing on the OGA
   supported-processor list (track amd/RyzenAI-SW #366). Checking both is a
   minute of reading per release cycle; no automation is proposed.
4. **If a trigger fires**, the follow-up ADR inherits this integration
   checklist as its scope skeleton (future work, explicitly *not* scope
   today): registry `.onnx` text-gen suffix handling; pull-layer ONNX bundle
   filesets (the Moonshine/Kokoro multi-file problem, ADR-0001 §3, becomes
   load-bearing for text-gen); a new `RuntimeFamily` member and provider;
   capability naming vs the existing `npu` backend; NPU capacity semantics
   (today's footprint-GB model has no real booking — `api/routes/npu.py`,
   `model_fit.py`); #1922 output-sanity gate compliance (mandatory for any
   new text-gen lane); curated rows only after on-box validation (#1891
   lesson); quant metadata on pull-registered models (#1890 lesson).

### What this ADR does not decide

- **#1947 (CIRU vLLM runtime family)** — the sibling decision. It shares the
  general question "when does hal0 add a runtime family" but its candidate
  runtime *works on Linux today*, which is why it gets a real
  managed-upstream-vs-family evaluation and this one does not. The two ADRs
  should not be merged: the general framework is worth writing only when a
  second *viable* family forces it.
- **FLM lane evolution** — licensing posture (proprietary kernels, commercial
  terms; the 08/2026 move into AMD's ROCm org may change this — trigger 8),
  model coverage, or whether the trio splits. Separate concern, no ADR needed
  yet.
- **#1922 gate design** — this ADR only binds any *future* NPU text-gen lane
  to pass it.
- **Windows support** — hal0 is Linux-only; that boundary is not revisited
  here.

## Consequences

**If accepted:** hal0's NPU story stays single-lane (FLM) with a written,
cited justification for why the obvious-sounding "just serve ONNX on the
NPU" is not being built. The next person who proposes it inherits the
evidence and the trigger list instead of re-running the investigation. Cost
of being wrong is one release cycle of lag behind a Lemonade/RyzenAI Linux
announcement — bounded and cheap, given the triggers are named and pollable.

**If rejected in favour of building now (Option A):** the work items in
Decision §4 become scope, plus an unbounded item nobody can schedule:
making AMD's Windows-only userspace exist on Linux. The empirical floor —
zero operators offloaded, no `linux_x86_64` wheels, SKU check rejecting
Strix Halo — is not something hal0 code can fix.

## Open questions for the operator

1. Is the FLM proprietary-kernel licensing (free non-commercial, commercial
   terms apply) acceptable for hal0's distribution posture long-term? This is
   orthogonal to the ONNX question but it is the standing risk in having FLM
   as the *only* NPU lane. Trigger 7 (an open-source XDNA2 LLM backend, e.g.
   via `iree-amd-aie`/`mlir-aie` or a llama.cpp XDNA backend) is the exit.
2. Should the trigger check be attached to an existing recurring ritual
   (release-cut checklist?) or left ad hoc? This ADR proposes ad hoc to avoid
   ritual creep; the operator may prefer otherwise.

## References

- `docs/research/onnx-npu-linux-2026-08.md` — full evidence base, 30 cited
  sources, blockers, trigger conditions.
- `src/hal0/profiles/__init__.py:43,106-144` — `RuntimeFamily`, family
  classifier, FLM slot types.
- `src/hal0/providers/flm.py:336-694` — the incumbent NPU provider.
- `src/hal0/providers/container.py:1141` — `_spec_provider_for` dispatch.
- `src/hal0/registry/fallback.py:79` — `.onnx` in `_NONTEXT_MODEL_SUFFIXES`.
- `installer/install.sh:2129-2312` — FLM .deb pinning, lemonade-team PPA
  (`libxrt-npu2`), `flm validate` smoke test.
- `docs/getting-started/drivers.mdx:102` — NPU-safe `amd_iommu=on iommu=pt`
  guidance.
- ADR-0001 — Moonshine: the multi-file ONNX bundle problem and the
  device-keyed engine rule this lane would have interacted with.
- Issues: #1947 (vLLM family), #1946 (PromptForge), #1922 (output-sanity
  gate), #1891 (validate-before-curate), #1890 (quant metadata), #1790
  (quant-vs-runner guard).
- amd/RyzenAI-SW#341, #366; amd/xdna-driver#1219;
  github.com/lemonade-sdk/lemonade; github.com/ROCm/FastFlowLM.
