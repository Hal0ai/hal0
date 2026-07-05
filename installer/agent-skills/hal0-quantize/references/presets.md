# ROCmFPX quant presets & profiles

Source of truth: `Hal0ai/Hal0_ROCmFPX` → `scripts/quantize-rocmfpx-agent.sh`. This page
mirrors its mapping so the agent can choose a preset without re-reading the repo.

## FORMAT × PROFILE → llama-quantize preset

| FORMAT   | PROFILE  | Preset                     | Notes |
|----------|----------|----------------------------|-------|
| rocmfp3  | straight | `Q3_0_ROCMFPX`             | smallest, lowest fidelity |
| rocmfp3  | agent    | `Q3_0_ROCMFPX_AGENT`       | agent-protected FP3 |
| rocmfp4  | straight | `Q4_0_ROCMFP4`             | the headline ROCmFP4 quant |
| rocmfp4  | agent    | `Q4_0_ROCMFP4_COHERENT`    | coherent/agent-protected FP4 |
| rocmfp6  | straight | `Q6_0_ROCMFPX`             | |
| rocmfp6  | agent    | `Q6_0_ROCMFPX_AGENT`       | |
| rocmfp8  | straight | `Q8_0_ROCMFPX`             | highest fidelity family quant |
| rocmfp8  | agent    | `Q8_0_ROCMFPX_AGENT`       | default of the upstream wrapper |

You can also call the binary directly:

```
build-strix-rocmfp4/bin/llama-quantize source.gguf out-q4.gguf Q4_0_ROCMFP4
```

## straight vs agent

**straight** = the whole model on the family block-format.

**agent** = a *tensor-routing* choice. It keeps the ROCmFPX block formats but spends
more bits on the tensors that drive structured behavior, to preserve JSON shape,
tool-call shape, coding behavior, and chat coherency. Agent quants are therefore
slightly larger than straight quants.

The agent profile protects:
- token + output embeddings
- attention Q/K/V/O tensors
- selected FFN-down tensors
- selective FFN-gate tensors
- (bulk FFN-up tensors stay on the family quant where possible)

Use **agent** for Hermes / OpenClaw-style workflows, tool calling, JSON output,
coding, or chat agents. Use **straight** when raw size/speed matters more than
structured-output fidelity.

## imatrix

For low-bit (fp3/fp4) MoE models, pass an importance matrix if the source repo
ships one (or generate with `llama-imatrix`):

```
IMATRIX=/path/to/imatrix.gguf FORMAT=rocmfp4 PROFILE=agent ... rocmfpx-quantize.sh
```

## Arch → build script → build dir

| ARCH   | Hardware                       | build script                     | build dir            |
|--------|--------------------------------|----------------------------------|----------------------|
| strix  | Strix Halo / RDNA3.5 (gfx1151) | `build-strix-rocmfp4-mtp.sh`     | `build-strix-rocmfp4`|
| rdna2  | RX 6000 (gfx1030 class)        | `build-rdna2.sh`                 | `build-rdna2`        |
| rdna3  | RX 7000 (gfx1100 class)        | `build-rdna3.sh`                 | `build-rdna3`        |
| rdna4  | RX 9000 (gfx1200 class)        | `build-rdna4.sh`                 | `build-rdna4`        |
| gfx906 | Vega 7nm / MI50 class          | `build-gfx906.sh`                | `build-gfx906`       |

Strix Halo runtime env (set automatically for `ARCH=strix`):

```
export HSA_OVERRIDE_GFX_VERSION=11.5.1
export GGML_HIP_ENABLE_UNIFIED_MEMORY=1
```
