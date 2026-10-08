# ADR-0025: Upstream llama.cpp is the default runner; the forks are frozen

## Status

**PROPOSED (2026-10-08).** Target: the next minor after v1.4.0. Owner
decision of 2026-10-08: this round of runner work is not bound by what was
built before; current model availability, popularity, stability and
maintainability decide. Nothing here lands in hal0 before the hardware
gate in `Hal0ai/hal0-runner-images` `runners/upstream/manifest.toml`
`[verification]` passes (Hal0ai/hal0-runner-images#14).

## Context

### Every fork hal0 built from has the same shape

The 2026-10-08 review of the sources behind hal0's runner images:

- `charlie12345/ROCmFPX` (the `rocmfpx` default, `hal0-combined:0826`):
  a squashed June snapshot of llama.cpp with no upstream merge since;
  ROCmFP4 refused upstream (ggml-org #24185); no `qwen4exp`
  (Qwen3.8-Flash-Next) six weeks after upstream merged it; maintainer
  activity 118 → 18 → 0 commits per month.
- `ciru-ai` (the `promptforge` runner): one maintainer, source published
  as Hugging Face tarballs, a custom ROCr, reproduced only on NixOS/Arch,
  and the PromptForge v2.3 model the runner exists for is already
  superseded by its author.
- `myhacsint` (the `strix` runner's source): dead since 2026-09-01; the
  live Vulkan line moved to `halo-box/strix-llama.cpp`.

Meanwhile the model support that mattered arrived upstream first: qwen35
MTP (May), `qwen4exp` (ggml-org #27941, Sep 1), `qwen4exp` NextN/MTP
(#29761, Oct 1), lazy tensor read (#27794). At release b11510 upstream also
carries the minicpm5 pretokenizer and a dedicated LFM2 parser, i.e. two of
the three fork patches the brain family depended on
(`runners/rocmfpx/patches/0001`, `0002`).

### The default model is fork-bound

`qwen3.6-27b` (the curated row tagged `default`) is
`plunderstruck/Qwen3.6-27B-MTP-ROCmFP4-GGUF`, loadable only by the fork.
The install-time primary ladder (`src/hal0/hardware/recommend.py`
`_PRIMARY_TIERS`), the seed stacks (`config/data/seed_stacks.toml`) and the
agent ladder (`src/hal0/install/agent_model.py`, three chadrock/Ace-Saber
ROCmFP4 builds) are likewise FPX-bound. The owner requires Qwen3.8 support;
no fork has it for the dense 27B and only upstream has it with MTP for
Flash-Next.

### ROCm 10.0 is a stable source now

`repo.radeon.com` stops at 7.2.4 for rhel10. AMD ships ROCm 10.x as
per-GPU packages (`amdrocm-*10.0-gfx1151`) on `stable.repo.amd.com`, a
versioned, signed repository. The new recipe pins the exact NEVR
(`10.0.0-4`) and Fedora 44 by digest.

## Decision (proposed)

1. **The default runner is `upstream`.** A new `RUNNER_IMAGES["upstream"]`
   row: pristine ggml-org llama.cpp at a tagged release, HIP + Vulkan
   (`supported_backends=("rocm", "vulkan")`), `is_default=True`, title
   **Standard**. `runner_for_backend()` returns it for every non-cuda,
   non-cpu device. Its image is a CI-built immutable tag from
   `Hal0ai/hal0-runner-images` (`ghcr.io/hal0ai/hal0-runner-upstream:
   upstream-<ref7>-r<sha7>`), pinned with its digest after the gate.
2. **`rocmfpx` is frozen, not deleted.** It keeps its row, its image
   (`hal0-combined:0826`), both backends and `FPX_RUNNER_KEYS` membership,
   loses `is_default`, and is retitled **ROCmFPX (frozen)**. It exists
   only to load ROCmFPX / ROCmI4 / IU4 GGUFs already on slots. No bumps.
   Deleted in the release after the last FPX slot migrates.
   This amends ADR-0006 decision 3: the two-backend shape belongs to the
   default runner **and** to the frozen FPX loader, by name; every optional
   runner still declares one backend.
3. **Slots on FPX models stay on `rocmfpx` by an explicit `binary`.** A
   boot/upgrade migration (the `clear_stale_mtp_overrides` pattern:
   resolve each slot's model in the registry, read its quant) stamps
   `binary = "rocmfpx"` on every binary-less slot whose model quant starts
   with `ROCmFP`, before `runner_for_backend` changes. Idempotent, dry-run
   first, surfaced on the dashboard. Without this the #1790 guard
   (`providers/container.py:_guard_fpx_quant_runner`) turns every such
   slot into a 422 at launch.
4. **The default model is `unsloth/Qwen3.8-27B-GGUF`.** New curated row
   `qwen3.8-27b`: `UD-Q4_K_XL` (16.4 GiB) + `mmproj-F16.gguf`,
   `architecture="qwen35"`, tags `chat, vision, tool-calling, mtp,
   default`. The MTP head is in the GGUF (`qwen35.nextn_predict_layers =
   1`, `blk.64.nextn.*`), so the existing `--spec-type draft-mtp` bundle
   applies with no `-md` plumbing. Shipped default context is set by the
   gate (target 64K–128K; 262K is the model's ceiling, not a product
   requirement). It becomes the top GPU rung of `_PRIMARY_TIERS` and the
   first FirstRun pick; `qwen3.6-27b` loses the `default` tag and stays
   as a migration source.
5. **Qwen3.8-Flash-Next is supported, not default.** Curated row
   `qwen3.8-flash-next`: `unsloth/Qwen3.8-Flash-Next-GGUF` `UD-Q4_K_XL`
   (~111 GB) + `MTP/mtp-Qwen3.8-Flash-Next-shared-Q8_0.gguf` + mmproj,
   `vram_gb_min` set so only a 128 GB box sees it. It needs a draft-model
   sidecar: `CuratedModel` gains `draft_file` (pulled like `mmproj_file`,
   emitted as `--spec-draft-model`), the one piece of new launch plumbing
   in this change.
6. **Agent and seed-stack models move off the forks.** The agent ladder
   and the saber/pi stacks point at plain GGUFs (Qwen3.8-27B first) once
   the gate confirms native tool calling on `upstream`; until then they
   carry `binary = "rocmfpx"` explicitly rather than inheriting the
   default.
7. **`promptforge` is removed from hal0** (row, `DEFAULT_PROMPTFORGE_IMAGE`,
   manifest key, legacy seed profile, specialty kind, bench lane, UI
   option, docs). The GHCR package stays (retention allowlist); the recipe
   is deleted from `hal0-runner-images` after this lands (its
   `docs/BUMPING.md` order).
8. **`strix` is decided by the gate.** Kept as an opt-in Vulkan runner
   re-pinned to `halo-box/strix-llama.cpp` only if its decode on the
   default model beats `upstream`'s Vulkan lane by more than 10%;
   otherwise removed with `promptforge`.
9. **Vulkan-lane admission stays evidence-gated.** `VULKAN_CAPABLE_IMAGE_REFS`
   gains the new digest only with the gate report, exactly as `:0826` and
   `strix:0831` earned theirs. `installer/lib/preflight.sh` reads the new
   constant name.

## Options considered

- **Keep `rocmfpx` as default and pin the upstream image per slot** (the
  #2118 shape). Rejected: ADR-0006 retired pin-only shipping, and it keeps
  the default on a dead fork.
- **Swap the image under the `rocmfpx` key.** Smallest diff. Rejected: the
  key means "loads FPX quants" to the #1790 guard and to every slot that
  relies on it; the new image cannot, so the key would lie.
- **A third runner for CIRU's IU4 Qwen3.8 (`jcbtc/Qwen3.8-Flash-CIRU-STRIX-IU4`)**.
  Rejected: needs a custom ROCr, scores below the HaloBox quant on its own
  quality panel, and its speed is matched by the halo-box Vulkan line.
- **Default on Flash-Next (110+ GB).** Rejected by the owner: too large
  for a default on a 128 GB box shared with the brain, TTS and STT slots.

## Consequences

- One hal0 PR per decision group, in this order: curated rows + `draft_file`
  (no runner change); the `upstream` row, `runner_for_backend`, the FPX
  migration and `promptforge` removal; docs and UI. Each cites the gate
  digest and run.
- Tests that pin `rocmfpx` as the default key
  (`tests/runners/test_registry.py` `test_exactly_one_default_gpu_runtime`,
  `test_single_backend_invariant`, `test_runner_for_backend_*`;
  `ui/src/dash/__tests__/hw-cascade.test.ts`) and the UI mirror of
  `runner_for_backend` (`ui/src/dash/hw-cascade.js`) change together.
- `exports/runner-image-pins.json` and the runner-images retention
  allowlist gain the new digest; the `promptforge` pin leaves the export
  but not the allowlist.
- The brain model (`lfm2.5-2.6b`) and the minicpm5 brain family either
  move to `upstream` (gate probes for hal0 #2056 and native tool calls
  pass) or keep `binary = "rocmfpx"` under decision 3; the gate decides,
  not this record.
- `STALE_ROCMFPX_IMAGE_REFS` does **not** gain `:0826` while any FPX slot
  exists: the retag sweep would move FPX slots onto an image that cannot
  load them.
- A fork's `track_ref` in the weekly fork watch keeps reporting; "N new"
  on a frozen runner is information, never a bump.

## Open questions for the operator

1. Agent-lane model: Qwen3.8-27B for Hermes and pi coder as well, or keep
   the chadrock ladder on the frozen runner for one more release? The
   gate's tool-calling probe informs this; the call is the owner's.
2. Default context for `qwen3.8-27b`: 64K (safe with the brain + voice
   slots resident) or 128K (fit-tested)?

## References

- ADR-0006 (every shipped image is a registry runner; decision 3 amended)
- Hal0ai/hal0-runner-images#14 (`runners/upstream` recipe, gate probes)
- Hal0ai/hal0-runner-images `docs/CONSOLIDATION.md` Phase 3
- `docs/guides/manage-runner-images.mdx`, `docs/concepts/strix-halo.mdx`
