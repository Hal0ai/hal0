# _engine

> 13 nodes

## Key Concepts

- **_engine()** (11 connections) — `tests/stacks/test_converge_primary.py`
- **_stack()** (11 connections) — `tests/stacks/test_converge_primary.py`
- **TestPrimaryConverge** (11 connections) — `tests/stacks/test_converge_primary.py`
- **StackSlotEntry** (9 connections)
- **RecordingSlotManager** (7 connections)
- **.test_offline_slot_is_loaded()** (6 connections) — `tests/stacks/test_converge_primary.py`
- **.test_dispatchable_different_model_is_swapped()** (6 connections) — `tests/stacks/test_converge_primary.py`
- **.test_dispatchable_same_model_is_skipped()** (6 connections) — `tests/stacks/test_converge_primary.py`
- **.test_transitional_slot_is_skipped()** (6 connections) — `tests/stacks/test_converge_primary.py`
- **.test_missing_snapshot_is_loaded()** (5 connections) — `tests/stacks/test_converge_primary.py`
- **.test_entry_without_model_is_ignored()** (5 connections) — `tests/stacks/test_converge_primary.py`
- **.test_load_failure_is_recorded_not_raised()** (5 connections) — `tests/stacks/test_converge_primary.py`
- **.test_converge_requires_slot_manager()** (4 connections) — `tests/stacks/test_converge_primary.py`

## Relationships

- [StackConfig](StackConfig.md) (10 shared connections)
- [StackApplyEngine](StackApplyEngine.md) (3 shared connections)
- [SlotState](SlotState.md) (1 shared connections)

## Source Files

- `tests/stacks/test_converge_primary.py`

## Audit Trail

- EXTRACTED: 90 (98%)
- INFERRED: 2 (2%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*