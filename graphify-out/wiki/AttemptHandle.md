# AttemptHandle

> 39 nodes

## Key Concepts

- **AttemptHandle** (22 connections) — `src/hal0/board/dispatch.py`
- **_SpyStore** (14 connections) — `tests/board/test_hermes_executor.py`
- **BoardExecutor** (12 connections) — `src/hal0/board/dispatch.py`
- **NoopExecutor** (12 connections) — `src/hal0/board/dispatch.py`
- **dispatch.py** (9 connections) — `src/hal0/board/dispatch.py`
- **dispatch()** (7 connections) — `src/hal0/board/dispatch.py`
- **.with_status()** (4 connections) — `src/hal0/board/dispatch.py`
- **Any** (4 connections)
- **.dispatch()** (4 connections) — `src/hal0/board/dispatch.py`
- **.dispatch()** (4 connections) — `src/hal0/board/dispatch.py`
- **DispatchResult** (3 connections) — `src/hal0/board/dispatch.py`
- **.inspect()** (3 connections) — `src/hal0/board/dispatch.py`
- **.cancel()** (3 connections) — `src/hal0/board/dispatch.py`
- **.reconcile()** (3 connections) — `src/hal0/board/dispatch.py`
- **.cancel()** (3 connections) — `src/hal0/board/dispatch.py`
- **test_noop_executor_conforms_to_protocol()** (3 connections) — `tests/board/test_board_dispatch.py`
- **.inspect()** (2 connections) — `src/hal0/board/dispatch.py`
- **.reconcile()** (2 connections) — `src/hal0/board/dispatch.py`
- **test_attempt_handle_with_status_is_immutable()** (2 connections) — `tests/board/test_board_dispatch.py`
- **Writeback** (1 connections)
- **Board executor dispatch seam (KB-5) — interface + registry, no-op default.  hal0** (1 connections) — `src/hal0/board/dispatch.py`
- **One immutable dispatch attempt + its cross-system correlation.      hal0 owns ``** (1 connections) — `src/hal0/board/dispatch.py`
- **Return a copy advanced to ``status`` with any new correlation ids.** (1 connections) — `src/hal0/board/dispatch.py`
- **Outcome of a :func:`dispatch` call.** (1 connections) — `src/hal0/board/dispatch.py`
- **The narrow executor interface a backend (e.g. Hermes) implements.      Implement** (1 connections) — `src/hal0/board/dispatch.py`
- *... and 14 more nodes in this community*

## Relationships

- [test_board_dispatch.py](test_board_dispatch.py.md) (10 shared connections)
- [test_hermes_executor.py](test_hermes_executor.py.md) (8 shared connections)
- [HermesBoardExecutor](HermesBoardExecutor.md) (8 shared connections)
- [compute_config_drift](compute_config_drift.md) (1 shared connections)
- [BoardStore](BoardStore.md) (1 shared connections)

## Source Files

- `src/hal0/board/dispatch.py`
- `tests/board/test_board_dispatch.py`
- `tests/board/test_hermes_executor.py`

## Audit Trail

- EXTRACTED: 117 (86%)
- INFERRED: 19 (14%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*