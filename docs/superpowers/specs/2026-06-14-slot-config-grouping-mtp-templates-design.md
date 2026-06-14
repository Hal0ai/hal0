# Slot config UX: grouped fields, type-default relocation, reasoning/MTP pills, non-manual chat templates

- **Date:** 2026-06-14
- **Status:** Approved (brainstorm) → ready for implementation plan
- **Surface:** hal0 dashboard slot edit drawer (`ui/src/dash/slot-modals.jsx`), Settings (`ui/src/dash/settings.jsx`), model recipe editor (`ui/src/dash/model-modals.jsx`); backend `src/hal0/config/schema.py`, `src/hal0/slots/flag_merge.py`, `src/hal0/providers/llama_server.py`.
- **Builds on:** PR #781 (non-blocking slot save/swap), `2026-06-05-slot-edit-panel-controls-design.md`.

## Problem

The slot edit drawer is a flat list of fields with no indication of which knob targets the **slot**, the **model**, or the **profile**. Specific pain points the operator hit:

1. No visual grouping — slot-level vs model-level fields are indistinguishable.
2. The **"Default for type"** checkbox is confusing in this context; the only "default" that matters while editing a slot is *which model it loads*.
3. The **reasoning** control is a checkbox whose label flips ("Reasoning Off" ↔ on) so it's ambiguous which state enables it.
4. **MTP** can only be had by switching to the `rocm-mtp` profile — there's no first-class toggle, and no guidance on when it helps.
5. The **chat template** (jinja) some models require is only settable by hand-editing TOML.

## How config works today (grounding)

- **Field ownership** (`schema.py`):
  - `[slot]` (`SlotConfig`): `name`, `port`, `device`, `profile`, `enabled`, `role`, `enable_thinking`, `workers`, `idle_timeout_s`, and `default` (the type-routing default).
  - `[model]` (`ModelConfig`): `default` (model id), `context_size`, `n_gpu_layers`, `rope_freq_base`, `extra`.
  - `[server]` (`ServerConfig`): `extra_args` (freeform llama-server passthrough).
  - **Profile** (`ProfileConfig`): `image`, `flags`, `mtp`, `device_class`, `backend` — shared across slots that reference it.
- **Type default** = exactly one `default=true` slot per type drives routing (`manager.default_slot_for`).
- **Reasoning** = `enable_thinking` tri-state (`true`/`false`/`None`=inherit-global), applied via `chat_template_kwargs.enable_thinking` with llama-server `--jinja` (`normalize/thinking.py`).
- **MTP** = profile flag (`ProfileConfig.mtp`); when true the `MTP_FLAG_BUNDLE` (`--spec-type draft-mtp --spec-draft-*`) is appended to profile flags. Seed profiles `rocm` (mtp off) and `rocm-mtp` (mtp on) share an image and differ only by this.
- **Chat template** = `chat_template` resolved via `_slot_or_backend("chat_template", "defaults", "chat_template_file")` (`llama_server.py:205`) → llama-server `--chat-template-file`. A slot value already wins over the model's `defaults`.

## Decisions (this brainstorm)

| Fork | Decision | Rationale |
|---|---|---|
| Type default | **Remove from drawer → new Settings "Default slots" pane** | One place to set all type defaults; the drawer stays about *this* slot. |
| MTP toggle | **Per-slot `mtp` override**, capability-gated pill | Decouples MTP from profile pairing; only meaningful when the model supports it. |
| Chat template | **Model-level default + per-slot override** | Set once per model (auto-applies everywhere), override per slot when needed. |
| Reasoning control | **Two-state sliding pill (On/Off)** | Explicit; drops the rarely-used inherit-global state. Fixed label removes the flip ambiguity. |

### MTP research findings (informs the design)

- MTP is model-native speculative decoding (built-in draft heads; no separate draft model). Mainlined in llama.cpp PR #22673 (May 2026).
- **MTP helps dense models (~2×), hurts MoE.** Confirmed by our own bench: `rocm` (MoE) 52.8 tps vs `rocm-mtp` 24.4 tps. The verify-overhead is a net loss on already-sparse MoE.
- **MTP requires an MTP-capable GGUF** (must ship the draft heads). A normal GGUF cannot use it.
- **Our bundle is likely mistuned:** `--spec-draft-p-min 0.0 --spec-draft-n-max 4`. Guidance: p-min matters more than n-max; ~0.75 is the sweet spot, n-max 5 for dense. Tracked as a **separate bench ticket** (do not change blind).
- Sources: johnpaulwile.substack.com/p/multi-token-prediction-mtp-in-llamacpp; github.com/ggml-org/llama.cpp/blob/master/docs/speculative.md; dredyson.com Qwen3.6-27B MTP guide; allenkuo.medium.com when-speculative-decoding-helps.

**Consequence:** the MTP pill renders only when `model.mtp_capable && slot device is rocm`, with an inline hint ("dense models only — MoE runs slower").

## Design

### Reusable grouping primitive

Introduce `FieldGroup({ label, hint, children })` — a labeled section wrapper (small caps header + subtle rule). Used by the drawer now and reusable across profiles/models/settings config surfaces. Lives in `ui/src/dash/primitives.jsx`.

### Grouped edit drawer

```
SLOT · this instance
  Name      (read-only)   Port (read-only)   Status (read-only)
  Profile   <select>  (GPU slots; live-filters the Model list)
  Enabled   pill

MODEL · what it loads
  Model     <select>  → sets the slot's default model; options recompute
                        from the *selected* Profile; persists model.default
                        and applies via the non-blocking swap (PR #781)
  Context   <int>
  Template  <model's chat_template, read-only>  [Override]

INFERENCE · behavior
  Reasoning <pill On/Off>
  MTP       <pill On/Off>   (only when model MTP-capable + rocm; hint shown)

▸ Advanced  (collapsed) — n_gpu_layers / rope_freq_base / extra_args
            (profile-owned, read-only) + resolved command
```

- **Removed:** the "Default for type" checkbox.
- **Model dropdown is reactive:** its compatible-model filter depends on `selectedProfile` (not the persisted profile), so changing Profile re-filters models immediately (e.g. picking `vulkan` drops rocmfp4 models). Selecting a model persists `model.default` via `PUT /config { model: { default } }` and applies it via the existing non-blocking swap path.

### Type-default → Settings "Default slots" pane

New section in `settings.jsx`: for each slot type that has ≥2 slots, a row with a dropdown to choose the default slot. Backend: a `POST /api/slots/{name}/default` (or `PUT /api/settings/default-slots`) that sets `default=true` on the chosen slot and clears it on its siblings of the same type (atomic). **Verify whether a set-default endpoint already exists** before adding one; today the drawer wrote `default` via `PUT /config`.

### Reasoning pill

Two-state sliding pill, fixed label "Reasoning". Maps `enable_thinking` `true`/`false`. The drawer no longer authors `None`; existing `None` slots seed the pill to Off and only write on change (preserve #587 dirty-tracking so a no-op save doesn't clobber). Instant-apply behavior unchanged (its own `PUT /config`).

### MTP pill (Phase 2 — backend + UI)

- **Backend:** add `SlotConfig.mtp: bool | None` (override). `flag_merge`/`profile_flags` expansion appends `MTP_FLAG_BUNDLE` when `slot.mtp` is set, else falls back to `profile.mtp`. Keeps the bundle as the single source of truth.
- **Model capability:** mark MTP-capable models in the registry (a `mtp` tag/`labels` entry or detected from GGUF metadata at scan time). Surface `mtp_capable: bool` on the `/api/models` payload (`normalizeApiModel`).
- **UI:** pill in the INFERENCE group, rendered only when `model.mtp_capable && device starts with "gpu-rocm"`. Toggling writes `slot.mtp` (non-blocking restart, same as profile change). Hint links the dense-vs-MoE caveat.
- **Separate ticket:** retune `MTP_FLAG_BUNDLE` (p-min 0→~0.75, n-max→5) behind a bench.

### Chat template: model-level + slot override (Phase 3 — backend + UI)

- **Template library:** ship a small catalog of common jinja templates (chatml, qwen3, llama3, gemma, …) under the package (e.g. `src/hal0/templates/chat/*.jinja`), exposed via `GET /api/chat-templates` (id, label, preview). Plus a "Custom" path: paste jinja → stored as a file the slot/model references; and "Auto" = use the GGUF-embedded template (no `--chat-template-file`).
- **Model level:** model recipe editor (`model-modals.jsx`) gains a "Chat template" field writing the model's `defaults.chat_template_file` (already read by `_slot_or_backend`). Default "Auto".
- **Slot override:** drawer Template row shows the model's value read-only; **[Override]** opens the same picker and writes the slot's `chat_template` (slot wins over model defaults, already supported).

## Phasing

| Phase | Backend | Ships |
|---|---|---|
| **1 — UI reorg** | none (unless set-default endpoint missing) | `FieldGroup`, grouped drawer, reasoning pill, remove type-default checkbox, reactive model dropdown, Settings "Default slots" pane |
| **2 — MTP** | small (`SlotConfig.mtp`, flag-merge, model `mtp_capable`) | capability-gated MTP pill; bench ticket filed |
| **3 — Chat templates** | medium (template catalog + storage + endpoint) | model recipe template field + slot override picker |

## Testing

- **e2e (Playwright) is the correctness gate for `.jsx`** — `tsc`/`vite build` do not catch undefined-var runtime errors in `.jsx` (learned in PR #781). Every phase adds/updates specs under `ui/tests/e2e/specs/` using the `apiMock` + `seedSlots` harness:
  - Phase 1: groups render with correct field membership; model dropdown re-filters when Profile changes; reasoning pill writes `enable_thinking` true/false and no-op save stays quiet; type-default checkbox absent; Settings pane sets default and clears siblings.
  - Phase 2: MTP pill hidden when model not capable / non-rocm; toggling writes `slot.mtp`; backend flag-merge unit test asserts bundle appended on `slot.mtp` override.
  - Phase 3: template picker lists catalog + custom; model recipe writes `defaults.chat_template_file`; slot override writes slot `chat_template` and wins over model.
- Backend: pytest for `flag_merge` MTP override and `mtp_capable` detection.

## Out of scope / follow-ups

- Retuning `MTP_FLAG_BUNDLE` (separate bench ticket).
- Applying the same non-blocking pattern to the slot **card's** `runMutation` (noted in PR #781).
- Extending `FieldGroup` grouping to profiles/models/settings surfaces beyond what each phase touches.

## Open questions

None blocking. Set-default endpoint existence to be confirmed during Phase 1 (additive if missing).
