# Stacks — Design Spec

- **Status:** Implemented / Shipped (v0.8.x) — Stacks subsystem (`src/hal0/stacks/`: schema/catalog, `StackApplyEngine` apply+drift+converge, `portable.py` export/import/snapshot), seed stacks (saber/forge/pi), and `stack_*` MCP/REST tools all landed. (#921, #923, #925, #926, #964) This spec is retained for historical/design context.
- **Date:** 2026-06-19
- **Author:** Alexander (via Claude)
- **Target repo:** `hal0` (runtime + `ui/` dashboard). Docs page lands in `hal0-web`.
- **Branch:** `feat/stacks-spec`

## 1. Summary

A **Stack** is a named, portable, editable bundle that captures *"what models are
serving, and how"* — a curated grouping of slots, their profiles, their model
assignments, and their capability selections. Stacks can be created, edited,
cloned, applied (loaded), snapshotted from live state, exported to a shareable
file, and imported. They are the runtime, user-facing successor to the existing
first-run-only **Bundles** picker (`src/hal0/bundles/`).

The feature is a quality-of-life multiplier: instead of hand-configuring eight
slots one at a time, a user loads "Coding" or "Voice" in one action, with a
diff preview before anything changes. Exported stacks let users share working
configurations with each other; the export format is shaped so a future public
**directory of stacks** is "just a server that lists these files."

### Approved decisions (brainstorming, 2026-06-19)

| Decision | Choice |
| --- | --- |
| **Composition** | Curated bundle — user hand-picks slots + profile + model + capabilities. Carries model **ids + registry metadata**, never weights. Embeds referenced custom profiles. |
| **Apply mode** | Declarative / replace — applying makes the system *match* the stack; un-named slots are unloaded (config preserved). Always shows a dry-run diff first. |
| **Import resolve** | Resolve & offer to pull — diff model refs vs local registry; offer one-click pull for missing models that carry `hf_repo`; flag unresolvable refs. |
| **Scope reach** | Inference surface only — slots, profiles, model assignments, capabilities. No providers/upstreams, dispatcher, memory, telemetry in v1. |
| **Drift layer** | In v1 — active-stack pointer + clean/modified drift status + "update from live" / "re-apply". |

## 2. Goals / Non-goals

**Goals**
- Group slots + profiles + model assignments + capability selections into a named, editable unit.
- One-action **apply** with a mandatory dry-run diff preview and transactional, reversible commit.
- **Snapshot** the current live config into a new stack.
- **Export / import** as a single portable `.hal0stack.json`, secrets excluded by construction.
- **Resolve & pull** missing models on import via the existing pull engine.
- Track the **active stack** and surface **drift** (live config diverged from applied stack).
- Ship a handful of curated **seed stacks** (clone-only) for day-one value.

**Non-goals (v1)**
- No public stack directory/registry (format is shaped for it; the service is out of scope).
- No bundling of GGUF weights on the default export path (optional local-backup mode only).
- No providers/upstreams, dispatcher, memory, or telemetry config in a stack.
- No cross-version downgrade migrations (forward-only, matching existing framework).

## 3. Data model

A Stack composes existing primitives **by reference**, with two deliberate
exceptions (profiles embedded, model metadata embedded) so a stack survives
transport to another machine.

```
Stack
├─ meta:           name, slug, description, author, icon/accent, tags[]
├─ schema_version, hal0_version          # provenance
├─ slots[]:        for each included slot →
│                    slot config fields (device, provider, role, vision, mtp,
│                                        enable_thinking, server.extra_args, npu, image)
│                    + profile  (by name → resolved against embedded profiles{})
│                    + model    (model id → resolved against embedded models{})
│                    + capabilities[]  (the (slot, child) device/provider/model/enabled rows)
├─ profiles{}:     embedded ProfileConfig for each referenced profile name
│                    (so a custom profile travels with the stack)
└─ models{}:       registry METADATA per referenced model id
                     (id, name, hf_repo, hf_filename, quant, size_bytes,
                      capabilities[], backends[], mmproj) — metadata only, no weights
```

**Why two binding layers.** Slots already reference models by id string
(`config/schema.py:149`) and profiles by name (`config/schema.py` SlotConfig
`profile`). A stack keeps those references intact, and additionally carries:
- an **embedded profile** per referenced name — because a shared stack that only
  referenced a custom profile would import broken on a box that lacks it;
- a **model-metadata sidecar** per referenced id — because the importer needs
  `hf_repo`/`hf_filename` to offer a pull when the model is absent.

**Excluded by construction.**
- **GGUF weights** — models bind by id (`registry/model.py:54`), weights live
  separately under the model store and are multi-GB.
- **Secrets** — provider/upstream auth is stored as env-var *names* in `api.env`
  (`config/schema.py:678`, `api/_env_store.py`), never in slot/profile/capability
  config. The inference-surface scope never touches them, so exports carry no secret.
- **Machine-specific absolute paths** — model `path` / model roots are *never
  trusted from an import*; the importer always re-resolves by id against the
  local registry (see §6).

### Pydantic models (new)

Mirrors `ProfileCatalog` (`src/hal0/profiles/__init__.py:129`):

- `StackSlotEntry` — one slot's contribution (config fields + profile name + model id + capability rows).
- `StackConfig` — meta + `schema_version` + `slots: list[StackSlotEntry]` + `profiles: dict[str, ProfileConfig]` + `models: dict[str, StackModelMeta]`.
- `StackModelMeta` — the metadata subset of `registry/model.py:Model` safe to embed.
- `StacksCatalog` — CRUD + atomic TOML I/O + seed-immutability guard, mirroring `ProfileCatalog`.

Field validators reuse existing conventions: slug regex `^[a-z0-9][a-z0-9_-]{0,31}$`
(as `SlotConfig.name`, `config/schema.py:620`).

## 4. Storage & format

| Artifact | Path | Format | Written via |
| --- | --- | --- | --- |
| Stack (canonical) | `/etc/hal0/stacks/<slug>.toml` | TOML | `write_toml_atomic()` (`config/loader.py:69`) |
| Active pointer | `/var/lib/hal0/stacks/state.json` | JSON | `write_state_atomic()` pattern (`slots/state.py:269`) |
| Export/import wire | `<name>.hal0stack.json` | JSON | API download / upload |

Paths added to `src/hal0/config/paths.py` alongside the existing slot/profile/
registry path helpers.

**On-disk = TOML** to match every other hal0 config file and the atomic-write
discipline. **Wire = JSON** because it is web-native, diff-friendly, trivially
signable, and directory-ready.

### Wire envelope (`.hal0stack.json`)

```jsonc
{
  "kind": "hal0.stack",
  "schema_version": 1,
  "hal0_version": "0.7.x",
  "exported_at": "<ISO8601>",     // stamped by the API handler at export time
  "checksum": "sha256:…",         // over the canonicalized `stack` body
  "stack": { /* StackConfig with profiles{} and models{} embedded */ }
}
```

The envelope rides the **existing schema-version + migration framework**
(`src/hal0/config/migrations/`): on import, an older `schema_version` is walked
forward via `run_migrations()` (`config/migrations/__init__.py:75`) before validation.

## 5. Apply engine — declarative, two-phase, reversible

Applying a stack converges the system to match it. Built on the transactional
primitive that already exists.

**Phase A — config (atomic, reversible).** Compute one `ChangeSet` spanning every
affected `slots/<name>.toml` + `capabilities.toml` + any new `profiles.toml`
entries, via `SlotConfigStore.apply()` (`src/hal0/slot_config/__init__.py:163`).
`apply()` writes nothing — it returns before/after snapshots.
- `commit()` (`slot_config/__init__.py:200`) writes all files atomically with
  rollback-on-partial-failure.
- `revert()` (`slot_config/__init__.py:222`) restores the before snapshots.
- Slots **named** in the stack are configured (profile + model + capabilities + slot fields).
- Slots **not** in the stack are marked disabled for unload; their TOML is preserved.

**Phase B — lifecycle convergence.** Drive `SlotManager` to match the committed
config: load/swap named slots to their assigned models, unload the rest. Reuses
the exact lifecycle path `CapabilityOrchestrator.apply` already uses
(`src/hal0/capabilities/orchestrator.py:164+` → SlotManager).

**Dry-run diff preview (always).** Because Phase A is compute-only, the API and
UI render a real before→after diff before any commit (e.g. *"start `agent` on
ace-saber, swap `chat` to crown-halo, stop `img`"*). No surprise applies. This
is the safety rail that makes declarative/replace comfortable.

**Failure handling.** A Phase-A commit failure rolls back to `before` (existing
`SlotConfigStore` invariant). A Phase-B lifecycle failure on an individual slot
is surfaced per-slot (the slot state machine already models `ERROR`,
`slots/state.py:51`) without unwinding the committed config; the user sees which
slots converged and which need attention.

## 6. Import / export & portability

**Export.** Serialize the stack, embed each referenced `ProfileConfig`, embed
each referenced model's metadata subset, stamp `exported_at` + `checksum`, emit
`.hal0stack.json`. Two paths:
- **Export** (default) — lightweight, metadata-only, the sharing/directory path.
- **Export with weights** (guarded) — large `.tar` including GGUFs, for *local
  full-backup only*; visually de-emphasized and never the directory path.

**Import.** Parse + validate envelope → `run_migrations()` to current schema →
**resolve pass** diffing model refs against the local registry. Per model:
- ✅ **present** → bind by id.
- ⬇️ **missing, has `hf_repo`** → offer one-click pull via the existing pull
  engine (`src/hal0/registry/pull.py`), with progress over the existing SSE pull
  stream (`/api/models/{id}/pull/stream`).
- ⚠️ **missing, unresolvable** → flag; that slot imports disabled with a clear reason.

Embedded profiles are reconciled into `profiles.toml` on import (created if
absent; name-collision prompts clone-with-suffix, never silent overwrite).

**Machine-specific safety.** Absolute model `path`, model roots, and any host URL
are *never* applied from an import. The importer re-resolves every model by id
against the local registry, so a stack from another box cannot inject a bad path.

## 7. Active-stack tracking & drift detection

This is the layer that makes Stacks first-class rather than a one-shot importer.

- After a successful apply, `state.json` records the applied stack `slug` + a
  **content hash** of the applied slot/capability/profile set.
- A cheap reconcile compares a hash of the *current* live config against the
  applied stack's hash → **status**:
  - `clean` — live config equals the applied stack.
  - `modified` — the user hand-edited a slot/capability since applying (git-"dirty").
  - `none` — no stack is currently applied.
- UX payoffs:
  - Stacks page shows the **Active** stack and a **drift badge**.
  - `modified` invites **"Update stack from current state"** (capture live edits
    back into the stack) or **"Re-apply to discard drift."**
  - Slots page can show a small *"part of stack: Coding"* chip per slot.
- The same hashing/capture code powers **snapshot-to-new-stack** at create time.

## 8. Backend API + MCP

### REST — new `src/hal0/api/routes/stacks.py` (mirrors `routes/profiles.py`)

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/stacks` | list (with active flag + drift status) |
| POST | `/api/stacks` | create (hand-built or from snapshot payload) |
| GET | `/api/stacks/{slug}` | detail |
| PUT | `/api/stacks/{slug}` | edit |
| DELETE | `/api/stacks/{slug}` | delete (guard seed stacks) |
| POST | `/api/stacks/{slug}/apply?dry_run=true` | diff preview (returns ChangeSet) |
| POST | `/api/stacks/{slug}/apply` | commit + lifecycle convergence |
| POST | `/api/stacks/{slug}/export` | download `.hal0stack.json` |
| POST | `/api/stacks/import?dry_run=true` | validate + resolve report |
| POST | `/api/stacks/import` | create from uploaded envelope |
| POST | `/api/stacks/snapshot` | build a stack from current live config |

Endpoint constants added to `ui/src/api/endpoints.ts`; client hooks mirror
`ui/src/api/hooks/useProfiles.ts`.

### MCP admin tools — `src/hal0/mcp/admin.py` (existing gated-write pattern)

- **Autonomous read:** `stack_list`, `stack_status`.
- **Gated write (ApprovalQueue):** `stack_apply`, `stack_import`, `stack_delete`.

Lets Hermes/agents swap stacks under owner approval, consistent with
`slot_create`/`capability_set`/`config_write`.

## 9. Dashboard UI — `#slots/stacks` sub-page

A near-clone of the Profiles page (`ui/src/dash/profiles.jsx`).

- **Nav:** add a third Slots child in `useNavItems()` (`ui/src/dash/chrome.jsx:210`)
  + a `renderView` case in `ui/src/dash/main.jsx` for `slotParam === "stacks"`.
- **Card grid** (reuse `DCard`/`StatusDot`, `ui/src/dash/cards-shell.jsx`): each
  card shows accent, name, description, contained slots as chips, an **Active**
  ribbon + drift badge, and actions: **Apply** (→ diff-preview modal), Edit,
  Clone, Export, Delete. Seed stacks are clone-only (reuse the Profiles
  immutability guard, `profiles.jsx:78`).
- **Editor `Drawer`** (reuse `Drawer` + `FormRow`): name/desc/icon + a slot-picker
  (add slot → choose profile + model + capabilities), populated from
  `/api/slots`, `/api/profiles`, `/api/models`.
- **Import** = file-drop → import-dry-run → resolve report (missing models with
  Pull buttons) → confirm.
- **Apply** = diff-preview modal (the §5 before→after) → Confirm.
- **Data layer:** new `ui/src/api/hooks/useStacks.ts` + endpoint constants,
  copying `useProfiles.ts`. CSS scoped under a `.st-*` prefix mirroring `.pf-*`.

## 10. Seed / starter stacks

Ship three curated, clone-only seed stacks, chosen from the 2026-06-19 model
roster benchmark (exclusive-GPU sweep, 26 models). Bench bands: 🟢 ≥60 t/s ·
🟡 25–60 · 🔴 <25 decode. Embed/rerank/stt/tts slots use hal0's seeded
NPU/FLM + `moonshine`/`kokoro` providers (not in the LLM bench). Non-seed slot
names (`util`, `quick`) are created on apply via `slot_create` if absent.
Stored as seed TOML under the installer (`installer/etc-hal0/stacks/`, alongside
the seed `profiles.toml`); they double as seed content for the future directory.

### `saber` — high-speed agentic MoE

Max-throughput autonomous loadout. Decode-per-GB leader on the board.

| Slot | Model (registry id) | Notes |
| --- | --- | --- |
| **agent** (star) | `chadrock-35b-ace-saber` | 35B-A3B MoE · 19.0 GB · f16 KV · 🟢 92.9 t/s · 94.3% MTP · vision + tools · 262k ctx. Current live agent slot. |
| **chat** | `qwen3.6-35b-a3b-crown-halo-mtp-dynamic` | 35B-A3B · 22.6 GB · 🟢 83.8 t/s · vision · 91.3% MTP. Same MoE family. |
| **util** | `gemma-4-12B-agentic-fable5` | 12B dense · 7.4 GB · **tool-calling** router/util · run on **Vulkan** (🟡 26.2 t/s vs 🔴 22.4 ROCm). |
| **stt** | seeded `moonshine` (NPU) | voice in. |
| **tts** | seeded `kokoro` (CPU) | voice out. |
| **embed + rerank** | seeded NPU/FLM | memory recall + precision. |

*Personal-only speed alt (not shipped as public seed):*
`chadrock3-6-35b-uncensored-mtp` — fastest 35B at 🟢 102.1 t/s, but uncensored.

### `forge` — coding-first developer

Fast coder primary + agentic muscle + a draft coder + repo RAG.

| Slot | Model (registry id) | Notes |
| --- | --- | --- |
| **chat** (primary) | `qwen3-coder-reap-25b-a3b-q5km` | 25B-A3B MoE · 17.7 GB · 🟡 54.7 t/s · **1368 prefill** (file ingest) · coding. |
| **agent** | `chadrock-35b-ace-saber` | 19.0 GB · 🟢 92.9 t/s · tools + vision — drives edits/tool-calls. |
| **quick** | `qwopus3-5-4b-coder-mtp-q6-k` | 3.6 GB · 🟢 85.0 t/s · MTP · coder — inline/draft completions. |
| **embed + rerank** | seeded NPU/FLM | codebase retrieval. |

*Heavy alt:* `qwen3-coder-next-q4kxl` (49.6 GB · 🟡 37.8 t/s) for max coding
quality when the agent slot need not be resident.

### `pi` — always-on support (compaction, recall, voice)

The background brain: faithful summarization, memory recall, voice I/O. Quality
over speed — q8 weights for faithful compaction.

| Slot | Model (registry id) | Notes |
| --- | --- | --- |
| **util** (star) | `chadrock3.6-27b-pi-agent-rocmfp4-mtp` | 27B dense · **q8** · 14.8 GB · 🟡 33.3 t/s · 89.4% MTP · tools. q8 fidelity for compaction/recall. |
| **stt** | seeded `moonshine` (NPU) | voice in. |
| **tts** | seeded `kokoro` (CPU) | voice out. |
| **embed + rerank** | seeded NPU/FLM | memory recall + precision. |

*Documented swap-in #4 (`researcher`):* long-context reasoning primary
`qwen3.6-35b-a3b-q4kxl` (🟡 46.1 t/s · **1300 prefill**) + embed + rerank for
heavy RAG. Folded into `forge`/`pi` retrieval by default; promotable to a
first-class seed on request.

## 11. Directory-readiness (format only — not built)

The `.hal0stack.json` envelope is shaped so the future directory is just "a
server that lists and serves these files": stable `kind`, `schema_version`,
`checksum` (→ signing), and author/tags/description/icon (→ listing cards).
Nothing in v1 blocks it; the directory service itself is out of scope.

## 12. Error handling & edge cases

- **Slug collision** on create/import → reject with `StackConflict` (mirrors
  profile conflict handling); import offers clone-with-suffix.
- **Seed stack mutation** → blocked by catalog guard; Edit becomes "Edit a copy"
  (Profiles pattern, `profiles.jsx:78`).
- **Apply with a missing model** (registry changed since stack saved) → dry-run
  surfaces it; commit blocked until resolved (pull or edit).
- **Partial Phase-B failure** → committed config stands; per-slot ERROR surfaced;
  user can re-converge.
- **Malformed import envelope** → reject with field-path error (wraps validation
  like `loader.py:161`); never partially applies.
- **Drift during apply** (live config changed mid-flow) → apply operates on the
  ChangeSet computed at preview time; a stale preview is detected by hash and the
  user is asked to re-preview.

## 13. Testing strategy

- **Unit:** `StackConfig`/`StacksCatalog` validation, round-trip TOML I/O,
  seed-immutability guard, envelope checksum, migration walk.
- **Apply engine:** ChangeSet correctness vs `SlotConfigStore`; commit rollback
  on injected write failure; declarative unload of un-named slots; drift hashing
  (clean vs modified).
- **Import/export:** round-trip a stack across an empty registry; resolve-and-pull
  decision matrix (present / pullable / unresolvable); secret-exclusion assertion
  (no env values ever appear in an export); machine-path re-resolution.
- **API:** route contract tests mirroring the profiles route tests; gated-MCP
  approval path for `stack_apply`.
- **UI:** hook tests for `useStacks`; diff-preview modal renders a ChangeSet;
  import flow surfaces Pull buttons for missing models.

## 14. Build sequence (one feature, staged PRs)

1. **PR-1 — Schema + catalog:** `StackConfig`/`StacksCatalog`, TOML persistence,
   paths, migration hook, unit tests.
2. **PR-2 — Apply engine + drift:** dry-run ChangeSet + commit + lifecycle
   convergence + active-pointer/drift hashing, tests against `SlotConfigStore`.
3. **PR-3 — Export/import:** envelope, checksum, resolve-and-pull, snapshot-from-live.
4. **PR-4 — REST + MCP:** `routes/stacks.py` + admin tools.
5. **PR-5 — Dashboard UI:** `#slots/stacks` sub-page, `useStacks` hooks,
   diff-preview & import modals.
6. **PR-6 — Seed stacks + docs:** curated seed stacks + a Stacks doc page in
   `hal0-web` (the only change that touches that repo).

## 15. Key file references (anchors for implementation)

- Slot schema & validators — `src/hal0/config/schema.py:257` (SlotConfig), `:620` (validators), `:149` (model ref).
- Profile schema/catalog — `src/hal0/config/schema.py:814`, `src/hal0/profiles/__init__.py:129`.
- Capability config/orchestrator — `src/hal0/capabilities/config.py:52`, `src/hal0/capabilities/orchestrator.py:164`.
- Transactional apply — `src/hal0/slot_config/__init__.py:131` (`apply`/`commit`/`revert`).
- Atomic TOML/JSON writes — `src/hal0/config/loader.py:69`, `src/hal0/slots/state.py:269`.
- Migrations — `src/hal0/config/migrations/__init__.py:75`.
- Model registry — `src/hal0/registry/model.py:54`, `src/hal0/registry/store.py`, pull `src/hal0/registry/pull.py`.
- REST precedent — `src/hal0/api/routes/profiles.py`; MCP — `src/hal0/mcp/admin.py`.
- Dashboard: nav `ui/src/dash/chrome.jsx:195`; router `ui/src/dash/main.jsx:25`; Profiles page `ui/src/dash/profiles.jsx`; primitives `ui/src/dash/primitives.jsx`, `ui/src/dash/cards-shell.jsx`; client `ui/src/api/client.ts`, `ui/src/api/endpoints.ts`, `ui/src/api/hooks/useProfiles.ts`.
- Prior art (Bundles) — `src/hal0/bundles/schema.py`.
