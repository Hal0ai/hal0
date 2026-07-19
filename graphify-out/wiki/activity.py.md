# activity.py

> 18 nodes

## Key Concepts

- **activity.py** (10 connections) — `src/hal0/api/routes/activity.py`
- **_store()** (7 connections) — `src/hal0/api/routes/activity.py`
- **list_activity()** (7 connections) — `src/hal0/api/routes/activity.py`
- **export_activity()** (6 connections) — `src/hal0/api/routes/activity.py`
- **stream_activity()** (6 connections) — `src/hal0/api/routes/activity.py`
- **ActivityInvalidQuery** (5 connections) — `src/hal0/api/routes/activity.py`
- **Request** (5 connections)
- **_validate()** (5 connections) — `src/hal0/api/routes/activity.py`
- **ActivityUnavailable** (4 connections) — `src/hal0/api/routes/activity.py`
- **_epoch()** (4 connections) — `src/hal0/api/routes/activity.py`
- **audit()** (4 connections) — `tests/board/test_board_routes.py`
- **_row_to_dict()** (3 connections) — `src/hal0/api/routes/activity.py`
- **Any** (2 connections)
- **Response** (1 connections)
- **StreamingResponse** (1 connections)
- **Durable activity / audit surface — mounted under ``/api/activity``.  Reads the S** (1 connections) — `src/hal0/api/routes/activity.py`
- **The audit store was not initialised on app.state (odd entrypoint).** (1 connections) — `src/hal0/api/routes/activity.py`
- **Caller supplied an unsupported filter value (e.g. unknown severity).** (1 connections) — `src/hal0/api/routes/activity.py`

## Relationships

- [Hal0Error](Hal0Error.md) (2 shared connections)
- [AuditStore](AuditStore.md) (1 shared connections)
- [record_action](record_action.md) (1 shared connections)
- [test_board_routes.py](test_board_routes.py.md) (1 shared connections)

## Source Files

- `src/hal0/api/routes/activity.py`
- `tests/board/test_board_routes.py`

## Audit Trail

- EXTRACTED: 70 (96%)
- INFERRED: 3 (4%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*