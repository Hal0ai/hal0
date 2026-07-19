# test_hermes_executor.py

> 26 nodes · cohesion 0.18

## Key Concepts

- **test_hermes_executor.py** (25 connections) — `tests/board/test_hermes_executor.py`
- **_executor()** (22 connections) — `tests/board/test_hermes_executor.py`
- **_running_handle()** (12 connections) — `tests/board/test_hermes_executor.py`
- **_json()** (9 connections) — `tests/board/test_hermes_executor.py`
- **test_executor_never_mutates_canonical_state_via_nudge()** (6 connections) — `tests/board/test_hermes_executor.py`
- **test_conforms_to_board_executor_protocol()** (4 connections) — `tests/board/test_hermes_executor.py`
- **test_executor_holds_no_store_reference()** (4 connections) — `tests/board/test_hermes_executor.py`
- **test_reconcile_declares_lost_on_run_unknown()** (4 connections) — `tests/board/test_hermes_executor.py`
- **test_reconcile_lost_when_no_run_id()** (4 connections) — `tests/board/test_hermes_executor.py`
- **test_reconcile_recovers_completed_run()** (4 connections) — `tests/board/test_hermes_executor.py`
- **test_blocked_handoff_surfaces_on_handle_not_board()** (3 connections) — `tests/board/test_hermes_executor.py`
- **test_cancel_confirmed()** (3 connections) — `tests/board/test_hermes_executor.py`
- **test_cancel_unreachable_does_not_claim_success()** (3 connections) — `tests/board/test_hermes_executor.py`
- **test_dispatch_upstream_error_is_failed_handle()** (3 connections) — `tests/board/test_hermes_executor.py`
- **test_inspect_reports_heartbeat()** (3 connections) — `tests/board/test_hermes_executor.py`
- **test_inspect_terminal_handle_is_noop()** (3 connections) — `tests/board/test_hermes_executor.py`
- **test_inspect_transient_failure_keeps_last_known()** (3 connections) — `tests/board/test_hermes_executor.py`
- **test_reconcile_declares_lost_when_unreachable()** (3 connections) — `tests/board/test_hermes_executor.py`
- **test_reconcile_recovers_running_run()** (3 connections) — `tests/board/test_hermes_executor.py`
- **test_dispatch_starts_run_and_fills_correlation()** (2 connections) — `tests/board/test_hermes_executor.py`
- **test_dispatch_unreachable_is_honest_failed_handle()** (2 connections) — `tests/board/test_hermes_executor.py`
- **Path** (1 connections)
- **Response** (1 connections)
- **HP-executor — concrete Hermes :class:`BoardExecutor` at the KB-5 seam.  Recorded** (1 connections) — `tests/board/test_hermes_executor.py`
- **Structural invariant: the executor has no board-store handle at all.** (1 connections) — `tests/board/test_hermes_executor.py`
- *... and 1 more nodes in this community*

## Relationships

- [get_executor](get_executor.md) (3 shared connections)
- [AttemptHandle](AttemptHandle.md) (3 shared connections)
- [_SpyStore](_SpyStore.md) (2 shared connections)
- [_HermesGateway](_HermesGateway.md) (1 shared connections)
- [HermesBoardExecutor](HermesBoardExecutor.md) (1 shared connections)
- [admin.py](admin.py.md) (1 shared connections)
- [test_board_dispatch.py](test_board_dispatch.py.md) (1 shared connections)

## Source Files

- `tests/board/test_hermes_executor.py`

## Audit Trail

- EXTRACTED: 126 (97%)
- INFERRED: 4 (3%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*