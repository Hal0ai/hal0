# Hal0 Brain ROCmFPX GGUF Repository Design

## Goal

Publish a public, consolidated Hugging Face repository at
`Hal0ai/hal0-brain-sft-ROCmFPX-GGUF` containing the verified F16 model and two
agent-oriented ROCmFPX quantizations. Repoint hal0's ROCmFPX brain catalog entry
to the new Q8 Agent artifact while continuing to reject stock runners for the
custom format.

## Source artifact

Use the locally verified F16 GGUF:

- Path: `/mnt/ai-models/chat/hal0-brain-sft/model.gguf`
- Size: `2166552096` bytes
- SHA-256: `ed9d28c4eac1d7c291bc80d9410c243a3d28e655921ccaf90f2b6619aa24d2c3`
- Tensor types: 170 `F16`, 49 `F32`
- Existing immutable HF source revision:
  `Hal0ai/hal0-brain-sft-GGUF@6b190df6e816cc806f7fa7ae3de7248f5551e00b`

The source file is copied without modifying its GGUF contents and published as
`hal0-brain-sft-F16.gguf`.

## Published artifacts

The new repository contains exactly these model files:

1. `hal0-brain-sft-F16.gguf`
2. `hal0-brain-sft-Q4_0_ROCMFP4_COHERENT.gguf`
3. `hal0-brain-sft-Q8_0_ROCMFPX_AGENT.gguf`

It also contains these user-facing assets:

- `chat-long-context.hal0profile.json` — the checksummed portable hal0 profile
  supplied at `/home/mint/Downloads/chat-long-context.hal0profile.json`.
- `hal0-brain-banner.png` — a derived copy of
  `/home/mint/Downloads/Gemini_Generated_Image_vi6t5bvi6t5bvi6t.png`, retaining
  the original artwork and adding the approved heading copy:
  - `HAL0 BRAIN`
  - `Advanced reasoning · Tool calling · Platform management`
  - `Your mini-agent administrator—trained on hal0 inside and out`

The original source image remains untouched.

The Q4 and Q8 files are generated independently from the verified F16 source,
not from an existing quantized file. The presets are:

- Q4 Agent: `Q4_0_ROCMFP4_COHERENT`
- Q8 Agent: `Q8_0_ROCMFPX_AGENT`

Quantization uses the authoritative `charlie12345/ROCmFPX` implementation pinned
to commit `61f2f2d7bc4955e9bca821095ef69125837133b5`, unless validation demonstrates
that this revision cannot load the source model. In that case publication stops
until a compatible, explicitly pinned ROCmFPX revision or runner is identified;
the model metadata must not be altered merely to bypass a loader error.

## Model card

The card must include:

- Apache-2.0 license and source-model relationship.
- Exact filenames, byte sizes, SHA-256 digests, GGUF `general.file_type` values,
  and observed tensor-type counts.
- The source model repository and immutable revision.
- The ROCmFPX repository and immutable quantizer revision.
- A compatibility matrix distinguishing F16 from custom ROCmFPX files.
- An explicit statement that the Q4 and Q8 custom tensors require a
  ROCmFPX-capable runner and are unsupported by stock llama.cpp.
- ROCmFPX CLI/server examples using the exact published filenames.
- Direct `hf download` commands for each GGUF and the portable profile.
- hal0 profile instructions covering dashboard import and the exact REST API
  dry-run/commit flow for `POST /api/profiles/import`.
- The profile's intended long-context use and its material runtime flags,
  including Q8 K/V cache, flash attention, no mmap, and disabled context shift.
- A warning that importing a profile with an existing name returns a collision;
  users should dry-run before committing or choose a different import name.
- A warning not to infer tensor formats from filenames; the documented values
  come from ROCmFPX-aware GGUF inspection.
- `hal0-brain-banner.png` as the heading image, with useful alt text that repeats
  the product identity without describing irrelevant visual details.

## Validation and publication flow

1. Verify the F16 source size, digest, metadata, and tensor types.
2. Quantize Q4 and Q8 independently from F16.
3. Inspect both outputs with ROCmFPX-aware `GGUFReader` tooling.
4. Confirm the requested file-type preset and the presence of the expected
   custom tensor type in each output.
5. Load each quantized output with the catalog-declared ROCmFPX-compatible
   runner. A metadata-only parse is not sufficient.
6. Confirm a stock llama.cpp runner rejects both custom artifacts. If a current
   stock implementation recognizes either custom type, update the compatibility
   statement from observed evidence rather than assumption.
7. Verify the portable profile envelope kind, schema version, content checksum,
   and dry-run import behavior without mutating the active profile catalog.
8. Produce `hal0-brain-banner.png`; verify dimensions, legibility, and that the
   original source image is byte-for-byte unchanged.
9. Compute final byte sizes and SHA-256 digests.
10. Create the public HF repository and publish the card, three GGUFs, portable
    profile, and banner image.
11. Resolve and record the resulting immutable HF commit revision.
12. Download or remotely inspect the published artifacts at that revision and
    recheck filenames, sizes, digests, profile checksum, and tensor types.

No artifact is published if ROCmFPX load validation fails.

## Hal0 catalog update

Update `src/hal0/lifecycle/data/models.toml` so
`hal0-brain-rocmfpx-agent` references:

- Source: `Hal0ai/hal0-brain-sft-ROCmFPX-GGUF`
- Revision: the final immutable HF commit
- Filename: `hal0-brain-sft-Q8_0_ROCMFPX_AGENT.gguf`
- Size and SHA-256: values verified after publication
- Format: `rocmfpx-gguf`
- Quantization: `fpx8-agent`
- Runners: `rocmfpx` and `vulkanfpx` only

Regenerate `src/hal0/lifecycle/data/catalog.json`. Existing catalog validation
must continue to reject pairing this model with `vulkan`, `cpu`, or `cuda`
stock runners.

## Error handling and boundaries

- Do not overwrite or delete the existing misleading FP8-only HF repository.
- Do not alter source GGUF metadata to work around loader incompatibility.
- Do not publish partial uploads as the documented release. If an upload fails,
  complete or replace it with a new immutable commit before updating the card
  and catalog.
- Do not update the catalog until the final published revision and digest have
  been independently verified.
- Keep generated model files and derived card artwork outside the git
  repository; only the design/catalog changes belong in hal0 git.
- Treat the provided profile JSON as a portable template, not a model-specific
  guarantee: operators may need a different profile if their hardware cannot
  sustain its batch, unified-batch, or long-context settings.

## Completion evidence

Completion requires:

- Public HF repository URL and immutable revision.
- Verified tensor-type summaries, sizes, and SHA-256 digests for all model
  files, plus a valid checksummed portable profile and legible heading image.
- Successful ROCmFPX runner load evidence for Q4 and Q8.
- Stock-runner rejection evidence for Q4 and Q8.
- Passing lifecycle catalog tests, compiler check, and local release catalog
  gate.
- A git diff limited to the approved design/catalog-generated changes and any
  directly required tests.
