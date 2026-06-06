# NPU Phase 2 — Implementation Blueprint

**Date:** 2026-06-06 · **Branch:** `feat/npu-phase2` (off `main`) · **Spec:** `2026-06-05-npu-flm-stack-section-design.md` §Phase 2

Goal: a `device=npu` capability selection for **embed** / **voice.stt** drives the
**FLM trio** (one process) — set lemond `flm_args` + enable a `device=npu`,
`type=embedding|transcription` slot RECORD for dispatch gating — and **never**
spawns a standalone FLM process for the modality.

## Decisions (resolved by orchestrator — these override the blueprint's "open questions")

1. **No eager anchor-reload.** When enabling/disabling an NPU modality changes
   `flm_args` and the anchor is live, **do NOT auto-restart** it. Set the config,
   write the slot record, and return `pending_reload=True`. The user applies it via
   the NPU section's existing "⟳ reload to apply" affordance. (Consistency with
   Phase 1 + never interrupt an active chat unexpectedly.)
2. **Preserve unrecognized `flm_args` flags.** `_recompose_flm_args` must replace
   only the `--asr`/`--embed` tokens in place (append if absent) and keep all other
   tokens (`--threads`, etc.) verbatim. Always emit explicit `0|1` for both trio
   flags.
3. **`/api/models` FLM surfacing is OUT of this PR.** File as a follow-up; pickers
   degrade to empty meanwhile. The orchestrator change is complete without it.
4. **`update_config` ordering.** Call `_rewrite_underlying_slot` (which sets the
   `model` sub-table) BEFORE the `{enabled}`-only `update_config` write; the enabled
   write must NOT include `model` (nested dicts are replaced wholesale).

## Reconciliation decision: Design A — reuse canonical `embed`/`stt`, flip `device`

`_CHILD_TO_SLOT` already maps `("embed","embed")→"embed"`, `("voice","stt")→"stt"`
(`orchestrator.py:48-55`) — unchanged. The orchestrator operates on these canonical
slots; detection is device-driven (`device==npu` + `type`), never literal names. The
seeded `agent`/`stt-npu`/`embed-npu` (`manager.py:77`, `slots.py:112`) are the trio's
own records — `stt-npu`/`embed-npu` stay `enabled=false` at seed and the orchestrator
never touches them (prevents double-match in `_is_npu_trio_request`). The anchor is
found by scanning `iter_configs()` for `type=="llm" && device=="npu"` — never hardcode
`agent`.

## 1. Current `apply()` lifecycle (embed) — file:line

`orchestrator.py:318` entry → `:344-375` load+merge (`_CHILD_TO_SLOT`, backend→device
alias) → `:380-381` `_validate_model_in_catalog` (calls `models_for_capability`, checks
`device` in model's `legal_backends`) → `:386-419` lifecycle branches:
- `:403` `if merged.enabled: _rewrite_underlying_slot(...)` (always).
- `:406` off→on: `_ensure_slot_exists` + `slot_manager.load(slot, model_id)` ← STANDALONE SPAWN.
- `:411` on→off: `slot_manager.unload(slot)`.
- `:416` model/backend change: `_ensure_slot_exists` + `slot_manager.swap(slot, model)` ← STANDALONE.

`_rewrite_underlying_slot` (`:557-590`) sends `{backend,device,provider,model}` (no
`type`). `_ensure_slot_exists` (`:521-555`) creates with `{name,port,backend,device,
provider,enabled,model}` — **no `type`**. **The gap:** `_is_npu_trio_request`
(`v1.py:783`) gates on `cfg.get("type")==slot_type` FIRST — absent `type` → trio
dispatch never activates. `update_config` is a shallow top-level merge
(`manager.py:1428`), so an existing `type` survives a rewrite, but a created slot never
gets one.

## 2. The NPU-trio fork

After merge+validation (`~:393`), compute `is_npu_target = merged.device=="npu" and
child in ("embed","stt")`. Keep `if merged.enabled: _rewrite_underlying_slot(...)`,
then branch:
```python
if is_npu_target:
    pending = await self._apply_npu_trio_modality(slot_name, child, merged, before_enabled)
else:
    ... existing enabled/model/backend branches ...
```

### New `async _apply_npu_trio_modality(slot_name, child, merged, before_enabled) -> bool`
1. `_ensure_slot_exists_npu(slot_name, child, merged)` (create path writes `type`).
2. `update_config(slot_name, {"enabled": merged.enabled})` — explicit, covers enable AND
   disable (so `_is_npu_trio_request`'s `enabled is False` check blocks dispatch). Runs
   AFTER `_rewrite_underlying_slot` (Decision 4).
3. Read-modify-write `flm_args` via the lemonade client:
   `cfg = await client.internal_config(); new = _recompose_flm_args(cfg.get("flm_args") or "", child, merged.enabled); await client.internal_set({"flm_args": new})`.
4. **Decision 1:** find anchor (`iter_configs` → `type==llm && device==npu`); if live,
   return `pending_reload=True` (do NOT restart). If not live, also `pending_reload`
   (won't take effect until the user loads it). Surface `pending_reload` in the
   `apply()` response.
5. **No `load()`/`swap()`/`unload()` on the embed/stt slot.**

### `_ensure_slot_exists_npu` — like `_ensure_slot_exists` but cfg includes
`"type": "embedding" if child=="embed" else "transcription"`, `device="npu"`,
`provider="flm"`, `backend="flm"`.

### `_recompose_flm_args(current, child, enable)` — module-level, **Decision 2**:
parse all tokens, set only the `--asr`/`--embed` flag for `child`, preserve every other
token, append the trio flags if absent, always emit explicit `0|1`. Unit-tested.

### Lemonade client wiring
Add `lemonade_provider` param to `CapabilityOrchestrator.__init__`; pass from
`create_app` lifespan (same place the orchestrator is constructed). Local import to keep
load cheap.

## 3. `asr` fanout — `catalog.py`
FLM ASR rows come from `_flm_rows_for_capability` (`catalog.py:620-672`), gated by the
early-return `if capability not in {"chat","embed"}: return []` (`:638`) and the
`reported_caps` filter (`:653`). Changes:
- `:255` `_NPU_FANOUT_CAPS = frozenset({"chat","embed","stt"})` (+ update comment).
- `:638` widen guard to `{"chat","embed","stt"}`.
- `:653` widen `reported_caps` filter to `{"chat","embed","stt"}`.
`providers/flm.py` `_classify_flm_model` already emits `"stt"` — no change. **Serial
prerequisite** for the orchestrator npu-stt path (validation calls
`models_for_capability("stt")` and would reject `npu` otherwise).

## 4. Device-partition invariant
Single `device` per selection; orchestrator is sole writer of `capabilities.toml`. After
`apply(...,device=npu)`: capabilities.toml + the `embed`/`stt` slot TOML both say
`device=npu, enabled=true` → frontend NPU section (keys on `device==npu`) owns it;
Capabilities grid reads `selections.<c>.device` and suppresses/links it. Switching to
`gpu-vulkan` flips device (trio dispatch bypassed) and the npu-disable path sets
`--embed/asr 0` + `enabled=false`.

## 6. Test plan — `tests/capabilities/test_orchestrator_reconciliation.py`
Extend `FakeSlotManager` with `iter_configs()`/`set_configs()`/`restart`; add
`FakeLemonadeClient` (`internal_config`/`internal_set`, records `set_calls`). Inject via
`lemonade_provider=lambda: client`. TDD cases:
1. NPU embed enable → `set_calls[-1]=={"flm_args":"--asr 1 --embed 1"}`, slot
   `update_config enabled=True`, **NO** `load`, `pending_reload` set (Decision 1: no restart).
2. NPU embed disable → `flm_args "--asr 1 --embed 0"`, slot `enabled=False` written, **NO** `unload`.
3. NPU stt enable → `flm_args "--asr 1 --embed ?"` with asr=1, no standalone load.
4. embed gpu-vulkan→npu → flm_args embed=1, no load/swap on embed, update_config device=npu+enabled.
5. embed npu→gpu-vulkan (and back) → flm_args embed=0 + device flip; gpu path DOES `load`.
6. `_recompose_flm_args` unit: `""→"--asr 0 --embed 1"` (embed,True); preserves other flags
   e.g. `"--threads 8 --asr 1 --embed 0"` + (embed,True) → keeps `--threads 8` (Decision 2).
7. New `tests/capabilities/test_catalog_npu_stt.py`: `models_for_capability("stt")` yields a
   row with an `npu` backend.
8. `_ensure_slot_exists_npu` create writes `type=="embedding"`.
9. Anchor offline → `pending_reload=True`, no restart.

## 7. Build sequence (green at each commit)
1. `_CHILD_TO_SLOT_TYPE` + add `type` to `_ensure_slot_exists` (+Case 8). Standalone.
2. `_recompose_flm_args` helper (+Case 6). Standalone.
3. Catalog widen to `stt` (+Case 7). **Serial prereq for step 5.**
4. Test stubs: `FakeSlotManager.iter_configs/restart`, `FakeLemonadeClient`. Standalone.
5. `_apply_npu_trio_modality` + `lemonade_provider` injection + `apply()` fork + wire in
   `create_app` (+Cases 1-5,9). Serial after 1-4.
6. Verify standard (non-NPU) disable path not regressed (`test_apply_no_rewrite_on_pure_disable`).
7. Update module docstring (remove "NPU multiplex OUT OF SCOPE").
8. `tests/capabilities/test_npu_phase2_integration.py` smoke (full drift fixture + npu embed).

## Files
`src/hal0/capabilities/orchestrator.py` (main), `src/hal0/capabilities/catalog.py`
(:255,638,653), `create_app` construction site (`src/hal0/api/__init__.py` or lifespan),
`tests/capabilities/test_orchestrator_reconciliation.py`, + new
`test_catalog_npu_stt.py`, `test_npu_phase2_integration.py`.

## Established facts
- STT trio dispatch already wired: `v1.py:1245` calls `_is_npu_trio_request(...,
  slot_type="transcription")` → `dispatch_stt_npu`. Enabling the `stt` slot
  (device=npu,type=transcription,enabled) activates it.
- `update_config` is read-merge(top-level)-write; existing `type` survives a rewrite.

## Follow-ups (separate)
- `/api/models` surfaces 0 FLM models → empty NPU pickers (Decision 3).
- VibeVoice TTS provider (its own spec).
