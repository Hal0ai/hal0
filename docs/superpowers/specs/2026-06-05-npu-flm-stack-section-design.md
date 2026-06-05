# NPU / FLM stack section (Spec 2)

**Date:** 2026-06-05
**Status:** Approved direction (Variant B) — implementation tasked
**Area:** dashboard SPA (`ui/src/dash/slots.jsx`) + slots/lemonade/capabilities API
**Sibling:** Spec 1 (`2026-06-05-slot-edit-panel-controls-design.md`) — the per-slot
controls + Capabilities 4-up grid. This spec adds the **NPU section** that sits
above/alongside that grid.

## Problem

The NPU runs **one FLM process** that packs **chat + ASR + embed coresident** (the
"FLM trio"), loaded when the NPU chat slot starts with `flm.args = "--asr 1 --embed 1"`.
The dashboard's current `NpuBlock` (`slots.jsx:426`) only **displays** the trio — you
can't pick the FLM chat model, toggle ASR/embed, or load/unload the stack from the UI.
Users also can't reason about how the NPU relates to the same capabilities (embed, STT)
running on iGPU/CPU. We're building a **control surface** (the chosen "Variant B"
bracketed-trio shape) and wiring it to actually drive the stack.

## Architecture reality (the constraints the UX must respect)

- **FLM trio = chat + ASR + embed in ONE process.** Defined/seeded as `agent` /
  `stt-npu` / `embed-npu` (`slots.py:112`, `manager.py:77`), but the **dispatch**
  detection is generalized by `device=="npu"` + `type` (`v1.py:782-800`) — it matches
  the request model against `slot.model.default` or `slot.name`, **not** hard-coded
  names. The existing `NpuBlock` is likewise device-driven (`s.device === "npu"`).
  → **The section keys off `device=="npu"`, never literal names.**
- **`flm.args` lives in lemond's `config.json`**, set via `POST /api/lemonade/config`
  `{flm_args}` (`lemonade_admin.py:322`, validated to require `--asr 1 --embed 1`),
  **applied at the next FLM load**. Changing modalities ⇒ reload.
- **TTS is NOT an FLM modality.** FLM serves chat/ASR/embed only. TTS (kokoro,
  VibeVoice) runs on CPU/iGPU and never appears in this section.
- **FLM has its own model namespace** (`registry/seeds/haloai_models.json`,
  `backend:"flm"`): chat (qwen3-it:4b, llama3.2:3b, gemma3:4b, gpt-oss:20b, …),
  embed (`embed-gemma-300m-FLM`), ASR (FLM whisper). **No arbitrary GGUFs.**
- **The FLM child's port** is discovered at runtime via
  `find_flm_chat_backend_url()` (`dispatcher/flm_trio.py`) — recipe=flm + type=llm in
  `/v1/health.loaded`.

### Two mechanisms that must be reconciled (the hard part)

1. **The trio mechanism** (this spec): one process, driven by `flm_args` + loading the
   NPU chat slot; ASR/embed are coresident, dispatched by `v1.py`.
2. **The capability orchestrator** (`capabilities/orchestrator.py`): selections
   (`capabilities.toml [selections.<cap>.<child>]` → device/provider/model/enabled)
   reconcile into **standalone** slots. It explicitly declares **"NPU multiplex …
   OUT OF SCOPE; NPU children spawn their own slot"** (orchestrator.py:15-17), and
   `_NPU_FANOUT_CAPS = {chat, embed}` (catalog.py) — so the capability picker offers
   NPU for chat/embed but **not** STT, and would try to make a *standalone* NPU embed
   slot rather than route into the trio.

These disagree about NPU embed. The design resolves it (below) and phases the work so
Phase 1 ships value without the full orchestrator unification.

## Design principles (flexible, intuitive UX)

1. **Chat is the anchor; ASR/embed are coresident toggles.** The section always leads
   with the FLM chat model. ASR and embed are bound to it (Variant B bracket) so it
   reads as "one process, boots together."
2. **One master power switch** loads/unloads the whole stack. Per-modality toggles set
   intent; because `flm_args` apply at load, changing a toggle while running shows a
   **pending "⟳ reload to apply"** state rather than lying about instant effect.
3. **Honesty about state.** Show: loaded/unloaded, live `flm_args`, the active chat
   model + per-modality model, the FLM child port (debug), and pending-vs-applied.
4. **FLM-namespace pickers only.** Chat/embed/ASR pickers list FLM models; offer a pull
   affordance for catalogued-but-not-downloaded FLM models. Never show GGUFs.
5. **Device-partition clarity (one capability, one home).** A capability (embed, STT)
   appears in **either** the NPU section (when on NPU) **or** the Capabilities grid
   (when on CPU/iGPU) — never both. A small inline link ("also runnable on iGPU/CPU →
   Capabilities") makes the relationship discoverable. TTS lives only in Capabilities.
6. **Graceful absence.** No NPU hardware → the section hides (or shows "NPU not
   detected"), exactly like `NpuBlock` returning null today.
7. **Forgiving, reversible.** Toggling embed/ASR off doesn't destroy config; it flips a
   flag. Re-enabling restores. Invalid combinations surface a clear message, never a
   silent no-op.

## The NPU section UI (Variant B — bracketed trio)

A section header **"NPU · FLM Stack"** with a coresident chip + **master power Switch**,
above three cards visually bound by a left bracket:

- **Chat card (anchor, always on):** FLM chat-model picker (from FLM chat namespace),
  live metrics (tok/s, TTFT, KV) when loaded.
- **ASR card:** on/off Switch + FLM ASR model picker (faded when off).
- **Embed card:** on/off Switch + FLM embed model picker (faded when off).

Footer: live `flm.args = "--asr <0|1> --embed <0|1>"`, FLM child port, and a
"reload to apply" hint when toggles are pending. Each card uses the same `.slot`
visual language as the rest of the grid (consistency with Spec 1's Capabilities cards).

This replaces the read-only `NpuBlock`/`NpuReactor` in `slots.jsx`.

## Backend wiring

### Reads
- `GET /api/slots` → slots with `device=="npu"`, `coresident_group`, `lemonade_state`,
  `backend_url`, per-slot metrics. (Drives display + which modalities are enabled.)
- `GET /api/lemonade/config` → current `flm_args` (parse `--asr`/`--embed`).
- FLM model lists → `GET /api/capabilities` catalogs (chat/embed) and/or
  `models_for_capability` filtered to `backend=="flm"`.

### Writes (control)
- **Set modalities:** `POST /api/lemonade/config { flm_args: "--asr <0|1> --embed <0|1>" }`.
  NOTE current validation *mandates* both `--asr 1` and `--embed 1`
  (`lemonade_admin.py:212-215`). **Change required:** relax validation so the trio can
  run chat-only or chat+one-modality (accept `--asr 0` / `--embed 0`); keep the keys
  present so absence never silently disables.
- **Pick chat model / load / unload:** `POST /api/slots/{npuChat}/swap` (model),
  `/load`, `/unload`. `{npuChat}` resolved by `device=="npu" && type=="llm"`, not name.
- **Enable/disable shadow modality slots:** `PUT /api/slots/{name}/config { enabled }`
  for the `device=="npu"` embedding/transcription slots (so dispatch gating in
  `v1.py:_is_npu_trio_request` matches), then reload the stack.

### Backend changes needed
1. **Relax `flm_args` validation** to allow `--asr 0` / `--embed 0` (chat-only or
   single-modality stacks). (`lemonade_admin.py`)
2. **A single "apply NPU stack" path** is desirable so the UI does one call: set
   flm_args + set chat model + enable/disable shadow slots + reload, transactionally.
   Phase 1 may compose the existing endpoints client-side; Phase 2 adds a dedicated
   `POST /api/npu/stack` (or capability-orchestrator route) that does it atomically.
3. **NPU ASR as a selectable capability (Phase 2):** add `asr` to `_NPU_FANOUT_CAPS`
   so the capability picker can route STT → NPU (today only chat/embed).

## Reconciliation decisions

- **Identity by device, not name.** The section, dispatch, and shadow-slot gating all
  key on `device=="npu"` + `type`. Keep seeding canonical names (`agent`/`stt-npu`/
  `embed-npu`) for fresh installs, but never *require* them. Boxes with `npu`/`embed`/
  `stt` work unchanged. (Document; add a test asserting device-driven detection.)
- **NPU embed routes into the trio, not a standalone slot.** When a capability
  selection is `device=npu`, the orchestrator must enable the trio's embed modality
  (flm_args `--embed 1` + enable the npu embedding slot) rather than spawn an
  independent FLM process. This closes the "NPU multiplex out of scope" gap. **Phased:**
  Phase 1 drives the trio directly from the NPU section; Phase 2 makes the capability
  orchestrator NPU-aware so the Capabilities picker and the NPU section stay in sync.
- **Voice stack split.** STT may live on NPU (trio ASR) or CPU/iGPU (moonshine/whisper);
  TTS only on CPU/iGPU (kokoro now; VibeVoice later). The NPU section shows STT only
  when it's on NPU; TTS never appears here.

## Voice stack & VibeVoice (dependency)

- **kokoro** (TTS, `providers/kokoro.py`) and **moonshine** (STT, `providers/moonshine.py`)
  are implemented; **whisper** STT runs via whisper.cpp or FLM-on-NPU.
- **VibeVoice** (TTS) is **catalogued only** (`vibevoice-realtime-0.5b`) — **no
  provider implemented**. Standing it up (iGPU PyTorch runtime + a `providers/vibevoice.py`
  + toolbox) is a **separate spec/dependency**, out of scope here. Until then kokoro is
  the working TTS. Tracked as a follow-on.

## Scope / phasing

- **Phase 1 (this task):** Build the Variant B NPU section and wire it to the FLM trio
  via existing endpoints + the `flm_args` validation relaxation. Outcome: from the
  dashboard you can pick the FLM chat model, toggle ASR/embed, and load/unload the
  stack, with honest pending/applied state. Replaces `NpuBlock`/`NpuReactor`.
- **Phase 2 (follow-on):** Capability-orchestrator NPU-awareness (embed/STT selections
  with device=npu route into the trio; add `asr` to NPU fanout), a transactional
  `POST /api/npu/stack` apply, and full NPU↔Capabilities device-partition sync.
- **Dependency spec:** VibeVoice TTS provider.

## Testing

- **Backend:** `flm_args` validation accepts `--asr 0`/`--embed 0`; rejects malformed.
  Device-driven trio detection (rename slots → still detected). Shadow-slot enable
  toggles dispatch gating.
- **Frontend (γ-suite / Playwright):** section renders only when an NPU slot exists;
  chat picker lists FLM-only models; toggling ASR/embed updates the pending `flm_args`
  preview; master switch issues load/unload; a capability shown on NPU is not also
  shown in the Capabilities grid.

## Files touched

- `ui/src/dash/slots.jsx` — replace `NpuBlock`/`NpuReactor` with the Variant B
  control section; device-keyed; wire to hooks.
- `ui/src/api/hooks/` — add `useLemonadeConfig` (get/set `flm_args`) if absent; reuse
  `useSlots`, `useCapabilities`.
- `ui/src/dash/dashboard.css` — NPU section styling (bracket, bound cards).
- `src/hal0/api/routes/lemonade_admin.py` — relax `flm_args` validation.
- Phase 2 (separate PRs): `capabilities/orchestrator.py`, `capabilities/catalog.py`
  (`_NPU_FANOUT_CAPS`), a `POST /api/npu/stack` route.
- Tests: backend `flm_args` + trio-detection; γ-suite NPU section spec.

## Prototype

Variant B chosen from the throwaway prototype at `ui/npu-proto.html?variant=B`
(`ui/src/npu-proto.tsx`). Delete the prototype once the section lands.
