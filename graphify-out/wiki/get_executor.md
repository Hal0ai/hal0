# get_executor

> 8 nodes · cohesion 0.29

## Key Concepts

- **get_executor()** (5 connections) — `src/hal0/board/dispatch.py`
- **clear_executors()** (4 connections) — `src/hal0/board/dispatch.py`
- **_clean()** (4 connections) — `tests/board/test_hermes_executor.py`
- **test_register_active_when_configured()** (4 connections) — `tests/board/test_hermes_executor.py`
- **test_register_inert_without_config()** (4 connections) — `tests/board/test_hermes_executor.py`
- **MonkeyPatch** (3 connections)
- **_clean_registry()** (2 connections) — `tests/board/test_board_dispatch.py`
- **Drop all registered executors (test isolation / teardown).** (1 connections) — `src/hal0/board/dispatch.py`

## Relationships

- [AttemptHandle](AttemptHandle.md) (4 shared connections)
- [test_hermes_executor.py](test_hermes_executor.py.md) (3 shared connections)
- [test_board_dispatch.py](test_board_dispatch.py.md) (2 shared connections)
- [systemd.py](systemd.py.md) (1 shared connections)
- [HermesBoardExecutor](HermesBoardExecutor.md) (1 shared connections)

## Source Files

- `src/hal0/board/dispatch.py`
- `tests/board/test_board_dispatch.py`
- `tests/board/test_hermes_executor.py`

## Audit Trail

- EXTRACTED: 16 (59%)
- INFERRED: 11 (41%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*