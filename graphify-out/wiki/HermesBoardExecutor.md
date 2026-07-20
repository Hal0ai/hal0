# HermesBoardExecutor

> 26 nodes · cohesion 0.15

## Key Concepts

- **HermesBoardExecutor** (16 connections) — `src/hal0/board/hermes_executor.py`
- **.dispatch()** (10 connections) — `src/hal0/board/hermes_executor.py`
- **.request()** (9 connections) — `src/hal0/board/hermes_executor.py`
- **hermes_executor.py** (8 connections) — `src/hal0/board/hermes_executor.py`
- **.reconcile()** (8 connections) — `src/hal0/board/hermes_executor.py`
- **._detail()** (7 connections) — `src/hal0/board/hermes_executor.py`
- **.inspect()** (7 connections) — `src/hal0/board/hermes_executor.py`
- **Any** (7 connections)
- **._correlation()** (6 connections) — `src/hal0/board/hermes_executor.py`
- **register()** (6 connections) — `src/hal0/board/hermes_executor.py`
- **_err()** (5 connections) — `src/hal0/board/hermes_executor.py`
- **.cancel()** (5 connections) — `src/hal0/board/hermes_executor.py`
- **_map_state()** (5 connections) — `src/hal0/board/hermes_executor.py`
- **_is_configured()** (3 connections) — `src/hal0/board/hermes_executor.py`
- **._new_attempt_id()** (2 connections) — `src/hal0/board/hermes_executor.py`
- **Concrete Hermes :class:`~hal0.board.dispatch.BoardExecutor` (HP-executor, KB-5).** (1 connections) — `src/hal0/board/hermes_executor.py`
- **Send one request; return ``(status_code, parsed_json)``.          A transport fa** (1 connections) — `src/hal0/board/hermes_executor.py`
- **Concrete :class:`~hal0.board.dispatch.BoardExecutor` for target ``hermes``.** (1 connections) — `src/hal0/board/hermes_executor.py`
- **Pull the Hermes-side correlation ids out of a worker response.** (1 connections) — `src/hal0/board/hermes_executor.py`
- **Merge worker telemetry (heartbeat / handoff) into the handle detail.          Ap** (1 connections) — `src/hal0/board/hermes_executor.py`
- **Start one Hermes worker run for ``card_id``; return its handle.          Posts t** (1 connections) — `src/hal0/board/hermes_executor.py`
- **Poll the run's current state (incl. worker heartbeat / handoff).          A read** (1 connections) — `src/hal0/board/hermes_executor.py`
- **Cancel the external run; return the terminal handle.          A confirmed cancel** (1 connections) — `src/hal0/board/hermes_executor.py`
- **Re-sync after a disconnect: RECOVER the run's state, or declare it LOST.** (1 connections) — `src/hal0/board/hermes_executor.py`
- **Is Hermes configured for this process? (config presence, NO network call).** (1 connections) — `src/hal0/board/hermes_executor.py`
- *... and 1 more nodes in this community*

## Relationships

- [AttemptHandle](AttemptHandle.md) (6 shared connections)
- [_HermesGateway](_HermesGateway.md) (5 shared connections)
- [secrets.py](secrets.py.md) (1 shared connections)
- [test_hermes_executor.py](test_hermes_executor.py.md) (1 shared connections)
- [_SpyStore](_SpyStore.md) (1 shared connections)
- [get_executor](get_executor.md) (1 shared connections)
- [test_mcp_transport_security.py](test_mcp_transport_security.py.md) (1 shared connections)
- [test_board_dispatch.py](test_board_dispatch.py.md) (1 shared connections)

## Source Files

- `src/hal0/board/hermes_executor.py`

## Audit Trail

- EXTRACTED: 110 (96%)
- INFERRED: 5 (4%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*