# Plan: model/slot/profile pipeline + models UI — fixes & polish

**Status:** EXECUTION PLAN — researched against the tree at `4f1d41b` (v0.8.4b1).
**Scope:** merges two analyses: (a) the model storage/listing/tagging/pull +
upstream-identification review, and (b) the model/slot/profile pipeline & UI
audit (dead launch path, preview drift, taxonomy sprawl, per-backend gaps,
flag-storage correctness, UI findings, runtime robustness).
**Coordination:** implementation is happening on `claude/new-session-09hvwe`.
The upstream-vs-local UI identification is ALREADY SHIPPED on
`claude/model-storage-ui-bguvd1` (draft PR #1035) — rebase over it before
touching `models.jsx`, `slot-modals.jsx`, `inference-pane.jsx`, or
`lib/normalizeApiModel.ts`, all of which it modifies.

Each workstream below is sized to one PR. "Fix" sections are prescriptive:
they name the mechanism that already exists in-tree to build on, not just the
symptom.

---

## Tier 0 — shipped / in flight

### WS-0 · Upstream-vs-local identification (DONE, PR #1035)
Upstream-advertised rows (`installed=false` + `upstream=<name>`) now get their
own "Upstream · remote" catalog section, an `upstream` chip, an `origin` cell
in the detail pane, a served-remotely note instead of Pull/View-on-HF, and are
excluded from all slot model pickers via `isUpstreamModel()` in
`ui/src/lib/normalizeApiModel.ts`. e2e: `models-upstream-v3.spec.ts`.

**Follow-ups folded into later workstreams:** explicit `origin` field on the
API row (→ WS-7), a Connections-view panel consuming the orphaned
`useEndpoints()` hook (→ WS-19).

---

## Tier 1 — quick wins (small diffs, immediate user value)

### WS-1 · Fix divergent model normalizer hiding models from slot pickers (BUG)
- **Problem:** `slot-modals.jsx:59-67` derives `type: 'llm'` only from
  `chat|coding` capabilities; the canonical `lib/normalizeApiModel.ts:41-49`
  also maps `tool-calling|vision`. A tool-calling-only or vision-only chat
  model is `type:''` in `compatibleModels()` and invisible in every slot
  picker while rendering normally in the Models pane.
- **Fix:** delete the local `normalizeApiModel` copy in `slot-modals.jsx` and
  import the TS one (the file already uses ESM imports — see its
  `@/api/hooks/*` imports — so `import { normalizeApiModel } from
  '@/lib/normalizeApiModel'` is mechanical). While there, dedupe
  `deriveDevice` and the redundant `fmtBytes` copies against the same module.
  Note `useModels()` already normalizes rows, so the double-normalize in
  `compatibleModels` can drop to a plain pass-through once callers are audited
  (create modal + edit drawer + swap popover all consume `useModels`).
- **Test:** unit-level: a row with `capabilities:['tool-calling']` classifies
  `llm`. e2e: extend `models-upstream-v3.spec.ts`'s create-slot-picker test
  pattern — inject a tool-calling-only row via the `HAL0_DATA` init-script
  hook and assert it appears as an option.
- **Conflict note:** PR #1035 adds `isUpstreamModel` to `compatibleModels` —
  rebase first.

### WS-2 · Make the resolved-command preview the launch truth
- **Problem:** `_resolve_slot_argv` (`providers/container.py:1008-1058`)
  re-assembles argv separately from `_llama_launch_plan` (`container.py:338`)
  and omits the slot `mtp` override, `--chat-template-file`, `--mmproj`, and
  the `--ctx-size` fallback from `_resolve_context_size`. Preview uses
  `resolve_argv`, launch uses `normalize_argv`; drift detection
  (`manager.py:1313`) uses `container_spec`, so preview and drift already
  disagree. The provenance drawer (`slot-modals.jsx:1218-1348`) presents the
  preview as auditable truth.
- **Fix:** `slots/argv.py` already exports
  `resolve_argv(segments: list[tuple[str, list[str]]])` — a labelled-segment
  resolver. Extract the segment assembly out of `_llama_launch_plan` into a
  shared `_llama_argv_segments(...) -> list[tuple[str, list[str]]]` (labels:
  `base`, `profile`, `chat-template`, `mmproj`, `extra_args`), have
  `_llama_launch_plan` flatten+`normalize_argv` it, and have
  `resolved_argv_detail_for_slot` feed the *same* segments (with the same mtp
  override and resolved context size) to `resolve_argv` for provenance. One
  assembler; preview, launch, and drift all derive from it.
- **Also:** route drift comparison through `argv.FLAG_ALIASES`
  canonicalization (`manager.py:135` + `:3236`) so `--batch-size` vs `-b`
  stops reporting false drift.
- **Test:** golden test asserting `flatten(segments) == launch argv` for a
  slot with mtp+chat-template+mmproj+extra_args; regression test for `-b` vs
  `--batch-size` drift.

### WS-3 · Wire the real FLM health probe into the container path
- **Problem:** `FLMProvider.health` (`providers/flm.py:332`) does a real
  inference probe (written because `/v1/models`-only was a known bug), but
  container-managed NPU slots always use the weak generic
  `ContainerProvider.health` (`container.py:637-665`).
- **Fix:** in `ContainerProvider.health`, delegate to
  `_spec_provider_for(slot_cfg)`'s health when the spec provider defines one
  (the FLM/Kokoro/Qwen3-TTS/ComfyUI dispatch mechanism already exists at
  `container.py:408`); fall back to the generic HTTP check otherwise.
- **Bundle the small FLM hygiene items** while in the file: unify the two
  hardcoded install roots (`flm.py:57` vs `:196`), delete the unread
  `_FLM_PROBE_OK` global, add a TTL to the catalog cache, add the missing `T`
  unit to `parse_flm_progress` (`flm.py:712`).

### WS-4 · One port-range constant
- **Problem:** `_next_free_slot_port` scans 8081–8099
  (`api/routes/slots.py:230`), the schema allows ≤8200 (`schema.py:95`),
  `stats.py` documents a third range.
- **Fix:** define `SLOT_PORT_RANGE = range(8081, 8200)` in one module
  (`hal0/config/schema.py` or `hal0/slots/__init__`), import everywhere,
  assert schema bounds from it.

### WS-5 · Backend-switch surface: finish or kill (recommend: kill for now)
- **Problem:** `useSlotBackend` (`useSlots.ts:485`) has no caller; the
  `backend_mismatch` chip (`slots.jsx:311-319`) tells the user to switch with
  no affordance; server-side `_backend_build_present` is a stub always
  returning True (documented 409 unreachable) and `actual_backend` is
  hardcoded `None` (`slots.py:962-972,1080,1125`).
- **Fix (recommended):** since backend identity is now expressed via profiles
  (the switch endpoint predates that), remove the endpoint's vestigial
  response fields + the dead hook, and make the chip's remediation "open the
  profile picker" (which is the real switch mechanism). Finishing the legacy
  endpoint would re-add a second way to express what profiles already own.
- **Also:** drop the four duplicate NPU endpoint aliases in
  `ui/src/api/endpoints.ts:64-77` (their TODOs already say so).

### WS-6 · Chat-template pick at pull time
- **Problem:** MMPROJ is selectable in the Add-by-HF modal but chat template
  is only settable post-hoc in the Recipe editor
  (`model-modals.jsx:539-556`).
- **Fix:** UI-only is enough: reuse the Recipe editor's `useChatTemplates`
  select in `AddByHfModal`, and on pull completion issue the same
  `PUT /api/models/{id}` (`useModelUpdate`) with
  `defaults.chat_template` — `usePullJob` already exposes the completion
  event to sequence it. (Backend alternative — accepting `defaults` in
  `POST /{id}/pull` — is cleaner long-term but touches
  `_resolve_pull_source_with_body`; do it only if already in there for WS-11.)

---

## Tier 2 — medium (one PR each)

### WS-7 · Canonical device/backend taxonomy + `/api/meta/enums`
- **Problem:** ≥5 backend vocabularies (`schema.py:45,52`, profile
  `runtime_family`/`device_class`, `model_meta.device_to_backend`), three
  duplicated backend↔device maps (`schema.BACKEND_TO_DEVICE:67`,
  `api/routes/slots.py:952`, `model_meta:238`), `DEVICE_DEFAULT_PROFILES`
  duplicated with different CPU behavior (`stacks.py:149`), three
  unknown-value policies, and five more hand-copied spellings in the UI
  (`models.jsx:165`, `model-modals.jsx:260` incl. a dead `cuda` toggle,
  `profiles.jsx:31`, `stacks.jsx:33`, `devKind` ×3).
- **Fix:** `hal0/model_meta/` already exports `canonical_device`,
  `device_to_backend`, `device_to_legacy_backend`, `classify` — finish the
  consolidation there: move `BACKEND_TO_DEVICE` + `DEVICE_DEFAULT_PROFILES`
  in, pick ONE unknown policy (recommend: passthrough + logged warning, since
  silent `cpu` coercion masks typos), and delete the route/stacks copies.
  Then add `GET /api/meta/enums` returning `{devices, backends, slot_types,
  capabilities, device_classes, curated_model_tags}` and a `useMetaEnums()`
  hook; replace the UI literals (incl. the three capability vocabularies in
  `model-modals.jsx:202/258/965` — `embed` vs `embeddings` etc.). Drop the
  `cuda` toggle or render it disabled-with-tooltip ("Phase 2,
  `recommend.py:124`").
- **Sequencing:** land before WS-16 (structured flag editor) and before any
  further UI vocab work; other UI PRs should stop adding literals now.

### WS-8 · Decide the fate of inert model/slot launch knobs (recommend: wire in)
- **Problem:** `model.defaults.extra_args` / `n_gpu_layers` /
  `rope_freq_base` and slot `[model].n_gpu_layers`/`rope_freq_base` are
  persisted, editable in the UI, and never read by the live launch — only the
  dead `LlamaServerProvider.merge_flags` consumed them. The `ModelDefaults`
  docstring (`registry/model.py:22`) still promises the merge.
- **Fix (recommended: option a — honor the contract):** in the WS-2 segment
  assembler add a `model-defaults` segment between `profile` and
  `extra_args`: `defaults.extra_args` verbatim, `n_gpu_layers → -ngl`,
  `rope_freq_base → --rope-freq-base`. Last-wins dedup via `normalize_argv`
  keeps slot `extra_args` authoritative. The UI already renders these as
  launcher defaults, so no UI change. If option (b — deprecate) is chosen
  instead: reject at `PUT /api/models/{id}` with a clear error, hide the
  fields in the Recipe editor, migration-strip on registry load.
- **Depends on:** WS-2 (segments).

### WS-9 · Boundary validation on slot config writes
- **Problem:** `PUT /config` and `PATCH /defaults` pass raw dicts to
  `update_config`; `SlotConfig`/`ModelConfig`/`ServerConfig` are
  `extra="allow"`, so `extra_arg`/`contextsize` typos persist silently.
  `ctx_size → context_size` is folded in three places
  (`slot_config/__init__.py:120`, `manager.py:1859`, `:3266`).
- **Fix:** one `normalize_slot_patch()` at the route boundary: fold known
  aliases (`ctx_size`), validate the patch against the pydantic models with
  `extra="forbid"` **for the patch only** (storage models stay
  `extra="allow"` for forward-compat), 400 with the unknown key named.
  Collapse the three `ctx_size` folds into it.

### WS-10 · Stacks write through the guarded path
- **Problem:** `stacks/apply.py:184-226` merges slot TOML itself — skipping
  the `ctx_size` fold, `_reconcile_device_profile`, NPU-exclusivity and
  default-uniqueness guards — and writes a different field set (incl. legacy
  `backend`) than the other two writers. Locking is asymmetric
  (`SlotConfigStore.transaction` locks only `capabilities.toml`); `create()`
  has a check-then-write TOCTOU (`manager.py:1773-1780`).
- **Fix:** route stack slot mutations through `SlotManager.update_config` /
  `create` (or extract the guard set into a shared function both call). Take
  the slot-TOML write lock in `update_config`/`create`/
  `_persist_model_default` (the flock sidecar mechanism already exists in
  `registry/store.py:85` — mirror it). Close the TOCTOU under the same lock.
- **Also:** stacks UX identity fixes from the audit if small enough, else
  split: `buildVM`'s `profile: sl.profile || sl.device` conflation
  (`stacks.jsx:68`), profile selects unfiltered by device class (`:517`), no
  `img` device option, three independent MTP toggles with gating only at
  slot level.

### WS-11 · Multi-file pulls: fetch MMPROJ with the model
- **Problem:** `run_pull` (`registry/pull.py:532`) streams exactly one file.
  The Add-by-HF modal *requires* an mmproj pick for vision models and passes
  `mmproj_filename`, but curated/detail-pane pulls fetch only the main GGUF;
  the mmproj association then depends on a later directory scan
  (`discover.py:166-255`). Curated multi-file pulls are an acknowledged TODO
  (`curated.py:20-62`).
- **Fix:** generalize `PullJob` to a file list (`files: [{hf_filename,
  dest, bytes_total, bytes_done, sha256}]`), aggregate progress across files
  (SSE shape keeps top-level `bytes_downloaded/bytes_total` so the UI needs
  no change), download mmproj after the model into the same directory, and
  set `model.mmproj` in `_register_pulled` directly instead of waiting for a
  scan. Resume sidecars are already per-file (`<id>.part.json`) — key them by
  filename. Extend `POST /{id}/pull` source resolution to carry
  `mmproj_filename` through `_resolve_pull_source_with_body`
  (`models.py:1253`).
- **Test:** pull a two-file fixture over the mocked HF endpoint; assert both
  files land, registry row has `mmproj` set, progress is monotonic across the
  file boundary; cancel mid-second-file resumes correctly.

### WS-12 · Verify pull integrity against an expected hash
- **Problem:** SHA-256 is computed while streaming and *recorded*
  (`pull.py:583,832`) but never compared to anything.
- **Fix:** `POST /api/models/inspect` already talks to the HF API — extend it
  (or the pull-source resolution) to capture the LFS object sha256 that HF
  exposes per file (`x-linked-etag` on the resolve redirect / `lfs.oid` in
  the repo tree API). Store as `expected_sha256` on the job; on stream
  completion compare and fail the job with a `pull.checksum_mismatch` error
  code (new entry in the `model.*`/`pull.*` error namespace), leaving the
  `.part` for diagnosis. When HF provides no hash (non-LFS files), keep
  today's record-only behavior.

### WS-13 · Models catalog: sorting, quant, date, tag filters
- **Problem:** no sort control anywhere (registry is id-sorted, UI only
  groups); quant is not a structured field (`gguf_header.py` doesn't read
  it; `meta.json.quant` is always `None`); `created` is emitted but never
  rendered; tags are edit-only, never a browse dimension.
- **Fix (client-heavy, backend-light):**
  - UI sort dropdown in the `.mdl-toolbar` (name / size / params / date,
    asc-desc), applied within each existing section — all fields are already
    on the rows.
  - Backend: extract quant at registration in `detect.py` — regex the
    filename (`(?i)(i?q\d[_a-z0-9]*|f16|bf16|f32)`) and/or read
    `general.file_type` in `gguf_header.py` — store as `Model.quant`,
    surface in `_model_to_dict`, render as a chip + filter.
  - Render `created` as an "added" column/detail cell.
  - Tag filter chips in the toolbar sourced from the curated tag vocab
    (via WS-7's `/api/meta/enums`), matching `Model.tags`.
  - Add `origin: "local"|"upstream"` to `_model_to_dict`/upstream rows
    (`models.py:213-293`) so clients stop inferring from
    `installed`+`upstream` (WS-0 follow-up; keep the old fields).

### WS-14 · Runtime robustness batch
One PR of small verified fixes in `slots/manager.py` + friends:
- Seed `_last_used` on adopt/reconcile (`manager.py:2708,2791,3017`) so
  idle-TTL/pressure eviction sees adopted slots.
- Add WARMING to `_FAIL_WATCH_LIVE_STATES` (or a warmup-deadline watcher) so
  a slot stuck after `_await_ready` timeout isn't a monitoring blind spot.
- mtime-keyed cache in `iter_configs` to stop `resolve_for_request`
  re-reading every slot TOML twice per routed request
  (`manager.py:1488,1518,1527`).
- Registry outage → explicit `model.registry_unavailable` error instead of
  silently assuming "model cached" (`manager.py:2437,2289`).
- Align capacity-math default ctx (`capacity.py:106`, 64k) with the
  launcher's fallbacks (`container.py:469-470`, 8k/32k) — one shared
  constant.
- Fold `flag_merge.py` into `argv.py` (full aliasing, fixes `-b 8192`
  mis-tokenization; only live consumer is the MTP bundle merge) and delete it.

---

## Tier 3 — structural (design sign-off first)

### WS-15 · Retire the dead launch path
Delete `LlamaServerProvider` launch machinery (`providers/llama_server.py:104`
— backend→binary selection, `HAL0_BACKEND`, `_HAL0_TOOLBOX_IMAGES` which now
diverges from `SEED_PROFILES`, device filtering, `-ngl` emission,
`merge_flags`) and `base.render_systemd_override` (`base.py:292-400`,
type-incompatible with `Mount`). **Precondition:** WS-8 has landed (so the
only still-referenced behavior, the model-defaults merge, lives in the live
path) and WS-19's device-existence filter has been ported out of it. Keep
whatever test fixtures still assert argv shapes by pointing them at the
segment assembler from WS-2.

### WS-16 · Structured flag editing in the profile UI
The most-tuned knobs (`-ngl`, rope, batch, flash-attn, cache-type) are
read-only "defined by profile" in the slot drawer
(`slot-modals.jsx:1164-1183`) while the profile editor is one opaque textarea
(`profiles.jsx:358`). Add a structured overlay on the profile form: parse the
flag string through the `argv.py` alias table into known fields + "other
flags" remainder, edit fields, serialize back. Expose the alias table via
WS-7's meta endpoint (or a `flag_schema` key on it) so the UI doesn't
hand-copy flag names. Storage model unchanged (still one flag string).

### WS-17 · Per-slot env + GPU pinning
- `[server].env` table on the slot schema → `RuntimeLaunchPlan.env`
  (`container.py:394-405` currently builds with no env) → rendered into the
  unit. Unlocks `HSA_OVERRIDE_GFX_VERSION` etc. without forking images.
- Per-slot `gpu_index` mapped to the right visibility env per runtime family
  (`HIP_VISIBLE_DEVICES` / `GGML_VK_VISIBLE_DEVICES`) — cheap future-proofing
  for dGPU/multi-GPU hosts; no behavior change when unset.

### WS-18 · Device-class gating of GPU passthrough
`container_spec` unconditionally injects GPU device nodes/groups
(`container.py:605-606`) — a `cpu-llm` slot gets `--device=/dev/kfd` if
present, and `_gpu.py:56-57` falls back to legacy dir strings on non-GPU
hosts. Gate on the profile's `device_class` (`cpu` → no GPU plumbing) and
filter device paths by `Path.exists()` (port the filter from the dead
provider before WS-15 deletes it). Also parameterize the Strix-Halo constants
behind the hardware probe (accel/render node names `flm.py:307`, AIE column
cap `npu_columns.py:151`, fallback GIDs `_gpu.py:73-79`, model paths inside
seed-profile flag strings `schema.py:778,787`) when a second hardware target
becomes concrete — not before.

### WS-19 · UI polish batch
Bundle of small verified items (one PR, mostly mechanical):
- Connections view: render remote upstreams via the orphaned `useEndpoints()`
  (`useSlots.ts:370-377`) — endpoint name, kind, health, advertised-model
  count; links the WS-0 upstream story end-to-end.
- `useProfiles` gets a `refetchInterval` (`useProfiles.ts:73`).
- Mock allowlist: add `/api/profiles` + `/api/stacks` so forced-mock mode
  doesn't break the create-slot flow.
- Replace native `window.confirm` (swap/dirty-close) with the
  `ConfirmDialog` primitive.
- Pull progressbar `aria-valuenow` wired to actual pct.
- Remove the injectable demo "sha256 mismatch" banner from the production
  render path (`slots.jsx:811-814`).
- `list`/`spec` slot-card variants: drop never-populated `rpm/xrt/avg`
  metrics; either wire lifecycle handlers or render their buttons disabled
  (`slots.jsx:766-770`).
- `enable_thinking` toggle gets a "applies per-request — no restart" hint
  (asymmetric with `mtp`, which restarts).
- Unify the two model-store-root defaults (`/mnt/ai-models` bind-mount vs
  `models_dir()` pull default via `effective_store()`,
  `config/paths.py:140-143` vs `pull.py:160`) — pick one, document it in the
  install docs.

---

## Suggested sequencing

```
Tier 1 (parallel-safe): WS-1 ─ WS-3 ─ WS-4 ─ WS-5 ─ WS-6
                         WS-2 ──► WS-8 ──► WS-15
Tier 2: WS-7 ──► WS-13 (enums), WS-16 (flag schema)
        WS-9 ──► WS-10 (shared boundary normalizer)
        WS-11 ─ WS-12 (both touch pull.py — same author or sequential)
        WS-14 (independent)
Tier 3: WS-15, WS-17, WS-18 (WS-18's existence filter ported before WS-15
        deletes its source), WS-19 (independent)
```

Rebase note for the implementing session: PR #1035
(`claude/model-storage-ui-bguvd1`) touches `models.jsx`, `slot-modals.jsx`
(`compatibleModels`), `inference-pane.jsx` (`ModelPicker`), and
`normalizeApiModel.ts` — WS-1, WS-7, WS-13, WS-19 all overlap those files.

## Test strategy

- **Backend:** every WS that touches argv/launch gets a golden-argv test
  against the WS-2 segment assembler; pull changes run against the existing
  mocked-HF fixtures (`tests/` has `test_models_routes.py` patterns to
  extend); taxonomy consolidation gets a one-shot exhaustive-mapping test
  (every legacy backend ↔ device ↔ profile default round-trips).
- **UI:** dependency-free `.mjs` unit tests for pure helpers (pattern:
  `ui/src/dash/__tests__/model-types.test.mjs`); Playwright specs for
  user-visible changes using the forced-mock `HAL0_DATA` init-script
  injection pattern established in `models-upstream-v3.spec.ts`. In this
  environment, bridge the browser revision via the
  `/opt/pw-browsers/chromium_headless_shell-*` symlink trick (see PR #1035
  session) before running specs.
