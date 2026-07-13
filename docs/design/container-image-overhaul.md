# Container Image System Overhaul

Status: proposed · Branch: `feat/container-image-overhaul` · Owner: platform

## Problem

hal0's runtime container image for each slot is chosen **inconsistently** and the
shipped default is **stale/wrong**, so fresh installs come up on old toolbox
images while the real, working runner (`hal0-rocmfpx`) only exists as a
**local one-off** on hand-tuned boxes.

Concretely (verified 2026-07-12):

1. **Fragmented resolution.** `image_ref()` is implemented by `comfyui`, `flm`,
   `kokoro`, `qwen3tts` (each with its own `HAL0_TOOLBOX_IMAGE_<X>` env +
   `_DEFAULT_<X>` constant; only comfyui consults `manifest_image_ref`).
   **`llama_server` has no `image_ref`** — the LLM/embed/rerank lanes read the
   *profile's literal `image`* from `config/schema.py` seed profiles or the slot
   TOML. So the manifest digest-pin never reaches the LLM lanes.
2. **Seed ≠ manifest.** Seed profiles hardcode
   `amd-strix-halo-toolboxes:{vulkan-radv-server, rocm-7.2.4-rocmfp4-server}`;
   `manifest.json.toolbox_images` names `hal0-toolbox-{vulkan,rocm}:v1`. Neither
   is the unified `hal0-rocmfpx` runner the platform actually wants.
3. **No propagation.** New defaults never reach existing users' slots except by
   hand-editing each slot TOML (`image = ghcr.io/hal0ai/hal0-rocmfpx:c077206`) —
   the current "FPX gotcha".

## The unified runner: `hal0-rocmfpx`

`ghcr.io/hal0ai/hal0-rocmfpx:c077206` is **one image serving all GPU LLM lanes**:
live slots run it for `vulkan` (agent/utility/ops) *and* `rocm` (code) backends,
*and* it adds FP4/FPX support. It supersedes **both** legacy toolboxes and
removes the per-slot FPX hand-add.

## Decisions (locked)

- **`hal0-rocmfpx` is the shipped platform default** for GPU LLM/embed/rerank
  lanes — in `manifest.json` + seed profiles, **not** a per-box `profiles.toml`
  override. The local one-off is promoted into the codebase.
- **Universal default (NOT hardware-gated).** `hal0-rocmfpx` is the default for
  every AMD GPU lane. It ships `mesa-vulkan-drivers` + a compiled Vulkan/RADV
  backend (the live Strix slots already run it via `-dev Vulkan0`), so it runs on
  any AMD GPU — the `CMAKE_HIP_ARCHITECTURES=gfx1151` pin only constrains the HIP
  kernels, not the portable Vulkan path. Carve-outs: CUDA → `llama.cpp:server-cuda`,
  CPU-only → the lean vulkan toolbox (a 7.5 GB ROCm image is wasteful for CPU).
  _(Superseded the earlier "gfx1151 HW-gate" idea — verified the image is
  Vulkan-portable, so gating added complexity + a hot-path `hardware.json` read
  for no benefit.)_
- **Rolling tag + digest pin.** Publish a rolling `hal0-rocmfpx:stable` tag;
  `manifest.json` stores it resolved to an immutable `@sha256` digest via
  `scripts/update-toolbox-digests.sh`. Bump = move tag → re-run script → release.
- **`hal0 update` reconciles existing slots** onto the new default image via the
  existing slot-drift + reconcile seam (#1138), not just fresh installs.

## Design

### 1. One resolver (`config/loader.py`)

Add `resolve_slot_image(*, backend, device_class, slot_cfg, manifest=None)` as the
single decision point, with strict precedence:

```
per-slot [model].image (explicit operator pin)
  → HAL0_TOOLBOX_IMAGE_<KEY>            (dev/test override)
  → manifest_image_ref(<key>)          (release-pinned tag@digest)  ← authoritative default
  → seed-profile fallback tag          (last resort; warns)
```

`<key>` comes from the profile/backend, not a hardcoded per-provider constant.
`base.image_ref` gains a concrete default that calls this; `llama_server` stops
reading the literal profile image and calls `image_ref` like every other
provider. The 4 special providers drop their bespoke constants and pass their
manifest key through the same resolver.

### 2. Manifest keys (`manifest.json`)

Add the unified runner and a portability fallback; keep genuinely-distinct images
separate:

```jsonc
"toolbox_images": {
  "rocmfpx":  {"tag": "ghcr.io/hal0ai/hal0-rocmfpx:stable",                    "digest": "sha256:…"},
  "vulkan":   {"tag": "ghcr.io/hal0ai/amd-strix-halo-toolboxes:vulkan-radv-server", "digest": "sha256:…"},  // fallback lane
  "flm":      {…}, "kokoro": {…}, "qwen3tts": {…}, "comfyui": {…}
}
```

Seed profiles reference the manifest **key** (or leave `image` empty and let the
resolver fill it) instead of hardcoding a registry tag.

### 3. Default resolver (universal — no hardware probe)

`resolve_default_image(backend, device_class)` (`config/schema.py`) is a pure,
deterministic map: CUDA → the cuda image, CPU-only → the lean vulkan toolbox,
every other (AMD GPU) lane → `hal0-rocmfpx`. No `hardware.json` read, so nothing
on the hot render path and no test host-dependence. _(As-built. An earlier draft
gated this on a gfx1151 probe; dropped once the rocmfpx image was confirmed
Vulkan-portable — see Decisions.)_

### 4. Update propagation (`cli/update_commands.py`)

The seam exists: `_fetch_slot_drift()` / `_print_drift_banner()` /
`_restart_drifted_slots()` + `hal0 update --restart-slots`, plus the shared
install/update slot-render reconcile seam (#1138). Changes:
- Extend the drift definition to include **image drift**: a slot whose resolved
  image ≠ the manifest-pinned default for its lane (the `image_mismatch` signal
  already surfaced in `slot_status`).
- On `hal0 update` (and `--restart-slots`), the reconcile seam regenerates the
  unit with the new image and reloads the slot — gated by the operator-approval
  flow for anything destructive.
- Operator-pinned slots (explicit `[model].image`) are **left alone** — image
  drift only auto-reconciles lane-default slots.

### 5. Versioning / CI

- `scripts/update-toolbox-digests.sh` already resolves tag→digest for every
  `toolbox_images` entry; adding `rocmfpx` makes it tracked automatically.
- `.github/workflows/toolbox.yml` must build/push `hal0-rocmfpx:stable` (or mirror
  it under `ghcr.io/hal0ai/`) so `release.yml`'s null-digest gate passes.
- `doctor` already probes `toolbox_images` reachability — extend to flag drift
  (installed slot image ≠ manifest pin), not just reachability.

## Rollout / risks

- **Size:** rocmfpx ≈ 7.5 GB vs 1.98 GB for vulkan-radv — a bigger pull for every
  AMD GPU host (accepted: it's the unified runner). Only CUDA / CPU-only hosts stay
  on a leaner image.
- **FPX quant off gfx1151:** standard GGUF quants run everywhere via Vulkan; the
  FPX-specific quant path was tuned on gfx1151, so "unvalidated, not unsupported"
  elsewhere (non-Strix users typically don't run FPX weights).
- **Back-compat:** old tags stay resolvable; existing slot TOMLs with explicit
  `image` are never rewritten.
- **Custom `profiles.toml`** (hand-tuned boxes) keep overriding by design; those
  images are operator pins and out of scope for auto-reconcile.

## Addendum — current-state findings (narrows scope)

Investigation showed most of the machinery **already exists and is wired**, so this
is "finish the half-done migration", not a greenfield build:

- **`DEFAULT_ROCMFPX_IMAGE = ghcr.io/hal0ai/hal0-rocmfpx:c077206`** is already the
  canonical constant (`config/schema.py:852`); FPX seed profiles already use it.
- **`_resolve_image_ref`** (`providers/container.py:120`) already falls through
  `slot.image → profile.image → DEFAULT_ROCMFPX_IMAGE`.
- **`retag_stale_slot_images()`** (`updater/updater.py:1190`) already migrates slot
  TOML + custom-profile `image` pins that match `STALE_ROCMFPX_IMAGE_REFS` →
  `DEFAULT_ROCMFPX_IMAGE`, preserves operator pins, is idempotent, and **is already
  called in the update apply path** (`updater.py:1684`, `:1918`). The updater also
  mirrors the manifest `toolbox_images` block (`updater.py:224`). Tested by
  `tests/updater/test_image_retag.py`, `tests/providers/test_image_resolution.py`.

### The remaining gaps

**Consumer side — DONE (PR #1297):**
1. ✅ **Basic seed profiles no longer hardcode the old toolbox.** `rocm`, `vulkan`,
   `rocm-longctx`, `embed`, `rerank`, `vulkan-embed`, `vulkan-rerank`, `cpu-llm`
   blank their `image` and defer to `resolve_default_image` (`ProfileConfig` now
   permits an empty image = "defer").
2. ✅ **`STALE_ROCMFPX_IMAGE_REFS` gained the two old toolbox refs** so `hal0 update`'s
   retag migrates existing slots off them.
3. ✅ **Universal default (no HW gate).** `resolve_default_image` maps every AMD GPU
   lane → rocmfpx, CUDA → cuda, CPU-only → lean toolbox — deterministic, probe-free.
   (Confirmed rocmfpx serves `--embedding`/`--reranking` and is Vulkan-portable, so
   no gfx1151 gate.) The retag resolves its target through the same function.

**Producer side — TODO (see `toolbox-repo-consolidation.md`):**
4. **Versioning.** `DEFAULT_ROCMFPX_IMAGE` is still a raw sha (`c077206`). Move to a
   rolling `:rocmfpx-stable` + `manifest.json` digest pin via `update-toolbox-digests.sh`;
   publish it from `Hal0_ROCmFPX`'s inherited CI so `release.yml`'s null-digest gate
   passes. Keep prior sha refs in `STALE_ROCMFPX_IMAGE_REFS` so each bump auto-retags.

## Phasing

1. ✅ **HW-gated resolver** (superseded by 2 — the gate was dropped for a universal
   default once the image was confirmed Vulkan-portable).
2. ✅ **Universal rocmfpx default + seed-profile repoint + retag** — shipped in PR #1297.
3. **Producer side** — rolling tag + manifest digest pin + toolbox-repo consolidation
   (Gap 0 / Gap 3).
