# HW-slot-ownership — hardware sticks to slots, logical tune sticks to models

> Ratified 2026-07-19 (user directive, deferred-pickup session). **Supersedes
> `spec-flags-ownership` §7** (which folded image/device/runner INTO the model).
> Reverses that axis: hardware/placement is owned by the **slot**, the model
> stays purely logical and device-agnostic. The §1–§6 flags-materialization
> half of spec-flags-ownership (profile→model tune text, one-owner freeform)
> stands unchanged.

## 1. Decision

Split ownership by **logical vs physical**:

- **Model** owns the *logical, device-agnostic* tune: freeform flag text
  (`-b/-ub`, `-fa`, KV-quant `-ctk/-ctv`, `--no-mmap`, `--n-cpu-moe`, rope, …)
  plus the typed capability fields (`mtp`, `jinja`, `chat_template`, modality).
  A model row is reusable across boxes and GPUs — it carries **no** image,
  **no** runner, **no** device, **no** `-ngl`, **no** `--threads`.
- **Slot** owns the *physical, box-specific* layer: `(slot_id, name, port,
  state)` + a small typed **hardware grid** `[device · NGL · THREADS · BINARY]`
  + an optional **`image_pin`**.
- **Profile** stays a device-agnostic *template* of the model tune only. No
  image, no hardware.

Rationale (one-owner rule): every physical fact has exactly one writer. Device
(rocm/vulkan) lived in two encodings under §7 — the typed field AND the
materialized `-dev/-ngl` in the model tune text — which can silently disagree
(slot says CPU, flags say `-ngl 99 -dev CUDA0` → silent CPU fallback running a
GPU argv). Forbidding hardware flags from the model collapses device to a single
owner: the slot. Same model on two GPUs = one model row + two slots with
different hardware — weights refcounted, no model-row fork, no contradiction.

## 2. Slot hardware grid (typed, 4 fields)

```
device [gpu-rocm ▾]   NGL [99]   THREADS [8]   BINARY [img-ref ▾]
 └ class+backend        └ -ngl     └ --threads   └ image ref (container build)
```

- **device** — existing enum `gpu-rocm / gpu-vulkan / cpu / npu`. **Owns
  class+backend** (single writer of "rocm vs vulkan"). Drives the GPU-visibility
  env / CDI `--device` mounts AND emits `-dev`. NOT merely a flag → stays typed.
- **NGL** — `n_gpu_layers` int → emits `-ngl`. Authoritative on the slot again
  (reverses the §5 fold that moved it into `model.defaults.n_gpu_layers`).
- **THREADS** — int → emits `--threads`.
- **BINARY** — the runner **image ref only** (container build). Its
  `supported_backends` list is **fit-check metadata, not a selector** — a
  multi-backend image (rocm+vulkan both) is disambiguated by `device`, never by
  BINARY. Replaces `model.preferred_runner`.

Rare/obscure hardware flags (`--main-gpu/-mg`, `--tensor-split/-ts`,
`--split-mode/-sm`, `-ngld`, physical multi-GPU `-dev CUDAn` index) are NOT in
the grid — if genuinely needed they go on the model freeform (accepted edge).

## 3. Image resolution (collapses §7's chain)

```
image_default = RUNNER_IMAGES[slot.BINARY]     (code registry, digest-pinned)
effective     = slot.image_pin or image_default
```

- **`slot.image_pin`** — optional, null default. The single canonical escape
  hatch (debug build / A-B / rollback-to-last-known-good). Promotes the former
  `_resolve_image_ref` tiers 1-2 shim to a first-class field.
- **`profile.image` (tier 4): DELETED.** No longer the hatch home.
- Canonical TOML key: top-level `image_pin`. Old `image` / `[slot].image`
  nestings collapse into it (kills the `[image]` image-gen-section `str(dict)`
  overload bug for free).
- Non-default `image_pin` is **shown on the slot card** (tag/digest) — drift is
  never hidden.

## 4. Fit-check

`device.backend ∈ RUNNER_IMAGES[slot.BINARY].supported_backends`. RUNNER_IMAGES
carries a supported-backend / format-arch field (lxc105 finding: forks reject
newer GGUFs). Incompatible `(device, BINARY)` **warns at assignment, not at
spawn**.

## 5. Enforcement (partition guard)

Single source of truth: `SLOT_HARDWARE_FLAGS` frozenset =
`{-ngl/--n-gpu-layers, -dev/--device, --threads/-t}` (the grid-owned flags;
`-dev` also enum-owned).

- **Model / profile flag save HARD-REJECTS** any flag in `SLOT_HARDWARE_FLAGS`
  with a "belongs on the slot" message — symmetric to the §21.7
  `MANAGED_ARGS_DENYLIST`, which already hard-rejects.
- Slot grid is typed → it structurally cannot carry model-tune flags.

## 6. Migrations (one-shot, snapshot-first, idempotent, re-runnable)

1. `model.defaults.n_gpu_layers` → slot **NGL** (unwinds the shipped §5 fold).
2. `model.preferred_runner` → slot **BINARY**.
3. `profile.image` **deliberate** pins → `image_pin` of every referencing slot
   (fold-to-slots, each gets its own copy). Former-default **debris** dropped
   via `STALE_RUNNER_IMAGE_REFS`. Zero-slot profiles: log the lost pin. Delete
   `profile.image`.
4. `slot.image` / `[slot].image` → `slot.image_pin`; collapse nestings.

Reuse `updater.retag_stale_slot_images` machinery (already distinguishes
deliberate pin vs former-default debris on both slots and custom profiles).

## 7. Killed / de-risked

- **Task #23 (`slot.device` → model runner axis): CANCELLED.** Device stays on
  the slot. The behavior-risky ~10-file GPU-visibility rewire and its live-GPU
  validation gate evaporate.

## 8. UI

- **Slot editor**: the 4-field HW grid + optional `image_pin` field + fit-check
  warning. **Slot card**: non-default image tag + HW chips.
- **Model drawer**: no device/runner/image; flags textarea client-validates
  against `SLOT_HARDWARE_FLAGS` (mirrors the server hard-reject).
- **Profiles**: remove the `image` field; device-agnostic tune only.
- **Runtimes panel**: reverse-index **slots** via `BINARY` (not models via
  `preferred_runner`) — `useRuntimes.ts` flips the join.

## 9. Verification

- Unit: partition denylist reject (model rejects `-ngl/-dev/--threads`); slot
  segment emits `-ngl/--threads/-dev`; image_default vs image_pin precedence;
  fit-check warn path; migration folds (ngl, runner, profile.image) + debris
  drop + zero-slot-profile log; idempotent re-run.
- Golden path #5 (pull→assign→infer) with a logical model + a slot carrying the
  HW grid — asserts no profile/model image or device read at launch.
- UI: slot grid edit/save + card drift chip; model-drawer HW-flag rejection;
  Runtimes panel slot reverse-index.

## 10. 1.0 seeded-profile strategy

The 1.0 catalog uses the minimal canonical workload strategy. Fresh installs
receive these immutable, device-agnostic profile templates:

| Profile | Runtime family | Purpose |
|---|---|---|
| `chat` | llama-server | general chat and coding |
| `chat-long-context` | llama-server | long-context logical tune |
| `dense` | llama-server | FPX-DNSE dense long-context tune |
| `moe` | llama-server | FPX-MOE/Saber logical tune |
| `embedding` | llama-server | pooled embeddings |
| `reranking` | llama-server | reranking |
| `cpu-chat` | llama-server | CPU-safe chat tune |
| `flm` | FLM | NPU chat/embed/STT runtime |
| `kokoro` | Kokoro | CPU speech synthesis |
| `qwen3-tts` | Qwen3-TTS | GPU speech synthesis |
| `comfyui` | ComfyUI | image generation |

ROCm/Vulkan/CUDA matrix variants are clone-only examples. They are not
catalog seeds because backend, device, offload layers, threads, runner binary,
and image pins are slot facts. The seeded slot TOMLs stamp those facts; model
family defaults and model defaults supply architecture-specific logical flags.
The `mtp` profile field remains informational for 1.0 API compatibility and is
scheduled for removal in the post-1.0 profile-schema cleanup; launch reads MTP
from model capability plus the selected slot runner.

Migration story: existing slots keep their physical fields and are migrated by
`hal0 slot migrate-hw`; old profile names remain operator data until explicitly
cloned/rebound, while fresh seed slots use the canonical names above. A missing
old profile falls back to `chat` for llama-server slots, with a warning.
