# FakeSnap

> 31 nodes · cohesion 0.16

## Key Concepts

- **FakeSnap** (23 connections) — `tests/stacks/conftest.py`
- **RecordingSlotManager** (14 connections) — `tests/stacks/conftest.py`
- **_engine()** (11 connections) — `tests/stacks/test_converge_primary.py`
- **_stack()** (11 connections) — `tests/stacks/test_converge_primary.py`
- **TestPrimaryConverge** (11 connections) — `tests/stacks/test_converge_primary.py`
- **conftest.py** (9 connections) — `tests/stacks/conftest.py`
- **StackSlotEntry** (9 connections)
- **test_converge_primary.py** (8 connections) — `tests/stacks/test_converge_primary.py`
- **RecordingSlotManager** (7 connections)
- **test_converge_unload.py** (7 connections) — `tests/stacks/test_converge_unload.py`
- **.test_dispatchable_different_model_is_swapped()** (6 connections) — `tests/stacks/test_converge_primary.py`
- **.test_dispatchable_same_model_is_skipped()** (6 connections) — `tests/stacks/test_converge_primary.py`
- **.test_offline_slot_is_loaded()** (6 connections) — `tests/stacks/test_converge_primary.py`
- **.test_transitional_slot_is_skipped()** (6 connections) — `tests/stacks/test_converge_primary.py`
- **.test_entry_without_model_is_ignored()** (5 connections) — `tests/stacks/test_converge_primary.py`
- **.test_load_failure_is_recorded_not_raised()** (5 connections) — `tests/stacks/test_converge_primary.py`
- **.test_missing_snapshot_is_loaded()** (5 connections) — `tests/stacks/test_converge_primary.py`
- **.test_converge_requires_slot_manager()** (4 connections) — `tests/stacks/test_converge_primary.py`
- **_no_seed_stacks()** (3 connections) — `tests/stacks/conftest.py`
- **.__init__()** (2 connections) — `tests/stacks/conftest.py`
- **.list()** (2 connections) — `tests/stacks/conftest.py`
- **.load()** (2 connections) — `tests/stacks/conftest.py`
- **.swap()** (2 connections) — `tests/stacks/conftest.py`
- **.unload()** (2 connections) — `tests/stacks/conftest.py`
- **MonkeyPatch** (1 connections)
- *... and 6 more nodes in this community*

## Relationships

- [StackConfig](StackConfig.md) (15 shared connections)
- [test_drift.py](test_drift.py.md) (6 shared connections)
- [SlotState](SlotState.md) (3 shared connections)
- [StackApplyEngine](StackApplyEngine.md) (3 shared connections)

## Source Files

- `tests/stacks/conftest.py`
- `tests/stacks/test_converge_primary.py`
- `tests/stacks/test_converge_unload.py`

## Audit Trail

- EXTRACTED: 169 (98%)
- INFERRED: 4 (2%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*