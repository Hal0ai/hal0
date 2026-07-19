# test_board_dispatch.py

> 19 nodes

## Key Concepts

- **test_board_dispatch.py** (13 connections) — `tests/board/test_board_dispatch.py`
- **register_executor()** (10 connections) — `src/hal0/board/dispatch.py`
- **_store()** (7 connections) — `tests/board/test_board_dispatch.py`
- **.dispatch()** (6 connections) — `tests/api/test_chat_normalization.py`
- **test_executor_may_not_reshape_canonical_state()** (6 connections) — `tests/board/test_board_dispatch.py`
- **Path** (5 connections)
- **test_nudge_dispatches_ready_cards_with_executor()** (5 connections) — `tests/board/test_board_dispatch.py`
- **test_nudge_respects_max()** (5 connections) — `tests/board/test_board_dispatch.py`
- **clear_executors()** (4 connections) — `src/hal0/board/dispatch.py`
- **test_registered_executor_dispatches_and_writes_back()** (4 connections) — `tests/board/test_board_dispatch.py`
- **test_explicit_attempt_id_overrides_executor()** (4 connections) — `tests/board/test_board_dispatch.py`
- **test_nudge_zero_without_executor()** (3 connections) — `tests/board/test_board_dispatch.py`
- **_clean_registry()** (2 connections) — `tests/board/test_board_dispatch.py`
- **test_empty_registry_dispatches_nothing()** (2 connections) — `tests/board/test_board_dispatch.py`
- **test_seam_module_exports()** (2 connections) — `tests/board/test_board_dispatch.py`
- **Register the executor that services ``target`` (idempotent overwrite).** (1 connections) — `src/hal0/board/dispatch.py`
- **Drop all registered executors (test isolation / teardown).** (1 connections) — `src/hal0/board/dispatch.py`
- **Board executor dispatch seam (KB-5) — interface, registry, no-op default.  Run t** (1 connections) — `tests/board/test_board_dispatch.py`
- **The writeback appends runs/events only — the card's lane is unchanged by     a d** (1 connections) — `tests/board/test_board_dispatch.py`

## Relationships

- [AttemptHandle](AttemptHandle.md) (10 shared connections)
- [test_hermes_executor.py](test_hermes_executor.py.md) (3 shared connections)
- [HermesBoardExecutor](HermesBoardExecutor.md) (1 shared connections)
- [test_chat_normalization.py](test_chat_normalization.py.md) (1 shared connections)
- [FakeMemoryProvider](FakeMemoryProvider.md) (1 shared connections)
- [BoardStore](BoardStore.md) (1 shared connections)
- [RequestSeam](RequestSeam.md) (1 shared connections)

## Source Files

- `src/hal0/board/dispatch.py`
- `tests/api/test_chat_normalization.py`
- `tests/board/test_board_dispatch.py`

## Audit Trail

- EXTRACTED: 53 (65%)
- INFERRED: 29 (35%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*