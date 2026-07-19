# snapshot_live_stack

> 23 nodes

## Key Concepts

- **snapshot_live_stack()** (17 connections) — `src/hal0/stacks/portable.py`
- **ModelRegistry** (7 connections)
- **_write_slot()** (7 connections) — `tests/stacks/test_snapshot.py`
- **TestSnapshot** (7 connections) — `tests/stacks/test_snapshot.py`
- **StackSlotEntry** (6 connections) — `src/hal0/config/schema.py`
- **StackCapabilityRow** (5 connections) — `src/hal0/config/schema.py`
- **test_snapshot.py** (5 connections) — `tests/stacks/test_snapshot.py`
- **.test_captures_capability_rows()** (5 connections) — `tests/stacks/test_snapshot.py`
- **.test_unset_capability_device_is_skipped()** (5 connections) — `tests/stacks/test_snapshot.py`
- **Path** (4 connections)
- **_write_caps()** (4 connections) — `tests/stacks/test_snapshot.py`
- **.test_captures_primary_slot()** (4 connections) — `tests/stacks/test_snapshot.py`
- **.test_captures_flat_shape_slot()** (4 connections) — `tests/stacks/test_snapshot.py`
- **.test_legacy_backend_device_dropped()** (4 connections) — `tests/stacks/test_snapshot.py`
- **.test_empty_slot_is_skipped()** (4 connections) — `tests/stacks/test_snapshot.py`
- **reg()** (3 connections) — `tests/stacks/test_snapshot.py`
- **.device_valid()** (1 connections) — `src/hal0/config/schema.py`
- **.slot_valid()** (1 connections) — `src/hal0/config/schema.py`
- **.device_valid()** (1 connections) — `src/hal0/config/schema.py`
- **One (slot, child) capability selection carried by a stack slot entry.      Mirro** (1 connections) — `src/hal0/config/schema.py`
- **One slot's contribution to a stack: which model/profile/caps it carries.      Re** (1 connections) — `src/hal0/config/schema.py`
- **Build a StackConfig from the current on-disk slots + capabilities.      Reads ``** (1 connections) — `src/hal0/stacks/portable.py`
- **Tests for snapshot-from-live: read slots + capabilities → a StackConfig.  Target** (1 connections) — `tests/stacks/test_snapshot.py`

## Relationships

- [embed_references](embed_references.md) (5 shared connections)
- [BaseModel](BaseModel.md) (2 shared connections)
- [schema.py](schema.py.md) (2 shared connections)
- [stacks.py](stacks.py.md) (1 shared connections)
- [CapabilitySelection](CapabilitySelection.md) (1 shared connections)
- [StackConfig](StackConfig.md) (1 shared connections)

## Source Files

- `src/hal0/config/schema.py`
- `src/hal0/stacks/portable.py`
- `tests/stacks/test_snapshot.py`

## Audit Trail

- EXTRACTED: 80 (82%)
- INFERRED: 18 (18%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*