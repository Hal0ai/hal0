# AttemptHandle

> 31 nodes · cohesion 0.10

## Key Concepts

- **AttemptHandle** (22 connections) — `src/hal0/board/dispatch.py`
- **BoardExecutor** (12 connections) — `src/hal0/board/dispatch.py`
- **NoopExecutor** (12 connections) — `src/hal0/board/dispatch.py`
- **dispatch.py** (9 connections) — `src/hal0/board/dispatch.py`
- **dispatch()** (7 connections) — `src/hal0/board/dispatch.py`
- **.with_status()** (4 connections) — `src/hal0/board/dispatch.py`
- **.dispatch()** (4 connections) — `src/hal0/board/dispatch.py`
- **.dispatch()** (4 connections) — `src/hal0/board/dispatch.py`
- **Any** (4 connections)
- **.cancel()** (3 connections) — `src/hal0/board/dispatch.py`
- **.inspect()** (3 connections) — `src/hal0/board/dispatch.py`
- **.reconcile()** (3 connections) — `src/hal0/board/dispatch.py`
- **DispatchResult** (3 connections) — `src/hal0/board/dispatch.py`
- **.cancel()** (3 connections) — `src/hal0/board/dispatch.py`
- **test_noop_executor_conforms_to_protocol()** (3 connections) — `tests/board/test_board_dispatch.py`
- **.inspect()** (2 connections) — `src/hal0/board/dispatch.py`
- **.reconcile()** (2 connections) — `src/hal0/board/dispatch.py`
- **test_attempt_handle_with_status_is_immutable()** (2 connections) — `tests/board/test_board_dispatch.py`
- **Protocol** (1 connections)
- **Board executor dispatch seam (KB-5) — interface + registry, no-op default.  hal0** (1 connections) — `src/hal0/board/dispatch.py`
- **Cancel the external run; return the terminal handle.** (1 connections) — `src/hal0/board/dispatch.py`
- **Re-sync after a disconnect; return the reconciled handle.** (1 connections) — `src/hal0/board/dispatch.py`
- **Dispatch one attempt for ``card_id`` to ``target``'s executor.      Returns a :c** (1 connections) — `src/hal0/board/dispatch.py`
- **Reference no-op executor: accepts a dispatch but does no external work.      Not** (1 connections) — `src/hal0/board/dispatch.py`
- **One immutable dispatch attempt + its cross-system correlation.      hal0 owns ``** (1 connections) — `src/hal0/board/dispatch.py`
- *... and 6 more nodes in this community*

## Relationships

- [test_board_dispatch.py](test_board_dispatch.py.md) (9 shared connections)
- [HermesBoardExecutor](HermesBoardExecutor.md) (6 shared connections)
- [get_executor](get_executor.md) (4 shared connections)
- [test_hermes_executor.py](test_hermes_executor.py.md) (3 shared connections)
- [_SpyStore](_SpyStore.md) (2 shared connections)
- [_HermesGateway](_HermesGateway.md) (1 shared connections)

## Source Files

- `src/hal0/board/dispatch.py`
- `tests/board/test_board_dispatch.py`

## Audit Trail

- EXTRACTED: 99 (86%)
- INFERRED: 16 (14%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*