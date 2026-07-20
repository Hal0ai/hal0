I have everything verified against code. Here is the implementation-ready spec.

---

# P2-device: make `device` the sole truth, delete the `backend` dual-write

Repo `/home/mint/hal0` @ `rework/descar`. All line numbers verified against current HEAD.

## 0. The critical distinction (read first)

There are **two unrelated `backend` concepts** in this codebase. Conflating them will break the runtime. The spec's premise ("delete the backend dual-write, including API responses") is only partly right — the API-response `backend` is **not** a mirror.

**Concept A — the deprecated hardware-preference MIRROR field** (this is what P2 deletes). A duplicate of `device`, written alongside it on disk for v0.1.x downgrade legibility. Tokens: `vulkan | rocm | cpu | flm | moonshine | kokoro`. Lives as `SlotConfig.backend` and `CapabilitySelection.backend`.

**Concept B — the effective RUNTIME backend token** (KEEP, load-bearing, derived from `device`). Tokens: `rocm | vulkan | cpu | flm`. This is what `device_to_backend()` returns as its 2nd element, what `_cfg_effective_backend()` computes, what the `Slot.backend` API field and `declared_backend` surface to the dashboard. It is **already** device-derived — it is the *fix*, not the *problem*. Deleting it would break the SlotCard backend chip and NPU trio dispatch.

Rule of thumb: if a `backend` value is `gpu-rocm`-style-mapped or persisted to a slot/capabilities TOML → Concept A (delete). If it's `rocm|vulkan|cpu|flm` derived at read-time for display/argv → Concept B (keep).

The API response (`api/routes/slots.py:1528,1556` → `snap.backend`; `Slot` dataclass field `manager.py:253`) is **Concept B**. The `Slot` dataclass has **no `device` field**, so the API response is *not* a device+backend dual-write. Leave it alone.

## 1. The 4 translators — disposition

All defined in `src/hal0/model_meta/__init__.py`. The task says "keep one" — that's not accurate against the code: three are load-bearing, only one is deletable.

| Translator | Def | Verdict | Why |
|---|---|---|---|
| `canonical_device(value)` | `:541` | **KEEP** (the device-normalizer to standardize on) | Normalizes any value → device enum. Used at every `device` write. |
| `device_to_backend(device)` | `:500` | **KEEP** (Concept B runtime) | device → `(recipe, llamacpp_backend)` for argv + display. Not a mirror. |
| `map_backend_to_device(backend)` | `:216` | **KEEP** (read-side legacy tolerance) | Engine of `canonical_device`; also used by capabilities v1→v2 migration, `hardware/recommend`, profile-coherence. Schema re-exports it (`schema.py:3082`; identity asserted `tests/model_meta/test_model_meta.py:329`). |
| `device_to_legacy_backend(device)` | `:571` + `_DEVICE_TO_LEGACY_BACKEND` `:568` | **DELETE** | Sole purpose is computing the Concept-A mirror token on write. Becomes dead once the 3 dual-writes go. |

**Actionable call sites of `device_to_legacy_backend`** (only 3, all Concept-A writes): `orchestrator.py:680`, `slot_config/__init__.py:567`, `stacks/apply.py:239` (+ imports `orchestrator.py:55`, `slot_config:51`, `stacks/apply:24`; `__all__` `model_meta:679`; docstrings `model_meta:77,561-567`, `slot_config:29`, `orchestrator:259`; tests `test_model_meta.py:12,30,263-283,416,421`).

`canonical_device` / `device_to_backend` / `map_backend_to_device` call sites stay as-is (they are `device`-side or Concept-B). Full inventory confirmed across the 9 files; the only import to drop per-file is `device_to_legacy_backend`.

## 2. Concept-A surfaces to remove (the dual-write)

### 2a. Model fields + validators

- **`SlotConfig.backend` field** — `src/hal0/config/schema.py:309-317`. Delete.
- **`SlotConfig.backend_valid` field_validator** — `schema.py:732-737`. Delete (and its `_VALID_BACKENDS`/`_LEGACY_BACKENDS` import at `:40,64` becomes unused → check for other users before removing; `_LEGACY_BACKENDS` is also the `/api/meta/enums` `LEGACY_BACKENDS` source, so keep the import if referenced elsewhere).
- **`SlotConfig._promote_backend_to_device`** — `schema.py:628-667`. ⚠️ **RISK — do not blind-delete** (see §3.1).
- **`CapabilitySelection.backend` field** — `src/hal0/capabilities/config.py:75-84`. Delete.
- **`CapabilitySelection._promote_backend_to_device`** — `config.py:98-123`. Read shim; see §3.1.

### 2b. Dual-write sites (write both `device` and `backend`)

1. `src/hal0/slot_config/__init__.py:567-573` (`reconcile_selection_into_slot`): drop `slot_backend = device_to_legacy_backend(...)` and the `updates["backend"] = slot_backend` block (567-571); keep `slot_device`/`updates["device"]`.
2. `src/hal0/capabilities/orchestrator.py:680-687` (`_ensure_slot_toml`): drop line 680 and `"backend": slot_backend or "vulkan"` (686); keep `"device": slot_device or "gpu-rocm"` (687). Update comment 677-679.
3. `src/hal0/stacks/apply.py:238-241`: drop the `legacy = device_to_legacy_backend(device)` / `updates["backend"] = legacy` block; keep `updates["device"] = device`.
4. `src/hal0/cli/slot_commands.py:459-466` (create body): drop `"backend": hw` (463); keep `"device": device` (462).
5. `src/hal0/cli/slot_commands.py:552` (update payload): `payload["backend"] = hardware.value` writes **only backend, no device** — this is a legacy path relying on server promotion. Replace with `payload["device"] = {vulkan→gpu-vulkan, rocm→gpu-rocm, cpu→cpu,...}[hardware.value]` (reuse the map at `slot_commands.py:454-458`).

### 2c. Strip-on-save / migration (already device-only; simplify)

- `config.py:367-378` (`capabilities_toml_payload`) — pops `backend` from each selection. Once the field is gone this pop is redundant but harmless (dict may carry stray `backend` under `extra`); safe to keep or remove.
- `config.py:236-249` (`migrate_capabilities_v1_to_v2`) — the v1→v2 backend→device rename. **KEEP** (this is the on-disk read migration for the capabilities file; still needed for legacy files). It calls `map_backend_to_device` — fine.

### 2d. POST/CLI input alias (decision needed, independent of the field)

- `orchestrator.py:396-405` accepts `backend=` in a POST partial and forward-translates via `canonical_device`. This operates on the **raw partial dict**, not the model field, so it survives field removal. **Decision:** keep it as API back-compat (recommended, cheap) or drop it. If kept, note it in a deprecation comment.
- `cli/capabilities_commands.py:147` `body["backend"] = backend` — CLI posting the alias. Switch to `body["device"]` (or keep if 2d alias kept).
- `cli/capabilities_commands.py:104,273,291` read `sel.backend` for display → switch to `sel.device`.
- `cli/capabilities_commands.py:279-284,297-302` construct `CapabilitySelection(backend=...)` → switch to `device=`.

## 3. Load-bearing / risky sites — flag before editing

**3.1 `SlotConfig._promote_backend_to_device` (`schema.py:628-667`) and `CapabilitySelection._promote_backend_to_device` (`config.py:98-123`) are the ONLY backend→device promotion for on-disk data that predates `device`.** There is **no on-disk slot-TOML migration** (`src/hal0/config/migrations/v1.py` does not touch backend/device — verified). If you delete the SlotConfig validator outright, a legacy slot TOML carrying only `backend="cpu"` (no `device`) loads as `device="gpu-rocm"` (`DEFAULT_DEVICE`) — a silent hardware regression. **Recommendation:** don't delete; **convert** to a read-only promotion shim — when `device` missing and `backend` present, set `device = map_backend_to_device(backend)` and `pop("backend")` (drop the "we deliberately do NOT delete backend" dual-keep at 641-643). This keeps device behavior identical while making `device` the sole persisted truth. Same for the capabilities validator. (With `extra="allow"`, a leftover `backend` key otherwise round-trips through `extra` — so the pop matters.)

**3.2 `_cfg_effective_backend` (`manager.py:3855-3887`)** — Concept B. Its internal legacy fallback (`d.get("backend")` at 3871-3880) is read-tolerance for old raw dicts; keep it. `manager.py:3589` `_cfg_effective_backend(cfg) or cfg.get("backend")` — the trailing `or cfg.get("backend")` is now redundant (the fn already folds legacy internally); optional to drop, harmless to keep. Sites `manager.py:1389,1394-1398,1438-1449,2005,2138-2142` all write the **derived** Concept-B token to `state.json extra["backend"]` / `Slot.backend` — **keep all**.

**3.3 `slot_view/__init__.py:351-354`** (`declared_backend` via `device_to_backend`), **`manager.py:2474-2478`** (`resolved.backend` vs `device_to_backend(device)[1]`), **`_base_profile_for_backend`/`_reconcile_device_profile` (`manager.py:3890-3969`)**, **POST `/api/slots/{name}/backend`** (`SELECTABLE_BACKENDS`), **`profile.backend`**, **`MODEL_BACKENDS`/`LEGACY_BACKENDS`** — all Concept B or unrelated. **Do not touch.**

## 4. Edit order

1. **Translator** — delete `device_to_legacy_backend` + `_DEVICE_TO_LEGACY_BACKEND` (`model_meta/__init__.py:568,571-587`, `__all__:679`, docstrings). This makes the compiler/tests point at every remaining caller.
2. **Dual-write sites** — §2b (1-5): remove the `backend` writes, drop the now-unused `device_to_legacy_backend` imports (`orchestrator:55`, `slot_config:51`, `stacks/apply:24`).
3. **Model fields + field_validator** — §2a: delete `SlotConfig.backend`, `backend_valid`, `CapabilitySelection.backend`.
4. **Promotion validators** — §3.1: convert (don't delete) both `_promote_backend_to_device` to read-only promote-then-drop shims.
5. **CLI** — §2b(4,5), §2d: `slot_commands.py` create/update, `capabilities_commands.py` reads/constructs.
6. **Input alias** — §2d decision in `orchestrator.py:401-405`.
7. **Tests** — §6.
8. Run `graphify update .` per CLAUDE.md after edits.

## 5. Grep to confirm zero remaining live Concept-A refs

```bash
cd /home/mint/hal0
# 1. translator fully gone (expect: no matches in src/)
grep -rn "device_to_legacy_backend\|_DEVICE_TO_LEGACY_BACKEND" --include='*.py' src/
# 2. no model still declares a backend field / validator (expect: only Concept-B/profile/meta)
grep -rn "backend.*=.*Field\|field_validator(\"backend\"\|\.backend =" --include='*.py' src/hal0/config/schema.py src/hal0/capabilities/config.py
# 3. no write of a backend KEY into a slot/caps config dict (audit each hit: must be Concept-B extra[] only)
grep -rn '"backend"\|updates\["backend"\]\|payload\["backend"\]\|body\["backend"\]\|cfg_dict\["backend"\]' --include='*.py' src/hal0/slot_config src/hal0/capabilities src/hal0/stacks src/hal0/cli
# 4. CapabilitySelection/SlotConfig no longer constructed/read with backend=
grep -rn "backend=" --include='*.py' src/hal0 | grep -iv "llamacpp\|to_backend\|_backend_for\|declared\|snap.backend\|eff"
# 5. sanity: these MUST still exist (Concept B) — non-empty is correct
grep -rn "_cfg_effective_backend\|device_to_backend\|declared_backend" --include='*.py' src/hal0 | head
```
Every hit from #3/#4 must be either a Concept-B `extra["backend"]`/`Slot.backend` write or gone.

## 6. Test files (assert on the deprecated dual-write — will break, must update)

**Must edit (assert Concept-A):**
- `tests/config/test_schema.py:104,110-115,159-161` — `SlotConfig.backend` field + `backend="vukan"` raises. Delete/rewrite to `device`.
- `tests/config/test_schema_migration.py:61-84` (SlotConfig backend→device promotion, `assert s.backend==...`), `:118-135`, `:365-370` (CapabilitySelection promote/strip). **Keep** the `map_backend_to_device` edge-case tests `:87-111` (that translator stays).
- `tests/slot_config/test_store.py:132-140,160,191-192,213,229` — `assert after["backend"]==...`. Retarget to `device`.
- `tests/slot_config/test_merge_slot_config.py:239` — `data["backend"]=="vulkan"` → `device`.
- `tests/capabilities/test_orchestrator_reconciliation.py` — many `assert on_disk.get("backend")=="flm"/"rocm"` (`:204,257,455`) plus `"backend": ...` selection fixtures throughout. Retarget on-disk asserts to `device`; the fixture `"backend": "npu"` inputs depend on the §2d alias decision.
- `tests/cli/test_capabilities_commands.py:55,114` — `CapabilitySelection(backend=...)` → `device=`.
- `tests/cli/test_slot_create_flags.py:87,91,106,121,271` — `assert body["backend"]==...`. Retarget to `device` (mirrors the `slot_commands.py:463/552` edits).
- `tests/model_meta/test_model_meta.py:263-283,416-421` (+ import `:30`) — `device_to_legacy_backend` tests. **Delete** with the function.

**Reference / keep (already device-only or Concept B — do not change):**
- `tests/api/test_install_apply.py:60-61` — already asserts `"backend" not in cfg`; this is the target end-state, a good oracle.
- `tests/agents/test_hermes_state_render.py`, `tests/api/test_npu_occupancy.py`, `tests/api/test_slots_routes.py` (`declared_backend`), `tests/api/test_profiles_route.py`, `tests/api/test_meta_enums.py` (`LEGACY_BACKENDS`) — all Concept B / profile / enum. Leave.

## Summary

Only `device_to_legacy_backend` is deletable among the 4 translators; `canonical_device` is the device-normalizer to standardize on, while `device_to_backend` (runtime) and `map_backend_to_device` (legacy read + migration) stay. The real dual-write is exactly 5 config/CLI write sites (§2b) plus the two model fields. The two `_promote_backend_to_device` validators are the *only* backend→device promotion for pre-`device` on-disk TOMLs (no slot migration exists) — convert them to read-only promote-then-drop shims rather than deleting, or legacy `cpu`/`npu` slots silently regress to `gpu-rocm`. The API-response `backend` and everything `_cfg_effective_backend`/`declared_backend` touch are Concept-B (device-derived) and must be preserved.