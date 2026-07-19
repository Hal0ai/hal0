# journal.py

> 23 nodes

## Key Concepts

- **journal.py** (11 connections) — `src/hal0/api/routes/journal.py`
- **_stream_iter()** (10 connections) — `src/hal0/api/routes/journal.py`
- **_collect()** (8 connections) — `src/hal0/api/routes/journal.py`
- **get_journal()** (8 connections) — `src/hal0/api/routes/journal.py`
- **JournalEntry** (7 connections) — `src/hal0/api/routes/journal.py`
- **_passes_filters()** (7 connections) — `src/hal0/api/routes/journal.py`
- **_hal0_event_to_entry()** (6 connections) — `src/hal0/api/routes/journal.py`
- **_bus()** (6 connections) — `src/hal0/api/routes/journal.py`
- **Request** (5 connections)
- **_sort_and_clamp()** (5 connections) — `src/hal0/api/routes/journal.py`
- **LevelFilter** (4 connections)
- **Any** (3 connections)
- **_source_matches()** (3 connections) — `src/hal0/api/routes/journal.py`
- **Unified journal endpoints over hal0 events.  Issue #323 (epic #322 — Phase 1 of** (1 connections) — `src/hal0/api/routes/journal.py`
- **Unified journal row served by ``/api/journal``.      Distinct from the raw event** (1 connections) — `src/hal0/api/routes/journal.py`
- **Project an EventBus event onto the unified ``JournalEntry`` shape.      Severity** (1 connections) — `src/hal0/api/routes/journal.py`
- **Return the EventBus on app.state, or ``None`` when absent.** (1 connections) — `src/hal0/api/routes/journal.py`
- **True when ``entry_source`` passes the ``?source=`` filter.      ``merged``/``all** (1 connections) — `src/hal0/api/routes/journal.py`
- **Apply source + slot + level (exact) + q (substring on ``msg``).** (1 connections) — `src/hal0/api/routes/journal.py`
- **Pull raw entries from the event bus, no filter applied.** (1 connections) — `src/hal0/api/routes/journal.py`
- **Sort entries by ``ts`` ascending, keep the newest ``limit``.** (1 connections) — `src/hal0/api/routes/journal.py`
- **Return a page of journal entries with a cursor for the next call.      ``next_si** (1 connections) — `src/hal0/api/routes/journal.py`
- **Async generator producing SSE frames for ``/api/journal/stream``.      1. Subscr** (1 connections) — `src/hal0/api/routes/journal.py`

## Relationships

- [test_journal_routes.py](test_journal_routes.py.md) (3 shared connections)
- [BaseModel](BaseModel.md) (1 shared connections)
- [EventBus](EventBus.md) (1 shared connections)

## Source Files

- `src/hal0/api/routes/journal.py`

## Audit Trail

- EXTRACTED: 93 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*