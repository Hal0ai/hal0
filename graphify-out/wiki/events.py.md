# events.py

> 17 nodes · cohesion 0.18

## Key Concepts

- **events.py** (7 connections) — `src/hal0/api/routes/events.py`
- **_bus()** (6 connections) — `src/hal0/api/routes/events.py`
- **list_events()** (6 connections) — `src/hal0/api/routes/events.py`
- **stream_events()** (6 connections) — `src/hal0/api/routes/events.py`
- **_normalise_severity()** (5 connections) — `src/hal0/api/routes/events.py`
- **EventsInvalidQuery** (4 connections) — `src/hal0/api/routes/events.py`
- **EventsUnavailable** (4 connections) — `src/hal0/api/routes/events.py`
- **Request** (3 connections)
- **Hal0Error** (2 connections)
- **Any** (1 connections)
- **StreamingResponse** (1 connections)
- **Dashboard footer event surface — backfill + live SSE stream.  Mounted under ``/a** (1 connections) — `src/hal0/api/routes/events.py`
- **Return a page of events with a cursor for the next call.      ``next_since`` is** (1 connections) — `src/hal0/api/routes/events.py`
- **Server-Sent Events stream: backfill then live tail.      ``type`` (fnmatch glob,** (1 connections) — `src/hal0/api/routes/events.py`
- **The event bus has not been initialised on app.state.      Raised when a test or** (1 connections) — `src/hal0/api/routes/events.py`
- **Caller supplied an unsupported query-param value (e.g. unknown severity).** (1 connections) — `src/hal0/api/routes/events.py`
- **Reject unknown severities up-front so callers see a 400 not a silent skip.** (1 connections) — `src/hal0/api/routes/events.py`

## Relationships

- [EventBus](EventBus.md) (1 shared connections)

## Source Files

- `src/hal0/api/routes/events.py`

## Audit Trail

- EXTRACTED: 51 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*