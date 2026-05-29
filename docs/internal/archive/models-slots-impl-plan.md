# Models & Slots — implementation plan

Spec for multi-agent execution. Each phase has defined file ownership and a hard gate. Agents stop at their gate; recommendations are not authorization for downstream work.

## Cross-cutting context

- Repo root: `/home/halo/dev/hal0/`
- Backend: `src/hal0/` (FastAPI routes live in `src/hal0/api/routes/`)
- Frontend: `ui/src/` (Vue 3 + Pinia + plain CSS)
- Builtin slot names: `primary`, `embed`, `stt`, `tts`, `img` — mirror at `src/hal0/slots/__init__.py:BUILTIN_SLOTS`
- Non-builtin slot lazily auto-created by the capabilities layer: `embed-rerank` (port 8086 by default; `CapabilityOrchestrator._ensure_slot_exists()` allocates from `8081..8099`).
- Backends enum (`_VALID_BACKENDS` in `src/hal0/config/schema.py`): `vulkan, rocm, flm, moonshine, kokoro, cpu`
- Providers enum (`_VALID_PROVIDERS` in `src/hal0/config/schema.py`): `llama-server, flm, moonshine, kokoro` — the ComfyUI provider exists in code (`src/hal0/providers/comfyui.py`) but is invoked via the dedicated `img` slot path, not through a `provider=` slot config.
- Footer events plan landed — `app.state.events` (`EventBus`) is the canonical emitter; see `src/hal0/events/`.

## Schema changes

### `Model` (`src/hal0/registry/model.py`)
Add:
- `backends: list[str]` — slot backends compatible with this model. GGUF → `["vulkan","rocm","cuda","cpu"]`. Moonshine → `["moonshine"]`. Kokoro → `["kokoro"]`.
- `defaults: ModelDefaults | None` — nested struct: `{context_size?: int, n_gpu_layers?: int, rope_freq_base?: float, extra_args?: str}`. All optional.
- `metadata.context_length: int | None` — architectural max read from GGUF header, set at scan/pull, read-only.

Migration: existing registry entries get `backends=[]` and `defaults=None` on first load; backfilled on next scan. Atomic TOML write path unchanged.

### `SlotConfig` (`src/hal0/config/schema.py`)
Add:
- `[server].extra_args: str | None` — freeform CLI passthrough appended after model defaults.

## Detection module

`src/hal0/registry/detect.py` (new):
- `detect(path: Path) -> DetectionResult` returns `{suggested_backends, suggested_capabilities, context_length, confidence, raw_hints}`.
- GGUF header parser (`src/hal0/registry/gguf_header.py`): read magic + arch + pooling-type keys. Format is documented; ~80 lines.
- Filename heuristic fallback when header unreadable: keywords (`embed`/`bge`/`e5` → embed; `whisper`/`moonshine` → asr; else chat for `.gguf`).
- File extension → suggested_backends seed: `.gguf` → all llama-server backends.

## Flag merge util

`src/hal0/launchers/flag_merge.py` (new):
- `merge_flags(model_defaults: str | None, slot_extra: str | None) -> str`
- Tokenize both; build set of slot's flag names; strip matching `--flag (value?)` from model defaults; concat `cleaned_model + " " + slot`.
- Heuristic: if next token doesn't start with `--`, treat as the flag's value.
- Append-list exception: `--lora`, `--draft-model`, `--override-kv` skip dedup (both apply).
- Malformed → dumb concat + structured warning log.

## Event emissions (shared ring with footer)

New event types on top of `slot.state` / `pull.*` / `system.*`:
- `model.registered` — fired by `POST /api/models` and scan-commit. `data: {id, backends, capabilities, source: 'manual'|'scan'|'hf'|'curated'}`.
- `model.updated` — `PUT /api/models/{id}` edits. `data: {id, changed_fields: [...]}`.
- `model.deleted` — emitted **last** in cascade (after all `slot.state` events from unloads). `data: {id, affected_slots: [...]}`.
- `model.detection.completed` — re-detect or scan preview. `data: {id?, diff?}`.
- `model.scan.started` / `model.scan.completed` — only if scan crosses ~2s threshold.

## API surface

New / changed endpoints (all in `src/hal0/api/routes/models.py` or `slots.py`):
- `POST /api/models/scan/preview` (new) — walk dir, return `DetectionResult[]` (no commit).
- `POST /api/models/scan` — accept (possibly user-edited) preview rows, commit. Emit one `model.registered` per row.
- `POST /api/models` — accepts a `Model`; UI single-file register path. Emit `model.registered`.
- `PUT /api/models/{id}` — extend body to accept `{name?, capabilities?, backends?, defaults?}`. Emit `model.updated`.
- `DELETE /api/models/{id}` — cascade by default. Order: unload each dependent slot → clear `[model].default` in TOMLs → registry delete → emit `model.deleted`. `?force=false` query opts back to block-with-409.
- `PUT /api/slots/{name}/config` — verify shallow-merge handles flat→nested body. UI sends flat today; backend schema is sectioned.

## UI changes

### Models page (`ui/src/views/Models.vue`)
- Rename "Pull model" button → "Add model".
- Add **Local file** tab to modal alongside Curated + HuggingFace.
  - Sub-action *Register single file*: path input + optional name + Submit → calls `POST /api/models`.
  - Sub-action *Scan directory*: path input + recursive toggle → preview table → user edits backends + capabilities per row → Commit.
- Per-model row: badges showing capability + backend tags (read-only).
- Per-model **Edit** action → new modal: name (text), capabilities (checkbox group), backends (multi-select), Advanced disclosure with `{context_size, n_gpu_layers, rope_freq_base, extra_args}` each having a "reset to detected" affordance. "Re-detect from file" button shows diff before applying.
- Delete confirm copy spells out cascade ("will unload from `primary`, clear from `chat-alt`, `embed`").

### Slots page (`ui/src/views/Slots.vue`)
- Model dropdown in Create + Edit filters by `slot.backend ∈ model.backends`. Hide incompatible. Zero compatible → empty state with "Add a model →" CTA (close edit → open Add modal → return).
- Auto-fill on model pick (Create + Edit, not inline swap): `ctx_size ← model.defaults.context_size ?? 4096` only if user hasn't touched the field. Show muted hint `"max {gguf.context_length}"` next to ctx_size.
- Edit modal expansion:
  - Keep current 4 fields at top.
  - Add `▸ Advanced` disclosure with two subgroups:
    - **Model**: `n_gpu_layers`, `rope_freq_base`
    - **Server**: `workers`, `idle_timeout_s`, `extra_args`
  - Provider + port shown read-only at top.
  - Per-field ⟳ icon on restart-required fields (`ctx_size`, `n_gpu_layers`, `port`).
- **Effective flags preview** in Advanced: read-only textarea showing `merge_flags(model.defaults.extra_args, slot.extra_args)` result with override-hints below ("--threads 4 from model defaults overridden by slot").
- Edit modal model field on save: if slot running AND only model changed → call `/swap`; else save config + prompt "Restart to apply?" one-click.
- **Standalone Swap modal: removed.**
- Drop per-slot SSE (`/api/slots/{name}/state/stream`) → subscribe to events store filtered `type === 'slot.state'`.

### SlotCard (`ui/src/components/SlotCard.vue`)
- Add inline model-swap dropdown (popover, `Teleport` to body to survive dense grid layouts + z-index). Triggered from model label area. Calls `/swap` directly. No ctx_size change.
- Existing actions (load/unload/restart/edit/logs/delete) unchanged.

## Footer integration (read-only here — footer plan owns)

- `Models.vue` and `Slots.vue` consume the events ring via `useEvents` composable (shared with footer).
- Footer's Logs tab sub-tabs derived from `/api/slots` list (one per running/recent slot), not hardcoded.

## Phases + ownership

### Phase 1 — Backend foundation (parallel, 2 agents)
File ownership locked, no overlap:

**A1: Events backbone — SUPERSEDED**
- Existing worktree `footer-backend-events` (commit `ac38899`) already implements this.
- Public API to consume:
  - `from hal0.events import EventBus, make_event`
  - Bus instance lives on `app.state.events`
  - Emit: `await app.state.events.emit(type=..., severity=..., source=..., message=..., data={...})`
- Event payload shape: `{id, ts (ISO8601 UTC), type, severity ('info'|'warn'|'error'), source, message, data: dict}`
- Routes: `GET /api/events?since&type&severity&limit` (max 1000 clamped) + `GET /api/events/stream?since` (SSE w/ backfill replay)
- Phase 2 agents reference this API; do NOT build their own events module.

**A2: Model schema + detection**
- Owns: `src/hal0/registry/model.py`, `src/hal0/registry/detect.py` (new), `src/hal0/registry/gguf_header.py` (new), registry store migration in `src/hal0/registry/store.py` (only fields, no API), tests.
- Tasks: add `backends`, `defaults`, `metadata.context_length` to `Model`. Build `detect()` + GGUF parser. Backfill function callable from scan path. Unit tests with sample GGUF fixtures.
- Hard gate: schema migrated, detect() works on test fixtures, registry round-trips. Do NOT change API routes. Do NOT touch frontend.

**A3: Slot util + flag merge**
- Owns: `src/hal0/config/schema.py` (only `extra_args` addition), `src/hal0/launchers/flag_merge.py` (new), `src/hal0/launchers/__init__.py` if needed, tests.
- Tasks: add `[server].extra_args` to `SlotConfig`. Build `merge_flags()`. Wire it into the llama-server launcher's arg-building path (find existing arg-build site, prepend merged result). Unit tests for merge edge cases (no-op, dedup, append-list, malformed).
- Hard gate: extra_args persists in slot TOML, merge_flags() tested, launcher uses it. Do NOT touch API routes. Do NOT touch frontend.

### Phase 2 — Backend API + frontend modal scaffolding (parallel, 3 agents, after Phase 1)

**B1: Models API**
- Owns: `src/hal0/api/routes/models.py`, related tests.
- Tasks: `POST /api/models/scan/preview` (new). Extend `POST /api/models/scan` to accept user-edited rows. Extend `PUT /api/models/{id}` for new editable fields. Cascade `DELETE` with ordered events. Emit `model.*` events via `await request.app.state.events.emit(...)` (EventBus from footer-backend-events).
- Hard gate: endpoints + tests + event emission. No frontend. Depends on footer-backend-events being merged or rebased on top of it for the EventBus import.

**B2: Slot edit modal expansion**
- Owns: `ui/src/views/Slots.vue` (Edit modal section only), no SlotCard, no Models.vue.
- Tasks: Advanced disclosure with grouped fields, ⟳ restart icons, `extra_args` field, effective-flags preview block. Filter model dropdown by `slot.backend ∈ model.backends`. Empty-state CTA. Auto-fill ctx_size from `model.defaults.context_size`. Save-dispatch logic (swap vs config + restart prompt). Remove standalone Swap modal block (lines ~778-815).
- Hard gate: modal works against existing backend (A3's `extra_args` field). Do NOT touch SlotCard. Do NOT touch Models.vue.

**B3: Models page Edit modal + Add Local-file tab**
- Owns: `ui/src/views/Models.vue` only.
- Tasks: rename Pull → Add. Add Local file tab with Register-single + Scan-directory sub-actions + preview table. Per-row Edit action → modal with name/capabilities/backends/defaults/re-detect. Per-row badges for capability + backend. Cascade-aware delete confirm copy.
- Hard gate: page works against B1's endpoints. Do NOT touch Slots.vue or SlotCard.

### Phase 3 — Polish + cleanup (parallel, 3 agents, after Phase 2)

**C1: SlotCard inline swap**
- Owns: `ui/src/components/SlotCard.vue` only.
- Tasks: inline popover dropdown (Teleport to body) for model swap. Wired to `/swap`. Filter by slot's backend. Position-aware (above/below based on viewport).
- Hard gate: works inside Slots.vue grid + footer's dense Slots tab.

**C2: Drop per-slot SSE → events ring subscription**
- Owns: `ui/src/views/Slots.vue` (subscription wiring only, NOT modal — B2 owns), `ui/src/composables/useEvents.js` if footer's not landed yet, or just consume it.
- Tasks: remove `openSlotStream`/`closeSlotStream` calls + `EventSource` per-slot setup. Subscribe to events ring filtered by `slot.state`. Keep polling as safety net.
- Hard gate: state still updates within 1s. No regression in lifecycle UI.

**C3: Playwright smoke + a11y pass**
- Owns: `tests/playwright/models-slots.spec.ts` (new), runs against dev server.
- Tasks: smoke flows — add model (local file), edit slot Advanced, swap via inline dropdown, cascade-delete model. A11y check on new modals (labelledby, focus trap, escape). Web-design-guidelines pass.
- Hard gate: tests pass against the dev stack.

## Landed surfaces — beyond the original spec

The phases above shipped, and three follow-on changes have layered on
top. Document them here so the plan continues to reflect what the code
actually does.

### Capabilities overlay (`src/hal0/capabilities/`)

Thin operator-facing overlay that groups slots into three dashboard
cards — Embed, Voice, Img — plus an NPU backend rollup. Children map
1:1 onto underlying slots via a hard-coded bridge in
`orchestrator.py`:

| Capability child | Underlying slot |
|---|---|
| `embed.embed`   | `embed`        |
| `embed.rerank`  | `embed-rerank` |
| `voice.stt`     | `stt`          |
| `voice.tts`     | `tts`          |
| `img.img`       | `img`          |

Persistence is a single TOML at `/etc/hal0/capabilities.toml`, written
atomically via `write_toml_atomic`. The schema is one
`CapabilitySelection` (`backend, provider, model, enabled`) per
`[selections.<slot>.<child>]` table. Operators may hand-edit the file;
the orchestrator reconciles on next apply (see "drift invariant"
below). The full design call is captured in
[ADR-0002](./adr/0002-capabilities-overlay.md).

HTTP surface lives at `src/hal0/api/routes/capabilities.py` (mounted
under `/api/capabilities`, admin-gated):

- `GET  /api/capabilities` → `{backends, catalogs, selections}`. The
  `catalogs` block carries one picker row per model grouped by id with
  a `backends` array for legal backend choices (model-first reshape,
  b90a569). `chat.chat` is included so the NPU backend card can list
  chat-on-NPU models even though chat itself lives on the dedicated
  `primary` slot.
- `POST /api/capabilities/{slot}/{child}` → accepts a subset of
  `{backend, provider, model, enabled}`. Unknown keys 400 with
  `capability.unknown_fields`. Error envelopes documented in
  [api-errors.md](./api-errors.md#capability-slots-envelope-apicapabilities).

CLI:

- `hal0 capabilities migrate [--dry-run]` (`src/hal0/cli/capabilities_commands.py`)
  rewrites persisted selections whose `(model, backend)` pair is
  illegal against the live catalog. Cross-referenced in
  [migration.md](./migration.md#cleaning-stale-capability-selections-hal0-capabilities-migrate).

### Drift invariant (`CapabilityOrchestrator.apply()`, commit 39adaf7)

`capabilities.toml` and the underlying `slots/*.toml` can drift
independently — a previous `apply()` that failed mid-flight, a manual
TOML edit, or an install-time seed can leave the two disagreeing.

The invariant `apply()` now enforces: **whenever the merged selection
is enabled, the underlying slot TOML is rewritten unconditionally** —
not just when the new selection differs from the previously-persisted
one. Diffing against the prior selection misses drift introduced
outside the orchestrator and lets `load()` / `swap()` spawn against a
stale slot TOML while the API reports the new selection as live.

The rewrite routes through `SlotManager.update_config()` so the
override drop-in + env file get regenerated alongside the TOML. If the
slot file doesn't exist yet, `_ensure_slot_exists()` synthesises it
from the selection before the lifecycle call.

### Scan preview overhaul (`POST /api/models/scan/preview`)

`src/hal0/api/routes/models.py:148` returns `DetectionResult` rows
without mutating the registry. The overhaul shipped in commits
7f64987, 8232989, cf5b791:

- **GGUF magic-byte detect** — `src/hal0/registry/gguf_header.py`
  reads the `b"GGUF"` magic + arch + pooling-type keys; runs on every
  candidate regardless of file extension so HF-cache `blobs/<hex>`
  files are still classified.
- **`general.name` → `suggested_name`** — when present in the GGUF
  header, surfaced as the row's suggested model name; falls back to
  `general.basename` and then to the filename.
- **`kind`-driven gating** — `DetectionResult.kind` is one of
  `gguf_chat / gguf_embed / gguf_rerank / asr / tts / image / ...`
  and the preview-row signature is `(suggested_name, size_bytes, kind)`
  so reblobbed HF snapshots dedup across `snapshots/<rev>/` directories.
- **Skip rules shared with discover** — `is_skippable()` from
  `registry/discover.py` keeps mmproj sidecars, multi-file shards, hex
  blobs without GGUF magic, and ComfyUI/voice accessory dirs out of
  the preview list.
- **Resolved-path dedup** — symlinks are resolved before
  signature-dedup; preserves the user-facing filename for display
  while collapsing the snapshot/blob duplicates.

`POST /api/models/scan` consumes the (possibly user-edited) preview
rows; each commit fires a `model.registered` event with
`source="scan"`.

### Rerank slot

- Slot name: `embed-rerank` (auto-created on first
  `embed.rerank.enabled = true` apply via `_ensure_slot_exists`).
- Default port: `8086` — picked by `_next_free_slot_port()` once
  `8081..8085` are taken; explicit setting recommended in the slot
  TOML.
- Model: `bge-reranker-v2-m3-q4_k_m` (seeded as `bge-reranker` in
  `src/hal0/registry/seeds/haloai_models.json`).
- Required server flag: `[server].extra_args = "--reranking"`.
  llama-server only registers the `/rerank` route when this flag is
  present; without it the slot will appear `ready` but `/v1/rerankings`
  dispatch returns the upstream's 404.
- Route: `POST /v1/rerankings` — plain dispatch passthrough
  (`src/hal0/api/routes/v1.py:260`).

### FLM provider live (NPU, `src/hal0/providers/flm.py`)

AMD XDNA NPU backend. The toolbox image
`ghcr.io/hal0ai/hal0-toolbox-flm:v1` is self-contained: FLM ships
under `/opt/fastflowlm/` inside the image (no host bind-mount path
needed since commit c998106).

Catalog surface (`src/hal0/capabilities/catalog.py`):

- `available_backends()` only surfaces `npu` when XDNA is present
  **and** `docker image inspect ghcr.io/hal0ai/hal0-toolbox-flm:v1`
  returns 0 — avoids the "no credentials → docker pull loop" failure
  mode for users without ghcr auth.
- `flm_served_models()` runs `flm list -j` inside the toolbox image
  and caches the parsed model list. FLM has its **own model tag
  namespace** (driven by the in-image `model_list.json`) — arbitrary
  GGUFs cannot be loaded onto the NPU. The picker only fan-outs `npu`
  rows for the `chat` and `embed` capabilities (whisper / asr on FLM
  is a deferred slice).
- Per-`(backend, model)` validation in `_validate_model_in_catalog()`
  rejects `backend=npu` + a llama.cpp-only model at apply time with
  `capability.illegal_backend_model_pair`.

### Capability error envelopes

All `capability.*` codes are catalogued in
[api-errors.md → Capability slots envelope](./api-errors.md#capability-slots-envelope-apicapabilities).

## Risk + safety

- **No commits / pushes** by any agent. Each agent leaves its worktree branch ready for review.
- **No `git add -A`** — split file ownership ensures no two agents touch the same file. If an agent finds overlap, STOP and report.
- **Worktree base**: main worktree pinned to `main` before Phase 1 spawn so agent branches fork from `main`, not from a feature branch.
- **Caveman tone** in all agent prompts + outputs.
- **No scope creep**: each agent has a hard gate. Recommendations beyond the gate go in the report, not the code.

## Footer work — DO NOT TOUCH (built in parallel sessions)

Existing worktrees own the footer surface. All Phase 2 / 3 frontend agents are forbidden from editing these files:
- `ui/src/components/footer/**` — `Footer.vue`, `FooterBar.vue`, `FooterPane.vue`, `ActivityTicker.vue`, `ProgressChip.vue`, `tabs/*`
- `ui/src/composables/useEvents.js` — consume only via `import { useEvents } from '@/composables/useEvents'`
- `ui/src/composables/useAutoscroll.js`
- `ui/src/stores/footer.js`
- `ui/src/views/Dashboard.vue` — footer cleanup is owned by footer PR
- `ui/src/views/Logs.vue` — footer PR keeps as-is per scope

Backend equivalents:
- `src/hal0/events/` — owned by `footer-backend-events`
- `src/hal0/api/routes/events.py` — owned by `footer-backend-events`
