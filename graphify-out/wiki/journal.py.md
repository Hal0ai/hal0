# journal.py

> 27 nodes · cohesion 0.14

## Key Concepts

- **journal.py** (11 connections) — `src/hal0/api/routes/journal.py`
- **_stream_iter()** (10 connections) — `src/hal0/api/routes/journal.py`
- **_collect()** (8 connections) — `src/hal0/api/routes/journal.py`
- **get_journal()** (8 connections) — `src/hal0/api/routes/journal.py`
- **stream_journal()** (8 connections) — `src/hal0/api/routes/journal.py`
- **JournalEntry** (7 connections) — `src/hal0/api/routes/journal.py`
- **_passes_filters()** (7 connections) — `src/hal0/api/routes/journal.py`
- **_bus()** (6 connections) — `src/hal0/api/routes/journal.py`
- **_hal0_event_to_entry()** (6 connections) — `src/hal0/api/routes/journal.py`
- **Request** (5 connections)
- **_sort_and_clamp()** (5 connections) — `src/hal0/api/routes/journal.py`
- **LevelFilter** (4 connections)
- **Any** (3 connections)
- **_source_matches()** (3 connections) — `src/hal0/api/routes/journal.py`
- **BaseModel** (1 connections)
- **StreamingResponse** (1 connections)
- **Unified journal endpoints over hal0 events.  Issue #323 (epic #322 — Phase 1 of** (1 connections) — `src/hal0/api/routes/journal.py`
- **Return the EventBus on app.state, or ``None`` when absent.** (1 connections) — `src/hal0/api/routes/journal.py`
- **True when ``entry_source`` passes the ``?source=`` filter.      ``merged``/``all** (1 connections) — `src/hal0/api/routes/journal.py`
- **Apply source + slot + level (exact) + q (substring on ``msg``).** (1 connections) — `src/hal0/api/routes/journal.py`
- **Pull raw entries from the event bus, no filter applied.** (1 connections) — `src/hal0/api/routes/journal.py`
- **Sort entries by ``ts`` ascending, keep the newest ``limit``.** (1 connections) — `src/hal0/api/routes/journal.py`
- **Return a page of journal entries with a cursor for the next call.      ``next_si** (1 connections) — `src/hal0/api/routes/journal.py`
- **Async generator producing SSE frames for ``/api/journal/stream``.      1. Subscr** (1 connections) — `src/hal0/api/routes/journal.py`
- **SSE live tail of the journal.      Replays the last ~50 filtered entries synchro** (1 connections) — `src/hal0/api/routes/journal.py`
- *... and 2 more nodes in this community*

## Relationships

- [test_journal_routes.py](test_journal_routes.py.md) (3 shared connections)
- [EventBus](EventBus.md) (1 shared connections)

## Source Files

- `src/hal0/api/routes/journal.py`

## Audit Trail

- EXTRACTED: 101 (97%)
- INFERRED: 3 (3%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*