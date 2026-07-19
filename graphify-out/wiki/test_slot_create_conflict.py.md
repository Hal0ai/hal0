# test_slot_create_conflict.py

> 8 nodes

## Key Concepts

- **test_slot_create_conflict.py** (4 connections) — `tests/slots/test_slot_create_conflict.py`
- **test_create_rejects_duplicate_and_preserves_config_and_state()** (4 connections) — `tests/slots/test_slot_create_conflict.py`
- **test_reconcile_precheck_pattern_is_idempotent_noop()** (4 connections) — `tests/slots/test_slot_create_conflict.py`
- **_slot_toml()** (3 connections) — `tests/slots/test_slot_create_conflict.py`
- **Path** (1 connections)
- **SC-5: SlotManager.create() must not clobber an existing slot.  Before this guard** (1 connections) — `tests/slots/test_slot_create_conflict.py`
- **A second create() for the same name raises and touches nothing.      Asserts thr** (1 connections) — `tests/slots/test_slot_create_conflict.py`
- **Internal reconcile callers pre-check cfg_path.exists() → never reject.      inst** (1 connections) — `tests/slots/test_slot_create_conflict.py`

## Relationships

- [SlotManager](SlotManager.md) (2 shared connections)
- [SlotConfigError](SlotConfigError.md) (1 shared connections)

## Source Files

- `tests/slots/test_slot_create_conflict.py`

## Audit Trail

- EXTRACTED: 16 (84%)
- INFERRED: 3 (16%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*