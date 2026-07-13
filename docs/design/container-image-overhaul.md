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
- **Hardware-gated.** `derive_profile` picks `hal0-rocmfpx` on gfx1151/Strix-Halo;
  falls back to the generic `amd-strix-halo-toolboxes:vulkan-radv-server` (RADV)
  on other AMD GPUs, and `llama.cpp:server-cuda` / cpu-llm as today. rocmfpx is
  never forced onto hardware that can't run it.
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

### 3. Hardware gate (`slots/profile_derive.py` + `providers/_gpu.py`)

`derive_profile` already selects the profile (hence lane). Extend it to choose the
image **key** by probed GPU: `gfx1151`/Strix-Halo → `rocmfpx`; other AMD → `vulkan`
(RADV); NVIDIA → `cuda`; no GPU → `cpu-llm`. Reads the existing `hardware.json`
probe (`_gpu.py`), never raises on a missing probe (defaults to `vulkan`).

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

- **Size:** rocmfpx ≈ 7.5 GB vs 1.98 GB for vulkan-radv — bigger fresh pull; HW
  gate keeps non-Strix hosts on the small image.
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

### The actual remaining gaps (surgical)
1. **Basic seed profiles still hardcode the old toolbox.** `rocm`, `vulkan`,
   `rocm-longctx`, `embed`, `rerank`, `vulkan-embed`, `vulkan-rerank`, `cpu-llm`
   pin `amd-strix-halo-toolboxes:{vulkan-radv-server, rocm-7.2.4-rocmfp4-server}`.
   → Fresh installs land on the old image.
2. **`STALE_ROCMFPX_IMAGE_REFS` omits those two toolbox refs** → `hal0 update`'s
   retag skips slots on them. Add both (incl. `localhost/` prefixes) so existing
   installs migrate.
3. **No HW gate.** `DEFAULT_ROCMFPX_IMAGE` (and the retag target) is unconditional;
   rocmfpx is a gfx1151/Strix-Halo build. Make the default *resolve* per probed
   GPU (rocmfpx on gfx1151, generic RADV vulkan / rocm toolbox elsewhere, cuda,
   cpu-llm) so retag + fresh-install don't force rocmfpx onto incompatible hosts.
   Note embed/rerank need `--embedding`/`--reranking` and cpu-llm needs CPU mode —
   verify the rocmfpx image serves those before repointing those lanes.
4. **Versioning.** `DEFAULT_ROCMFPX_IMAGE` is a raw sha (`c077206`). Move to rolling
   `hal0-rocmfpx:stable` + `manifest.json` digest pin via `update-toolbox-digests.sh`;
   add `.github/workflows/toolbox.yml` build/push so `release.yml`'s null-digest gate
   passes. Keep prior sha refs in `STALE_ROCMFPX_IMAGE_REFS` so each bump auto-retags.

## Phasing (stacked PRs)

1. **Resolver unification** — `resolve_slot_image`, `base.image_ref` default,
   `llama_server` uses it, providers consolidated. No behavior change (manifest
   still names old images). Tests: precedence + parity with current output.
2. **Repoint to rocmfpx + HW gate** — manifest `rocmfpx` key, seed profiles by
   key, `derive_profile` gate, `update-toolbox-digests` + CI. Fresh installs land
   on rocmfpx on Strix-Halo.
3. **Update propagation** — image-drift in `_fetch_slot_drift`, reconcile on
   `hal0 update`, doctor drift flag.
