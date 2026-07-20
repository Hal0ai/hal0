I now have a complete and verified map. Here is the implementation-ready spec.

---

# P2-config: Collapse capabilities.toml double-bookkeeping

## Verification note
All line references verified against `/home/mint/hal0` on branch `rework/descar`. The docs describing "issue #697 SlotConfigStore as one reconciled truth" are **stale in intent** — the code still persists `capabilities.toml` as an independent authoritative file AND reconciles it against `slots/*.toml` on every write. That is the double-bookkeeping to remove.

---

## PART 0 — EXACT MAP (file:line)

### `capabilities/` package

**`capabilities/config.py`** (411 lines) — the `capabilities.toml` model + IO:
- `CapabilitySelection` (BaseModel) `config.py:51-123`. Stored fields per `(slot,child)`:
  - `device: str` `:67` (`gpu-rocm|gpu-vulkan|npu|cpu`, "" = unset)
  - `backend: str` `:75` — DEPRECATED alias of device (removed v0.3)
  - `provider: str` `:85` (`llama-server|flm|moonshine|kokoro|comfyui`)
  - `model: str` `:89` (registry id)
  - `enabled: bool` `:93`
  - `_promote_backend_to_device` validator `:98-123`
- `CapabilityConfig` `:126-154` — `schema_version:int` `:141`, `selections: dict[slot][child] -> CapabilitySelection` `:151`
- `capabilities_toml_path()` `:160`, `capabilities_v1_backup_path()` `:165`
- Migration: `read_schema_version` `:180`, `migrate_capabilities_v1_to_v2` `:194`, `auto_migrate_capabilities_file` `:257` (rename→`.v1.bak`, rewrite v2, under `file_lock`)
- IO: `load_capabilities_config` `:330`, `capabilities_toml_payload` `:349` (canonical dump, strips `backend`), `save_capabilities_config` `:381`

**`capabilities/orchestrator.py`** (889 lines):
- `_CHILD_TO_SLOT` `:68-75` — the (slot,child)→slot_name bridge: `(embed,embed)→embed`, `(embed,rerank)→embed-rerank`, `(voice,stt)→stt`, `(voice,tts)→tts`, `(img,img)→img`, `(vision,vision)→vision`
- `_SLOT_TO_CHILD` `:78-80`, `_CHILD_TO_SLOT_TYPE` `:88-91` (`embed→embedding`, `stt→transcription`), `_CHILD_TO_CAPABILITY` `:127-134`, `_CAPABILITY_TO_SLOT_TYPE` `:136-144`, `LEGAL_SLOTS` `:94`
- `CapabilityOrchestrator.__init__` `:173-186` — constructs `self._store = SlotConfigStore(capabilities_path=...)` `:185`
- `_load` `:190`/`_save` `:193` (wrap load/save_capabilities_config)
- `initialize_if_missing` `:196-251` — the **seed**: under `file_lock`, if file exists → `auto_migrate_capabilities_file`; else walk `_CHILD_TO_SLOT`, `load_slot_config(slot_name)`, lift `device/provider/model.default/enabled` into each child, `self._save(cfg)`
- `get_state` `:270-317` — `_load()` → merges persisted selections with live SlotManager status → dashboard payload
- `_selection_with_defaults` `:261-266`
- `apply` `:358-537` — merge partial → validate → `self._store.apply_and_commit(SlotSelection(...))` `:442` → lifecycle dispatch (load/swap/unload or NPU-trio)
- `_ensure_slot_exists` `:664-713` — auto-creates `slots/<name>.toml` (disabled-slot fallback) via `SlotManager.create`
- NPU trio: `_apply_npu_trio_modality` `:717`, `_set_flm_modality` `:764`, `_ensure_slot_exists_npu` `:814`
- `_validate_model_in_catalog` `:541`, `_profile_for_fit` `:640`, `_next_free_slot_port` `:847`

**`capabilities/profile_fit.py`** (52 lines) — `profile_name_for_fit(capability, device)` `:21`; pure (capability,device)→profile-name. **No capabilities.toml dependency.** Keep as-is.

**`capabilities/catalog.py`** — `available_backends()` `:236` (hardware probe), `models_for_capability()` `:806` (registry), `catalogs_by_slot()` `:936` (registry), `tts_profile_for_device()` `:125`. **None read capabilities.toml.** Keep as-is.

**`capabilities/__init__.py`** `:15` re-exports `CapabilityConfig`, `CapabilitySelection`, `CapabilityOrchestrator`.

### `slot_config/__init__.py` `SlotConfigStore` (640 lines)
- Low-level write path: `write_slot_toml` `:70`, `slot_write_lock` `:85` (coarse `<slots_dir>.lock`), `fold_ctx_size_alias` `:113`, `merge_slot_config` `:143` (the shared copy-safe projection primitive), `unknown_slot_config_keys` `:240`
- `FileState` `:312`, `ChangeSet` `:324` (+`changed` `:336`), `SlotSelection` `:342-354`
- `SlotConfigStore` `:360`:
  - `_caps_path` `:381`, `_slot_path` `:386`
  - `apply` (compute-only) `:392-428` → ChangeSet over `[caps_path, slot_path]`
  - `transaction` `:432-453` (holds caps lock + slot_write_lock), `apply_and_commit` `:455-469`
  - `commit` `:473-493` (per-file atomic + rollback), `revert` `:495-498`
  - `_reconciled_capabilities` `:502-517` — **writes the caps file** from selection via `capabilities_toml_payload`
  - `_reconciled_slot` `:519-589` — projects selection onto slot TOML (`enabled` unconditional, device/provider/model/profile when enabled), TTS profile `:591-606`
- `_read_toml_or_none` `:612`, `_write_state` `:621`

### `stacks/apply.py` `StackApplyEngine` (456 lines) — the 3rd apply engine
- `_read_toml_or_none` `:43` (local mirror), `StackChangePlan` `:56`, `ConvergeReport` `:106`
- `_CHILD_TO_GROUP` `:88`/`_CHILD_TO_SLOT_NAME` `:96` — **hardcoded duplicate** of orchestrator's `_CHILD_TO_SLOT` ("KEEP IN SYNC" comment `:87`)
- `StackApplyEngine.__init__` `:121` (holds a `SlotConfigStore` `:130`)
- `plan` `:144-183`, `validate` `:185`, `_reconciled_stack_slot` `:209-261` (routes through `reconcile_and_guard_slot_config`)
- `apply_config` `:273-287` (commits via `self._store.commit` under `transaction`)
- drift: `_projection_from_plan` `:291`, `_projection_live` `:295`, `record_active` `:304`, `drift_status` `:326`
- converge (Phase B lifecycle): `converge` `:355`, `_converge_primary` `:387`, `_converge_capabilities` `:406` (calls `orchestrator.apply`), `_converge_unload` `:440`

### Every reader/writer of `capabilities.toml`

**WRITERS:**
| Site | file:line | Path |
|---|---|---|
| Seed | `orchestrator.py:251` `_save` → `save_capabilities_config` | first-boot only |
| Apply | `slot_config/__init__.py:517` `_reconciled_capabilities` → committed at `:487` | every capability apply |
| Migrate CLI | `cli/capabilities_commands.py:329` `save_capabilities_config` | repair |
| Schema auto-migrate | `config.py:317` `write_toml_atomic` (inside `auto_migrate_capabilities_file`) | v1→v2 |

**READERS (as truth):**
| Site | file:line | Purpose |
|---|---|---|
| `get_state` | `orchestrator.py:281` `_load()` | dashboard payload |
| `apply` (before-snapshot) | `orchestrator.py:385` `_load()` + `:412` store re-read | merge base |
| snapshot/export | `stacks/portable.py:348` `load_capabilities_config()` | stack snapshot |
| migrate CLI | `cli/capabilities_commands.py:242` `load_capabilities_config()` | repair walk |
| store reconcile | `slot_config/__init__.py:512` `CapabilityConfig.model_validate(raw_before)` | reconcile base |

**Docs/permission refs (non-parsing):** `install/perms.py:150` (0o600 PermRow), `config/paths.py:282` (docstring), `agents/hermes_templates/{AGENTS,SOUL}.md.j2` (Hermes prose), `cli/registry_commands.py:20/40/199`, `cli/model_commands.py:492`, `profiles/__init__.py:287/291`, `slots/manager.py:1810/2016` (error strings).

### Field overlap (capabilities.toml vs slots/*.toml) — EXACT
| CapabilitySelection field | slot TOML field | Overlap |
|---|---|---|
| `device` (`config.py:67`) | `device` (`schema.py:318`) | identical enum |
| `provider` (`:85`) | `provider` (`schema.py:340`) | identical |
| `model` (`:89`) | `model.default` (`ModelConfig`) | 1:1 (`model` string ↔ `model.default`) |
| `enabled` (`:93`) | `enabled` (`schema.py:348`) | identical |
| `backend` (`:75`, deprecated) | `backend` (`schema.py:309`, deprecated) | identical alias |

**Every field `CapabilitySelection` persists is already present in the backing slot TOML.** `capabilities.toml` carries **zero unique authoritative data** — except the one edge case in Part D.

### `hal0 capabilities migrate` CLI
`cli/capabilities_commands.py`: `migrate` command `:205-333`, registered in `cli/main.py:69` (`add_typer(capabilities_app, name="capabilities")`). Walks selections `:246`, `_classify_pair` `:169`, snaps illegal (model,backend) pairs, `save_capabilities_config(cfg)` `:329`. `list` `:64` and `set` `:115` are thin API clients (keep).

---

## PART A — Which apply engine to KEEP: **`SlotConfigStore`**

**Recommendation: keep `SlotConfigStore`; delete the capabilities-orchestrator in-apply reconcile and `stacks/apply.py`'s parallel reconcile.**

Rationale:
1. **It already owns the slot-TOML write invariant.** `write_slot_toml` `:70` and `slot_write_lock` `:85` are THE byte-level write path used by everyone (`slots/manager.py:1985/2103/2419/2509/2552`, `installer.py:537`, `models.py:1377`). The store is the seam every writer already routes through. The other two "engines" are thin layers *on top* of it (`stacks/apply.py:130` and `orchestrator.py:185` both hold a `SlotConfigStore`).
2. **It is the only one with a tested atomic/rollback commit.** `commit` `:473` + `revert` `:495` + `merge_slot_config` `:143` are unit-pinned (`tests/slot_config/test_store.py`, `test_store_locking.py`, `test_merge_slot_config.py`). The orchestrator's old "unconditional in-place rewrite" and stacks' plan/commit are re-derivations.
3. **The other two duplicate the (slot,child)→slot_name map.** `stacks/apply.py:88-103` hardcodes a "KEEP IN SYNC" copy of `orchestrator._CHILD_TO_SLOT`. Collapsing onto one engine removes the drift surface entirely.
4. **`stacks/apply.py` is not a *different* engine** — its `apply_config` `:286` literally calls `self._store.commit`. It is a *caller* of the store plus (a) a stack-row→slot projection `_reconciled_stack_slot` `:209` and (b) lifecycle convergence `converge` `:355`. Only those two pieces are unique; both can be reduced to store calls + `SlotManager` lifecycle.

**What "keep SlotConfigStore" means concretely after this change:** the store loses its `capabilities.toml` responsibility entirely (delete `_reconciled_capabilities` `:502` and drop `caps_path` from the ChangeSet). It becomes purely "reconcile one slot TOML + commit atomically". That is the single apply engine; both the capability apply path and the stack apply path call `store.apply_and_commit`/`store.commit` with slot-only ChangeSets.

---

## PART B — Make capabilities a DERIVED projection (not a persisted file)

### The derived read
`capabilities.toml` stops being written. `CapabilityConfig` is computed on demand from the slot TOMLs. The projection is **deterministic and already exists** — it is exactly the body of `initialize_if_missing` `:231-249`. Promote it to a pure function:

```
def derive_capabilities_config(load_slot=load_slot_config) -> CapabilityConfig:
    cfg = CapabilityConfig()
    for (slot, child), slot_name in _CHILD_TO_SLOT.items():
        cfg.selections.setdefault(slot, {})
        sel = CapabilitySelection()
        try:
            sc = load_slot(slot_name)          # slots/<slot_name>.toml
        except Exception:
            cfg.selections[slot][child] = sel  # blank picker
            continue
        sel.device   = canonical_device(sc.device)
        sel.provider = sc.provider
        sel.model    = sc.model.default or ""
        sel.enabled  = bool(sc.enabled) and bool(sel.model)
        cfg.selections[slot][child] = sel
    return cfg
```

Live location: `capabilities/config.py` (or a new `capabilities/derive.py`). `slot_config` must not import `capabilities` (cycle — see `slot_config/__init__.py:56-62`), so the projection lives in the capabilities package.

Then:
- `orchestrator._load` `:190` → `return derive_capabilities_config()` (no file read).
- `orchestrator.get_state` `:281` — unchanged except `_load()` now derives. Live status enrichment `:291-311` still comes from SlotManager, as today.
- `stacks/portable.snapshot_live_stack:348` — replace `load_capabilities_config()` with `derive_capabilities_config()`. (Note: it already ALSO reads the slot TOMLs directly via `_read_slot_raw`, so this makes the two consistent.)

### The write path (apply)
`orchestrator.apply` `:358` keeps computing `merged: CapabilitySelection` `:409` exactly as today, but:
- Replace `self._store.apply_and_commit(SlotSelection(...))` `:442` with a **slot-only** commit. The store's `apply` `:392` no longer produces a caps FileState; the ChangeSet is one file (`slots/<slot_name>.toml`).
- The before-snapshot `before_enabled/before_model/before_device` `:387-389` currently read from the derived selection — keep, but source them from `derive` (i.e. from the slot TOML) so they reflect on-disk truth.
- **Critical change (Part D):** because the disabled-pre-pick has nowhere to live once caps is gone, `apply` must **ensure the slot TOML exists on any selection** (see Part D), so the merged selection always lands in a slot TOML.

### What to DELETE
1. **`SlotConfigStore._reconciled_capabilities`** `slot_config/__init__.py:502-517` and the caps half of `apply` `:411-413,:420-421,:424-425`. Store `apply` becomes a single-file (slot) ChangeSet. `_caps_path` `:381` and `capabilities_path` ctor arg `:371,376` deleted.
2. **`initialize_if_missing` seeding** `orchestrator.py:196-251` — deleted entirely (the projection replaces it). Its `auto_migrate` call `:220` is subsumed by the one-shot migration in Part C. The boot call `api/__init__.py:1104-1116` is deleted or replaced with a no-op/one-shot migration hook.
3. **`hal0 capabilities migrate`** command `cli/capabilities_commands.py:205-333`, `_classify_pair` `:169-202`, and the now-unused imports (`CapabilitySelection`, `capabilities_toml_path`, `load_capabilities_config`, `save_capabilities_config`, `file_lock`, `ModelRegistry`, `models_for_capability`). The illegal-(model,backend) repair it did is now unreachable-by-construction: selections are derived from slot TOMLs, which are validated on write by `unknown_slot_config_keys`/`reconcile_and_guard_slot_config`. If operators still want a repair for illegal *slot* TOML pairs, that belongs under `hal0 slots` — out of scope here.
4. **`save_capabilities_config`** `config.py:381`, **`capabilities_toml_payload`** `:349`, **`load_capabilities_config`** `:330` (replaced by `derive_*`), **`auto_migrate_capabilities_file`** `:257`, **`migrate_capabilities_v1_to_v2`** `:194`, **`read_schema_version`** `:180`, **`capabilities_v1_backup_path`** `:165`, **`capabilities_toml_path`** `:160` — after the one-shot migration in Part C ships and removal window closes. During the deprecation window keep `migrate_capabilities_v1_to_v2` reachable from the one-shot migrator only.
5. **`CapabilityConfig.schema_version`** field `config.py:141` — no on-disk file to version. `CapabilitySelection.backend` `:75` + `_promote_backend_to_device` `:98` — keep only while the API still emits the `backend` alias to the v0.1.x dashboard (`get_state:305`, `apply` return `:527`); delete when the UI rework lands.
6. **`orchestrator._save`** `:193` — deleted (nothing writes caps).
7. **`stacks/apply.py` as a distinct engine** — see Part E for the exact reduction (its `_CHILD_TO_GROUP`/`_CHILD_TO_SLOT_NAME` `:88-103` delete; import from orchestrator or keep converge routing through `orchestrator.apply`).

### `SlotConfigStore.transaction` after the change
`transaction` `:432` currently locks caps + slots. After caps is gone it locks only `slot_write_lock` `:452`. The caps `file_lock` and all `initialize_if_missing`/migrate re-entrancy notes (`config/locking.py:25-26`) become dead — simplify.

---

## PART C — Migration for existing `capabilities.toml` files

There is nothing to migrate *into* — the file is being retired. But existing installs have a `capabilities.toml` that may hold selections **not reflected in slot TOMLs** (the pre-pick case, or historical drift where caps and slot disagreed). Migration = **fold caps into slot TOMLs, then delete caps.**

One-shot migrator `migrate_capabilities_into_slots()` (new, runs once at boot, guarded by presence of the file):
1. Under `slot_write_lock()`, `load_capabilities_config(path)` (keep this reader alive for the window). If `schema_version==1`, run `migrate_capabilities_v1_to_v2` in-memory first.
2. For each `(slot,child,selection)`: if `selection.model` or `selection.device` is set and disagrees with the derived value from `slots/<slot_name>.toml`, treat **capabilities.toml as the winner** (it was the operator-facing surface) and write the selection into the slot TOML via `store.apply_and_commit(SlotSelection(...))` — creating the slot if needed (Part D create path). This is the ONLY place caps→slot direction is honored.
3. Rename `capabilities.toml` → `capabilities.toml.migrated.bak` (reuse `capabilities_v1_backup_path` naming). Log one line.
4. Idempotent: on next boot the file is absent → no-op.

Hook: replace `api/__init__.py:1104-1116` `initialize_if_missing()` call with `migrate_capabilities_into_slots()` (best-effort, must not block startup — same try/except as today `:1109-1116`). Ship this in the release BEFORE the one that deletes the caps readers, so an install that skipped the migration boot still gets folded on upgrade.

Remove `install/perms.py:150` PermRow for `capabilities.toml` in the release that deletes the file (until then the `.bak` still wants 0o600).

---

## PART D — ⚠ Downtime window / ordering

The dangerous transition is: **caps stops being written but is still the only home of the disabled-pre-pick selection.** Sequence to avoid data loss:

**⚠ Step 1 (release N, no downtime):** Land the one-shot `migrate_capabilities_into_slots` at boot (Part C) AND make `apply` **ensure the slot TOML exists on every selection** (Part D create-on-select below). Both readers (`get_state`, snapshot) still read caps. Result: from now on every selection is materialized in a slot TOML; caps is redundant but still written.

**⚠ Step 2 (release N, same deploy, after boot migration has run once):** Switch `_load`/`snapshot` to `derive_capabilities_config`. Stop writing caps (`_reconciled_capabilities` deleted, `_save` deleted). This is the **cutover instant** — must happen only after Step 1's migration has folded caps into slots. Because Step 1 runs at boot before the first request, a single `hal0-api` restart is the entire window.

**⚠ Step 3 (release N+1):** Delete `capabilities.toml.*.bak`, the migrate CLI, the dead config.py readers/migrators, `CapabilityConfig.schema_version`, the perms row.

**The create-on-select fix (load-bearing for Step 2):** Today `store.apply` returns `after==before` when the slot TOML is absent (`_reconciled_slot:555-556`), and `_ensure_slot_exists` `:664` only runs inside the *enabled* lifecycle branch (`apply:482,492`). So a **disabled selection with a picked model** for a not-yet-created slot (canonical: `embed-rerank`) lives ONLY in caps. Once caps is derived-only that pick is lost on reload.

Fix: in `apply`, call `_ensure_slot_exists(slot_name, merged)` **before** `store.apply_and_commit` whenever `merged.model` or `merged.device` is set (not just when enabling). The created slot is written with `enabled=merged.enabled`. Then the store reconcile writes the model/device onto it. This makes the slot TOML the sole, complete home of the selection. Side effects to accept: a port is allocated (`_next_free_slot_port:847`) and a `state.json` created on first *pick* rather than first *enable* — cheap and idempotent. For NPU trio children the existing `_ensure_slot_exists_npu` `:814` covers this.

Downtime = one `hal0-api` restart per host at Step 2. No data migration downtime beyond boot.

---

## PART E — Per-file edits + risks

| File | Edit |
|---|---|
| `capabilities/config.py` | Add `derive_capabilities_config()`. Keep `load_capabilities_config` + `migrate_capabilities_v1_to_v2` for the migration window only. Delete `save_capabilities_config`, `capabilities_toml_payload`, `auto_migrate_capabilities_file`, `capabilities_toml_path` (N+1). Remove `CapabilityConfig.schema_version` (N+1). |
| `capabilities/orchestrator.py` | `_load:190`→derive; delete `_save:193`; delete `initialize_if_missing:196-251`; in `apply` swap `apply_and_commit:442` to slot-only ChangeSet + add unconditional `_ensure_slot_exists` on any set model/device (Part D); source `before_*:387-389` from derive. Drop the `SlotConfigStore(capabilities_path=...)` arg `:185`. |
| `slot_config/__init__.py` | Delete `_reconciled_capabilities:502-517`; make `apply:392` a single-file (slot) ChangeSet; drop `capabilities_path` ctor arg `:371,376` + `_caps_path:381`; `transaction:432` locks only `slot_write_lock`. |
| `stacks/apply.py` | Reduce to store-caller: keep `_reconciled_stack_slot`/`plan`/`apply_config`/`converge`; delete `_CHILD_TO_GROUP`/`_CHILD_TO_SLOT_NAME:88-103` and import the single map from orchestrator (cycle allows it here — stacks may import capabilities). Its converge already calls `orchestrator.apply`, which is now the one write path — no caps writes. |
| `cli/capabilities_commands.py` | Delete `migrate:205-333` + `_classify_pair:169-202` + now-dead imports. Keep `list`/`set` (API clients). |
| `cli/main.py` | `capabilities` typer unchanged (still has list/set). |
| `api/__init__.py` | Replace `initialize_if_missing():1104-1116` with `migrate_capabilities_into_slots()` (window), then remove at N+1. |
| `stacks/portable.py:348` | `load_capabilities_config()` → `derive_capabilities_config()`. |
| `install/perms.py:150` | Remove `capabilities.toml` PermRow (N+1). |
| `config/paths.py:282`, hermes templates, `registry_commands.py`, `model_commands.py`, `profiles/__init__.py`, `slots/manager.py` error strings | Prose/doc updates: "disable via capabilities.toml" → "disable the slot's `enabled` in slots/<name>.toml (or the dashboard capability card)". |
| Tests | `tests/capabilities/test_orchestrator_reconciliation.py`, `tests/slot_config/test_store*.py`, `tests/cli/test_capabilities_commands.py`, `tests/config/test_schema_migration.py`, `tests/stacks/test_converge_capabilities.py` — rewrite the caps-file assertions to assert on slot TOMLs + the derived projection. Add a test for the one-shot folder and the create-on-select behavior. |

### Risks — anything that treats capabilities.toml as truth
1. **Disabled-pre-pick loss** (highest) — mitigated by create-on-select (Part D). Without it, cutover silently drops picks.
2. **Historical drift where caps ≠ slot TOML** — the one-shot migrator (Part C) resolves caps-wins, once. After cutover the derived read will surface whatever the slot TOML says; if an operator hand-edited caps but not the slot, that edit is lost. Acceptable — caps is being retired and SOUL.md `:41` already says "never edit capabilities.toml directly."
3. **`stacks/portable.snapshot_live_stack:348`** currently reads caps for capability rows; a stale caps could have produced snapshots that disagree with slot TOMLs. Switching to derive makes snapshots consistent — a behavior change to call out in release notes.
4. **v0.1.x dashboard `backend` alias** — `get_state:305` and `apply` return `:527` still emit `backend`. Keep `CapabilitySelection.backend` + `_promote_backend_to_device` until the UI rework; deleting early 500s the old frontend.
5. **NPU trio enabled-state** — for `device=npu` embed/stt the effective on/off also lives in the anchor's `[npu]` toggle (`_set_flm_modality:764`), while the derived `enabled` reads the modality slot's `enabled` field (written by `_apply_npu_trio_modality:755`). These are kept in sync by `apply` today; verify the derived read matches the `[npu]` toggle in `test_npu_phase2_integration.py`. If they can diverge, derive `enabled` for npu children from the anchor toggle, not the shadow slot.
6. **Concurrency** — after removing the caps lock, the only lock is `slot_write_lock` `:85`. Confirm no remaining reader assumed the caps `file_lock` for ordering (`config/locking.py:25-26` notes become dead; migrate CLI's `file_lock(capabilities_toml_path())` `:241` is deleted with the command).

### Net deletion
~470 lines removed (migrate CLI ~165, `initialize_if_missing` ~55, caps IO/migration in config.py ~180, `_reconciled_capabilities`+caps ChangeSet half ~40, stacks duplicate map ~20), replaced by a ~20-line `derive_capabilities_config` + a ~30-line one-shot migrator. Double-bookkeeping and two of three apply engines gone; `SlotConfigStore` is the single slot-config writer, `slots/*.toml` the single source of truth.