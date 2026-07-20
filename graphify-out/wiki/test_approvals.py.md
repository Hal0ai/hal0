# test_approvals.py

> 18 nodes · cohesion 0.21

## Key Concepts

- **test_approvals.py** (14 connections) — `tests/api/test_approvals.py`
- **TestClient** (8 connections)
- **FastAPI** (6 connections)
- **_build_app()** (4 connections) — `tests/api/test_approvals.py`
- **_noop_executor()** (4 connections) — `tests/api/test_approvals.py`
- **test_double_approve_returns_409()** (4 connections) — `tests/api/test_approvals.py`
- **test_list_pending_returns_enqueued_entries()** (4 connections) — `tests/api/test_approvals.py`
- **test_unavailable_queue_returns_503()** (4 connections) — `tests/api/test_approvals.py`
- **app()** (3 connections) — `tests/api/test_approvals.py`
- **client()** (3 connections) — `tests/api/test_approvals.py`
- **queue()** (3 connections) — `tests/api/test_approvals.py`
- **test_approve_runs_executor()** (3 connections) — `tests/api/test_approvals.py`
- **test_deny_does_not_run_executor()** (3 connections) — `tests/api/test_approvals.py`
- **test_approve_unknown_id_returns_404()** (2 connections) — `tests/api/test_approvals.py`
- **test_list_pending_empty_returns_empty_list()** (2 connections) — `tests/api/test_approvals.py`
- **Any** (1 connections)
- **Integration tests for the ``/api/agent/approvals`` REST surface.  The orchestrat** (1 connections) — `tests/api/test_approvals.py`
- **When app.state.approval_queue is absent, the dependency 503s.** (1 connections) — `tests/api/test_approvals.py`

## Relationships

- [ApprovalQueue](ApprovalQueue.md) (6 shared connections)

## Source Files

- `tests/api/test_approvals.py`

## Audit Trail

- EXTRACTED: 66 (94%)
- INFERRED: 4 (6%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*