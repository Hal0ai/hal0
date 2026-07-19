# StackConfig

> 51 nodes · cohesion 0.09

## Key Concepts

- **StackConfig** (65 connections) — `src/hal0/config/schema.py`
- **StackSlotEntry** (40 connections) — `src/hal0/config/schema.py`
- **RecordingOrchestrator** (19 connections) — `tests/stacks/conftest.py`
- **_engine()** (10 connections) — `tests/stacks/test_converge_capabilities.py`
- **_engine()** (9 connections) — `tests/stacks/test_converge_unload.py`
- **test_converge_capabilities.py** (8 connections) — `tests/stacks/test_converge_capabilities.py`
- **TestCapabilityRouting** (8 connections) — `tests/stacks/test_converge_capabilities.py`
- **TestUnloadSweep** (8 connections) — `tests/stacks/test_converge_unload.py`
- **_row()** (7 connections) — `tests/stacks/test_converge_capabilities.py`
- **.test_enabled_capability_slot_is_not_unloaded()** (7 connections) — `tests/stacks/test_converge_unload.py`
- **test_apply_validate.py** (6 connections) — `tests/stacks/test_apply_validate.py`
- **_engine()** (6 connections) — `tests/stacks/test_apply_validate.py`
- **.test_disabled_row_is_not_applied()** (6 connections) — `tests/stacks/test_converge_capabilities.py`
- **.test_embed_row_routes_to_embed_group()** (6 connections) — `tests/stacks/test_converge_capabilities.py`
- **.test_rerank_routes_to_embed_group()** (6 connections) — `tests/stacks/test_converge_capabilities.py`
- **.test_stt_and_tts_route_to_voice_group()** (6 connections) — `tests/stacks/test_converge_capabilities.py`
- **.test_unknown_child_is_recorded_as_error()** (6 connections) — `tests/stacks/test_converge_capabilities.py`
- **.test_offline_slot_not_in_stack_is_left_alone()** (6 connections) — `tests/stacks/test_converge_unload.py`
- **.test_running_slot_not_in_stack_is_unloaded()** (6 connections) — `tests/stacks/test_converge_unload.py`
- **.test_stack_primary_slot_is_not_unloaded()** (6 connections) — `tests/stacks/test_converge_unload.py`
- **test_validate_ignores_entries_without_refs()** (5 connections) — `tests/stacks/test_apply_validate.py`
- **.test_apply_failure_is_recorded()** (5 connections) — `tests/stacks/test_converge_capabilities.py`
- **RecordingSlotManager** (5 connections)
- **.test_unload_failure_is_recorded()** (5 connections) — `tests/stacks/test_converge_unload.py`
- **TestStackConfig** (4 connections) — `tests/config/test_stacks_schema.py`
- *... and 26 more nodes in this community*

## Relationships

- [StackApplyEngine](StackApplyEngine.md) (23 shared connections)
- [FakeSnap](FakeSnap.md) (15 shared connections)
- [StacksCatalog](StacksCatalog.md) (10 shared connections)
- [embed_references](embed_references.md) (9 shared connections)
- [portable.py](portable.py.md) (6 shared connections)
- [test_drift.py](test_drift.py.md) (5 shared connections)
- [stacks.py](stacks.py.md) (4 shared connections)
- [schema.py](schema.py.md) (4 shared connections)
- [StackModelMeta](StackModelMeta.md) (4 shared connections)
- [test_seeds_parity.py](test_seeds_parity.py.md) (2 shared connections)
- [save_profiles_config](save_profiles_config.md) (2 shared connections)
- [ModelRegistry](ModelRegistry.md) (2 shared connections)

## Source Files

- `src/hal0/config/schema.py`
- `src/hal0/stacks/apply.py`
- `tests/config/test_stacks_schema.py`
- `tests/stacks/conftest.py`
- `tests/stacks/test_apply_validate.py`
- `tests/stacks/test_converge_capabilities.py`
- `tests/stacks/test_converge_unload.py`

## Audit Trail

- EXTRACTED: 215 (68%)
- INFERRED: 101 (32%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*