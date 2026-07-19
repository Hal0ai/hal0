# file_lock

> 20 nodes · cohesion 0.16

## Key Concepts

- **file_lock()** (15 connections) — `src/hal0/config/locking.py`
- **lock_path_for()** (6 connections) — `src/hal0/config/locking.py`
- **test_locking.py** (6 connections) — `tests/config/test_locking.py`
- **_contender()** (5 connections) — `tests/config/test_locking.py`
- **_hold_then_release()** (5 connections) — `tests/config/test_locking.py`
- **locking.py** (4 connections) — `src/hal0/config/locking.py`
- **test_reentrant_within_same_process()** (4 connections) — `tests/config/test_locking.py`
- **test_second_process_blocks_until_first_releases()** (4 connections) — `tests/config/test_locking.py`
- **Path** (3 connections)
- **test_creates_sibling_lock_file()** (3 connections) — `tests/config/test_locking.py`
- **_depths()** (2 connections) — `src/hal0/config/locking.py`
- **Path** (2 connections)
- **Any** (2 connections)
- **Cross-process advisory file locking for config read-modify-write (SC-10).  Sever** (1 connections) — `src/hal0/config/locking.py`
- **Return the sibling ``.lock`` path :func:`file_lock` locks for ``target``.** (1 connections) — `src/hal0/config/locking.py`
- **Hold an exclusive advisory lock serializing an RMW on ``target``.      Locks the** (1 connections) — `src/hal0/config/locking.py`
- **Unit tests for the shared advisory ``file_lock`` helper (SC-10).  Mirrors the in** (1 connections) — `tests/config/test_locking.py`
- **Acquire the lock, record acquire/release timestamps around a hold.** (1 connections) — `tests/config/test_locking.py`
- **Wait for the holder to grab the lock first, then block on acquire.** (1 connections) — `tests/config/test_locking.py`
- **A nested acquire on the same path in the same thread must not deadlock.** (1 connections) — `tests/config/test_locking.py`

## Relationships

- [CapabilitySelection](CapabilitySelection.md) (4 shared connections)
- [write_slot_toml](write_slot_toml.md) (3 shared connections)
- [SlotConfigStore](SlotConfigStore.md) (1 shared connections)

## Source Files

- `src/hal0/config/locking.py`
- `tests/config/test_locking.py`

## Audit Trail

- EXTRACTED: 48 (71%)
- INFERRED: 20 (29%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*