# approvals.py

> 20 nodes

## Key Concepts

- **approvals.py** (9 connections) — `src/hal0/api/routes/approvals.py`
- **_queue()** (8 connections) — `src/hal0/api/routes/approvals.py`
- **approve_approval()** (8 connections) — `src/hal0/api/routes/approvals.py`
- **deny_approval()** (8 connections) — `src/hal0/api/routes/approvals.py`
- **ApprovalNotFound** (6 connections) — `src/hal0/api/routes/approvals.py`
- **ApprovalAlreadyResolved** (6 connections) — `src/hal0/api/routes/approvals.py`
- **ApprovalQueueUnavailable** (5 connections) — `src/hal0/api/routes/approvals.py`
- **Request** (5 connections)
- **list_pending()** (5 connections) — `src/hal0/api/routes/approvals.py`
- **approval_events()** (5 connections) — `src/hal0/api/routes/approvals.py`
- **Any** (3 connections)
- **StreamingResponse** (1 connections)
- **Approval inbox REST routes (mounted under ``/api/agent/approvals``).  The hal0 a** (1 connections) — `src/hal0/api/routes/approvals.py`
- **The ApprovalQueue is not initialised on app.state.      The orchestrator wires `** (1 connections) — `src/hal0/api/routes/approvals.py`
- **The requested approval id is not in the queue.** (1 connections) — `src/hal0/api/routes/approvals.py`
- **The approval has already been approved/denied.** (1 connections) — `src/hal0/api/routes/approvals.py`
- **Return every entry still in the ``pending`` state.      The dashboard's bell bad** (1 connections) — `src/hal0/api/routes/approvals.py`
- **Approve one pending entry; the queue runs the bound executor.      Returns the e** (1 connections) — `src/hal0/api/routes/approvals.py`
- **Deny one pending entry; no executor runs.** (1 connections) — `src/hal0/api/routes/approvals.py`
- **SSE stream: backfill pending entries then live-tail queue events.      Pattern m** (1 connections) — `src/hal0/api/routes/approvals.py`

## Relationships

- [ApprovalQueue](ApprovalQueue.md) (4 shared connections)
- [Hal0Error](Hal0Error.md) (3 shared connections)
- [record_action](record_action.md) (2 shared connections)

## Source Files

- `src/hal0/api/routes/approvals.py`

## Audit Trail

- EXTRACTED: 72 (94%)
- INFERRED: 5 (6%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*