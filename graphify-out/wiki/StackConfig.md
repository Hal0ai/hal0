# StackConfig

> 53 nodes

## Key Concepts

- **StackConfig** (65 connections) — `src/hal0/config/schema.py`
- **FakeSnap** (23 connections) — `tests/stacks/conftest.py`
- **RecordingOrchestrator** (19 connections) — `tests/stacks/conftest.py`
- **RecordingSlotManager** (14 connections) — `tests/stacks/conftest.py`
- **_engine()** (10 connections) — `tests/stacks/test_converge_capabilities.py`
- **conftest.py** (9 connections) — `tests/stacks/conftest.py`
- **_engine()** (9 connections) — `tests/stacks/test_converge_unload.py`
- **test_converge_capabilities.py** (8 connections) — `tests/stacks/test_converge_capabilities.py`
- **TestCapabilityRouting** (8 connections) — `tests/stacks/test_converge_capabilities.py`
- **test_converge_primary.py** (8 connections) — `tests/stacks/test_converge_primary.py`
- **TestUnloadSweep** (8 connections) — `tests/stacks/test_converge_unload.py`
- **_row()** (7 connections) — `tests/stacks/test_converge_capabilities.py`
- **test_converge_unload.py** (7 connections) — `tests/stacks/test_converge_unload.py`
- **test_apply_validate.py** (6 connections) — `tests/stacks/test_apply_validate.py`
- **_engine()** (6 connections) — `tests/stacks/test_apply_validate.py`
- **.test_embed_row_routes_to_embed_group()** (5 connections) — `tests/stacks/test_converge_capabilities.py`
- **.test_rerank_routes_to_embed_group()** (5 connections) — `tests/stacks/test_converge_capabilities.py`
- **.test_stt_and_tts_route_to_voice_group()** (5 connections) — `tests/stacks/test_converge_capabilities.py`
- **.test_disabled_row_is_not_applied()** (5 connections) — `tests/stacks/test_converge_capabilities.py`
- **.test_unknown_child_is_recorded_as_error()** (5 connections) — `tests/stacks/test_converge_capabilities.py`
- **RecordingSlotManager** (5 connections)
- **.test_running_slot_not_in_stack_is_unloaded()** (5 connections) — `tests/stacks/test_converge_unload.py`
- **.test_stack_primary_slot_is_not_unloaded()** (5 connections) — `tests/stacks/test_converge_unload.py`
- **.test_enabled_capability_slot_is_not_unloaded()** (5 connections) — `tests/stacks/test_converge_unload.py`
- **.test_offline_slot_not_in_stack_is_left_alone()** (5 connections) — `tests/stacks/test_converge_unload.py`
- *... and 28 more nodes in this community*

## Relationships

- [StackApplyEngine](StackApplyEngine.md) (27 shared connections)
- [_engine](_engine.md) (10 shared connections)
- [embed_references](embed_references.md) (9 shared connections)
- [StacksCatalog](StacksCatalog.md) (6 shared connections)
- [stacks.py](stacks.py.md) (4 shared connections)
- [StackModelMeta](StackModelMeta.md) (4 shared connections)
- [SlotState](SlotState.md) (4 shared connections)
- [schema.py](schema.py.md) (2 shared connections)
- [BaseModel](BaseModel.md) (1 shared connections)
- [seeds.py](seeds.py.md) (1 shared connections)
- [snapshot_live_stack](snapshot_live_stack.md) (1 shared connections)
- [test_stacks_routes.py](test_stacks_routes.py.md) (1 shared connections)

## Source Files

- `src/hal0/config/schema.py`
- `tests/stacks/conftest.py`
- `tests/stacks/test_apply_validate.py`
- `tests/stacks/test_converge_capabilities.py`
- `tests/stacks/test_converge_primary.py`
- `tests/stacks/test_converge_unload.py`

## Audit Trail

- EXTRACTED: 265 (86%)
- INFERRED: 44 (14%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*