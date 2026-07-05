---
name: hal0-quantize
category: homelab-ops
description: Creating ROCmFPX-family quants (ROCmFP3/4/6/8, straight or agent) from BF16/F16 GGUF sources on hal0 / Strix Halo, using the Hal0ai/Hal0_ROCmFPX runner. Use when asked to quantize a model to ROCmFP4 (or FP3/FP6/FP8), build the ROCmFPX llama.cpp tree for a GPU arch, or produce an agent-coherent quant for tool-calling / JSON / coding workloads. Encodes the clone→build→quantize contract and the straight-vs-agent tensor-routing choice.
---

# hal0 ROCmFPX quantization

Turn a **BF16/F16 GGUF** into a **ROCmFPX-family quant** using the
[`Hal0ai/Hal0_ROCmFPX`](https://github.com/Hal0ai/Hal0_ROCmFPX) runner — the
hal0-branded fork of `charlie12345/ROCmFPX`. It is a llama.cpp fork that adds the
ROCmFP3/FP4/FP6/FP8 block formats plus a straight-vs-agent tensor-routing wrapper.
Its `main` HEAD is the same commit the published `ghcr.io/hal0ai/hal0-rocmfpx:server`
serving image is built from (see the `hal0-rocmfpx-runner` memory).

This skill is a **thin orchestration layer** over that repo's own scripts. It does
not reimplement quant logic — it clones + builds the tree for your GPU arch, then
drives `scripts/quantize-rocmfpx-agent.sh`. We track the repo's **default branch
(latest)** by default — no pinned commit. The published serving image only bundles
`llama-server/bench/cli` (not `llama-quantize`), so quantization builds from source.

> Supersedes the old fork-binary method (`Q4_0_ROCMFP4_STRIX_LEAN`, custom quant
> IDs 100–106, `ghcr.io/hal0ai/amd-strix-halo-toolboxes` toolbox image). That
> path and its read-only-mount `basic_ios::clear` gotcha are retired — this
> pipeline builds natively and writes to a normal host path.

## When to use this skill

- "Quantize `<model>` to ROCmFP4" (or FP3 / FP6 / FP8).
- "Make an agent/coherent quant" for Hermes / OpenClaw / tool-calling / JSON / coding.
- "Build the ROCmFPX llama.cpp tree" for Strix Halo (or another AMD arch).

## The scripts

All under `scripts/` next to this file. Shared config lives in `rocmfpx-env.sh`.

| Script                  | Does |
|-------------------------|------|
| `rocmfpx-pipeline.sh`   | **End-to-end**: ensure the build exists (clone+build if needed), then quantize. Start here. |
| `rocmfpx-build.sh`      | Clone/update `Hal0ai/Hal0_ROCmFPX` (latest) and build the llama.cpp tree for one GPU arch. |
| `rocmfpx-quantize.sh`   | Quantize one `SRC`→`OUT` with an existing build (thin wrapper over the repo's `quantize-rocmfpx-agent.sh`). |

## Quick start (Strix Halo, this box)

One command, BF16 in → ROCmFP4 agent quant out (clones + builds on first run):

```bash
SRC=/mnt/ai-models/foo/foo-BF16.gguf \
OUT=/mnt/ai-models/foo/foo-Q4_0_ROCMFP4_COHERENT.gguf \
FORMAT=rocmfp4 PROFILE=agent \
installer/agent-skills/hal0-quantize/scripts/rocmfpx-pipeline.sh
```

Build once, quantize many:

```bash
ARCH=strix JOBS=16 installer/agent-skills/hal0-quantize/scripts/rocmfpx-build.sh

# then, per model:
SRC=in-BF16.gguf OUT=out-Q8_0_ROCMFPX_AGENT.gguf FORMAT=rocmfp8 PROFILE=agent \
  installer/agent-skills/hal0-quantize/scripts/rocmfpx-quantize.sh
```

## Env contract

Required for quantize/pipeline:
- `SRC` — BF16/F16/F32 GGUF input (split inputs stay split by default).
- `OUT` — output GGUF path (or split-output prefix).

Quant selection (passed straight to the upstream wrapper):
- `FORMAT` = `rocmfp3` | `rocmfp4` | `rocmfp6` | `rocmfp8` (default `rocmfp8`).
- `PROFILE` = `agent` | `straight` (default `agent`).
- `IMATRIX=path` — importance matrix; recommended for low-bit MoE.
- `KEEP_SPLIT=1`, `DRY_RUN=1`, `NTHREADS=N` — as per the upstream wrapper.

Build / arch:
- `ARCH` = `strix` (default) | `rdna2` | `rdna3` | `rdna4` | `gfx906`.
- `JOBS` — parallel build jobs (default `nproc`; README uses 16).
- `ROCMFPX_VARIANT` = `stable` (default) | `experimental` — which tree to build
  (see below). Each variant uses a separate checkout + build dir.
- `ROCMFPX_HOME` — checkout + build location (default `$HOME/ROCmFPX` for stable,
  `$HOME/ROCmFPX-experimental` for experimental).
- `ROCMFPX_REPO` / `ROCMFPX_BRANCH` — override the upstream source. Stable defaults
  to `https://github.com/Hal0ai/Hal0_ROCmFPX.git` @ `main`; experimental to
  `https://github.com/charlie12345/ROCmFPX.git` @ `experimental-rocmfpx-branch`.
- `REBUILD=1` (pipeline) / `FORCE_CLEAN=1` (build) — force a fresh build.

### Stable vs experimental variant

`ROCMFPX_VARIANT=experimental` builds the upstream `charlie12345/ROCmFPX`
`experimental-rocmfpx-branch` (FP3/FP6/turbo3_0/turbo4_0 + MTP edge-case work not
yet in the Hal0ai fork). It lives in its own checkout/build dir, so it coexists
with the stable build — nothing is clobbered.

```bash
# build the experimental tree once
ROCMFPX_VARIANT=experimental ARCH=strix JOBS=16 \
  installer/agent-skills/hal0-quantize/scripts/rocmfpx-build.sh

# quantize with it (end-to-end also works)
ROCMFPX_VARIANT=experimental \
SRC=in-BF16.gguf OUT=out-Q3_0_ROCMFPX_AGENT.gguf FORMAT=rocmfp3 PROFILE=agent \
  installer/agent-skills/hal0-quantize/scripts/rocmfpx-pipeline.sh
```

Pin any commit/branch of either variant with `ROCMFPX_BRANCH=<ref>` (or point
`ROCMFPX_REPO` at another fork entirely).

## straight vs agent (the important choice)

- **straight** — whole model on the family block-format. Smallest/fastest.
- **agent** — keeps the family format but spends more bits on the tensors that
  drive structured behavior (embeddings, attention Q/K/V/O, selected FFN-down,
  selective FFN-gate), preserving JSON shape, tool-call shape, coding, and chat
  coherency. Slightly larger. **Use for agent workloads.**

Full preset table, tensor-routing detail, imatrix notes, and the arch→build-dir
map are in [`references/presets.md`](references/presets.md).

## Source model tips

- If the source is an unsloth `*-GGUF` repo, it usually already ships a **BF16
  GGUF** (often split) — download that and quantize directly; skip the
  safetensors + `convert_hf_to_gguf.py` step.
- Verify the output metadata with the fork's `llama-gguf <file> r` — stock Python
  `gguf` cannot parse the ROCmFPX types.
- Full inference smoke needs the GPU, which is time-shared with live hal0 slots —
  don't casually load a large model.

## Related

- [`hal0-bench`](../hal0-bench/SKILL.md) — benchmark the resulting quant (ROCm vs Vulkan).
- [`hal0-tune`](../hal0-tune/SKILL.md) — tune llama.cpp flags for the quant.
