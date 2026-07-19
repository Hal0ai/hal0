# _write_slot

> 19 nodes

## Key Concepts

- **_write_slot()** (10 connections) — `tests/slots/test_default_uniqueness.py`
- **test_default_uniqueness.py** (9 connections) — `tests/slots/test_default_uniqueness.py`
- **Path** (8 connections)
- **test_create_rejects_second_default_of_same_type()** (6 connections) — `tests/slots/test_default_uniqueness.py`
- **test_update_config_rejects_flipping_default_when_one_exists()** (6 connections) — `tests/slots/test_default_uniqueness.py`
- **test_default_slot_for_still_raises_on_two_disk_defaults()** (6 connections) — `tests/slots/test_default_uniqueness.py`
- **test_create_allows_first_default_of_type()** (5 connections) — `tests/slots/test_default_uniqueness.py`
- **test_create_allows_default_of_different_type()** (5 connections) — `tests/slots/test_default_uniqueness.py`
- **test_create_allows_non_default_peer_of_same_type()** (5 connections) — `tests/slots/test_default_uniqueness.py`
- **test_update_config_sole_default_does_not_self_conflict()** (5 connections) — `tests/slots/test_default_uniqueness.py`
- **Write-time "one default per type" validation in SlotManager (SC-4).  ARCHITECTUR** (1 connections) — `tests/slots/test_default_uniqueness.py`
- **Seed a minimal slot TOML without going through SlotManager.** (1 connections) — `tests/slots/test_default_uniqueness.py`
- **Creating a second ``type=llm, default=true`` slot must be rejected.** (1 connections) — `tests/slots/test_default_uniqueness.py`
- **Flipping ``default=false → true`` when a peer default exists is blocked.** (1 connections) — `tests/slots/test_default_uniqueness.py`
- **Routing-time backstop: two on-disk defaults still raise at resolve.** (1 connections) — `tests/slots/test_default_uniqueness.py`
- **The first default of a type is legal even with a non-default peer.** (1 connections) — `tests/slots/test_default_uniqueness.py`
- **A default of a DIFFERENT type does not conflict with an llm default.** (1 connections) — `tests/slots/test_default_uniqueness.py`
- **A non-default peer may coexist alongside an existing default.** (1 connections) — `tests/slots/test_default_uniqueness.py`
- **Updating the sole default without touching the default flag is legal.      The p** (1 connections) — `tests/slots/test_default_uniqueness.py`

## Relationships

- [SlotManager](SlotManager.md) (7 shared connections)
- [SlotConfigError](SlotConfigError.md) (3 shared connections)

## Source Files

- `tests/slots/test_default_uniqueness.py`

## Audit Trail

- EXTRACTED: 64 (86%)
- INFERRED: 10 (14%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*