# Slots page: edit controls + layout (`enable_thinking`, `enabled`, `n_gpu_layers`, sort, Capabilities grid)

**Date:** 2026-06-05
**Status:** Approved design — ready for implementation plan
**Area:** dashboard SPA (`ui/src/dash/`) + slots API (`src/hal0/api/routes/slots.py`)

**Scope note:** This is "Spec 1" — pure slots-page UX (controls + layout). The
**NPU / FLM stack management** section is a separate "Spec 2" (its own design),
because NPU embedding is a coresident FLM-trio modality, not a standalone slot,
and needs backend modeling decisions first.

## Problem

Operators can only change a handful of slot settings from the dashboard
(`device`, `default`, `ctx_size`, `idle_timeout_s`, `workers`, `extra_args`).
Three high-value settings have no UI:

- **`enable_thinking`** — the per-slot reasoning default. Turning it on/off
  today means hand-editing `/etc/hal0/slots/<name>.toml`. This was the root of
  a real incident (the `primary` slot's `enable_thinking=true` made the Hermes
  TUI appear to hang on empty `reasoning_content`).
- **`enabled`** — activate/deactivate a slot. The only "off" today is delete.
- **`n_gpu_layers`** — GPU offload tuning on the shared 96 GB box.

## Goals / non-goals

**Goals**
- Toggle `enable_thinking` per slot from the edit drawer; effect on next message.
- Toggle `enabled` per slot directly on the slot **card**; faded card when off.
- Edit `n_gpu_layers` from the drawer's Advanced section.
- **Sort** enabled slots first, disabled slots to the end of the grid.
- **Capabilities section**: render the utility slots (embedding, reranking,
  transcription, tts) in a denser 4-up (quarter-width) grid, since their card
  content is narrow.

**Non-goals**
- Per-slot sampling defaults (`temperature`/`top_p`/`max_tokens`) — those are
  per-request, not slot config. Out of scope (would be a separate `[model].extra`
  / sampling-defaults feature).
- `role` and `rope_freq_base` controls — deliberately excluded (`role` is a
  routing foot-gun; `rope_freq_base` is niche).

## Background — how the pieces already work

- **Write path exists.** `PUT /api/slots/{name}/config` (`update_slot_config`)
  shallow-merges a partial `SlotConfig` into the TOML. `enable_thinking: bool|None`
  and `enabled: bool` are top-level `SlotConfig` fields
  (`src/hal0/config/schema.py:241,253`). `PATCH /api/slots/{name}/defaults`
  (`update_slot_defaults`) merges into the `[model]` sub-table — that's where
  `n_gpu_layers: int` lives (`ModelConfig`, `schema.py:156`).
- **`enable_thinking` is read live per request** by `_slot_thinking_default`
  (`src/hal0/api/routes/v1.py:333`) and injected as `chat_template_kwargs` by
  `src/hal0/normalize/thinking.py`. Absent/`None` → **OFF (suppression)**, so the
  effective state is binary (a tri-state "default" would duplicate "off").
  → Toggling `enable_thinking` takes effect on the **next message, no restart**.
- **`enabled` is read at load/registration time** and participates in coexistence
  rules: two `device=npu, type=llm, enabled=true` slots cannot coexist
  (`state.py:190`); FLM-trio gating (`slots.py:260`); peer checks
  (`manager.py:1631,1650`). → Enabling can **fail validation**; disabling does
  **not** auto-stop a running slot.
- **`n_gpu_layers` changes model load** → restart-required, like `ctx_size`.
- **Payload gap.** `Slot.as_dict()` (`manager.py:166`) ships only runtime fields,
  not `enabled`/`enable_thinking`/`n_gpu_layers`. These must be added to the slot
  list payload so the card and drawer can seed their controls.

## Design

### Component 1 — Backend: expose config fields in the slot payload

In the slots list serialisation (`_slot_to_dict` / `list_slots` enrichment in
`src/hal0/api/routes/slots.py`), add three fields read from the slot's config:

- `enabled: bool` (top-level, default `true`)
- `enable_thinking: bool | null` (top-level)
- `n_gpu_layers: int` (from `[model]`)

These are cheap reads (config already loaded for other enrichment). No schema
change — the fields already exist in `SlotConfig`/`ModelConfig`.

### Component 2 — Backend: `enabled` transition safety

No new endpoint; `enabled` writes through the existing `PUT /{name}/config`.
Two behaviours must hold so the toggle is truthful (verify current behaviour,
add where missing):

- **Invalid enable** (would violate npu-exclusivity / FLM-trio) → `update_config`
  raises a `ValidationError`/`Conflict` with a clear message. The API returns the
  error; the UI reverts the switch and toasts the message.
- **Disable of a running slot** → the persisted `enabled=false` write is followed
  by a **stop** of the running slot (reuse the existing unload/stop path) so the
  slot is actually offline, matching the faded card. If the slot is already off,
  the write alone suffices.

### Component 3 — Frontend: `enabled` toggle on the card + fade

In `SlotCard` (`ui/src/dash/slots.jsx`):

- Add a compact switch to the header's top-right `<div className="right">`
  (beside the ★default / coresident chips), bound to `slot.enabled`.
- On flip: optimistic update → `PUT /api/slots/{name}/config { enabled }`.
  On error: revert + error toast. On disable: backend also stops the slot
  (Component 2); the card transitions to its off/faded state.
- **Fade:** when `!slot.enabled`, add a modifier class (e.g. `slot--disabled`)
  to the root `.slot` div → `opacity: ~0.5` in `dashboard.css`. The toggle itself
  stays full-opacity and interactive so the slot can be re-enabled; action
  buttons (Start/Stop/Restart) are hidden or disabled while disabled.
- `enabled` is **not** duplicated in the drawer — the card is the single source.

### Component 4 — Frontend: `enable_thinking` instant toggle in the drawer

In `EditSlotDrawer` (`ui/src/dash/slot-modals.jsx`), **llm slots only**
(hidden for stt/tts/embed/rerank/image):

- A switch bound to `slot.enable_thinking` (seed from payload; `null`→off).
- On flip (instant-apply, mirrors the backend selector): `PUT /{name}/config
  { enable_thinking }`, optimistic, revert + toast on error. Toast
  `thinking on/off — applies to next message`.
- Label/subtext: *"Stream reasoning before the answer. Off = faster, direct
  replies."* **No** `⟳ restart required` badge (live per-request read).

### Component 5 — Frontend: `n_gpu_layers` input in the drawer

In `EditSlotDrawer` Advanced section, an input bound to a `nGpuLayers` state,
saved with the **existing Save button** via `defaultsMut` →
`PATCH /{name}/defaults { n_gpu_layers }` (alongside `ctx_size`). Shows the
`⟳ restart required` badge. (Not instant — changes model load.)

### Component 6 — Frontend: sort enabled-first, disabled-to-end

In the slots grid render (`ui/src/dash/slots.jsx`), stable-sort so `enabled`
slots render before `!enabled` ones, preserving existing order within each group
(don't disturb the current type/role ordering otherwise). Pairs with the faded
card so disabled slots visually and positionally sink to the bottom.

### Component 7 — Frontend: Capabilities section (4-up grid)

Group the slots page into sections:
- **Primary/chat** (llm slots) — current card width.
- **Capabilities** — `embedding`, `reranking`, `transcription`, `tts` slots in a
  **4-up** (quarter-width) responsive grid. These cards have minimal content
  (a chip or two + one metric), so quarter-width avoids the wasted space of
  full-width cards. Responsive: 4-up on wide, collapsing to 2-up / 1-up on
  narrow viewports.

This is layout/CSS only — same `SlotCard`, rendered into a `.slot-grid--quarter`
container for the Capabilities section. No card-internal changes. (The optimal
section composition and where a future NPU section sits will be validated via a
throwaway UI prototype before implementation.)

## Data flow

```
Card enabled switch ─PUT /config {enabled}─▶ update_config ─▶ TOML
   └─ on disable: + stop running slot
   └─ on invalid enable: ValidationError ─▶ revert switch + toast

Drawer thinking switch ─PUT /config {enable_thinking}─▶ TOML
   └─ next message: _slot_thinking_default reads it live ─▶ chat_template_kwargs

Drawer n_gpu_layers + Save ─PATCH /defaults {n_gpu_layers}─▶ TOML [model]
   └─ ⟳ restart required to apply
```

## Error handling

- All writes: on non-2xx, revert the optimistic control to its prior value and
  surface the API error message via `window.__hal0Toast(..., "warn")`.
- Invalid `enabled` enable: show the coexistence-conflict message verbatim.
- Non-llm slots never render the thinking toggle.

## Testing

**Backend**
- `update_config` round-trips `enable_thinking` (true/false/null) and `enabled`
  to the TOML and back through the slot payload.
- Enabling a slot that violates npu-exclusivity / FLM-trio raises the expected
  error (extend existing coexistence tests if present).
- Slot list payload includes `enabled`, `enable_thinking`, `n_gpu_layers`.

**Frontend (γ-suite / Playwright)**
- Thinking toggle renders only for llm slots; flip issues `PUT /config` with the
  correct body; error reverts the switch.
- Card `enabled` toggle: flip issues `PUT /config`; disabling adds the fade class
  and hides Start/Stop; invalid-enable reverts + toasts.
- `n_gpu_layers` saves via the Save button (`PATCH /defaults`) and shows the
  restart badge.

## Files touched

- `src/hal0/api/routes/slots.py` — payload fields; `enabled` stop-on-disable +
  invalid-enable error surfacing (verify/extend).
- `ui/src/dash/slots.jsx` — card `enabled` switch + fade; enabled-first sort;
  Capabilities section grouping.
- `ui/src/dash/slot-modals.jsx` — drawer `enable_thinking` toggle + `n_gpu_layers`
  input.
- `ui/src/dash/dashboard.css` — `.slot--disabled` opacity; switch styling;
  `.slot-grid--quarter` 4-up responsive grid.
- Tests: backend slot-config tests + γ-suite slot specs.
