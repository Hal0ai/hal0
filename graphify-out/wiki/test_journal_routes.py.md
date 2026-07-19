# test_journal_routes.py

> 33 nodes

## Key Concepts

- **test_journal_routes.py** (16 connections) — `tests/api/test_journal_routes.py`
- **_clear_bootstrap_events()** (11 connections) — `tests/api/test_journal_routes.py`
- **TestClient** (10 connections)
- **_bus()** (9 connections) — `tests/api/test_journal_routes.py`
- **stream_journal()** (8 connections) — `src/hal0/api/routes/journal.py`
- **test_journal_stream_replay_includes_prior_entries()** (6 connections) — `tests/api/test_journal_routes.py`
- **test_journal_get_with_hal0_event_returns_it()** (5 connections) — `tests/api/test_journal_routes.py`
- **test_journal_get_source_aliases_return_full_stream()** (5 connections) — `tests/api/test_journal_routes.py`
- **test_journal_get_source_filter_narrows()** (5 connections) — `tests/api/test_journal_routes.py`
- **test_journal_get_since_cursor_pagination()** (5 connections) — `tests/api/test_journal_routes.py`
- **_parse_sse_frames()** (4 connections) — `tests/api/test_journal_routes.py`
- **test_journal_get_empty_returns_empty_list()** (4 connections) — `tests/api/test_journal_routes.py`
- **test_journal_get_unknown_source_matches_nothing()** (4 connections) — `tests/api/test_journal_routes.py`
- **test_journal_get_level_filter()** (4 connections) — `tests/api/test_journal_routes.py`
- **test_journal_get_q_filter_substring()** (4 connections) — `tests/api/test_journal_routes.py`
- **test_journal_stream_handshake_returns_sse_content_type()** (4 connections) — `tests/api/test_journal_routes.py`
- **FastAPI** (4 connections)
- **test_journal_stream_yields_event_on_emit()** (4 connections) — `tests/api/test_journal_routes.py`
- **StreamingResponse** (1 connections)
- **SSE live tail of the journal.      Replays the last ~50 filtered entries synchro** (1 connections) — `src/hal0/api/routes/journal.py`
- **Any** (1 connections)
- **Tests for the unified ``/api/journal`` + ``/api/journal/stream`` routes.  The jo** (1 connections) — `tests/api/test_journal_routes.py`
- **Drop the lifespan's ``system.restart`` event from the EventBus ring.      Withou** (1 connections) — `tests/api/test_journal_routes.py`
- **Pull JSON payloads out of an SSE response body.** (1 connections) — `tests/api/test_journal_routes.py`
- **No events → empty list + null cursor.** (1 connections) — `tests/api/test_journal_routes.py`
- *... and 8 more nodes in this community*

## Relationships

- [journal.py](journal.py.md) (3 shared connections)
- [EventBus](EventBus.md) (1 shared connections)
- [RequestSeam](RequestSeam.md) (1 shared connections)

## Source Files

- `src/hal0/api/routes/journal.py`
- `tests/api/test_journal_routes.py`

## Audit Trail

- EXTRACTED: 120 (94%)
- INFERRED: 7 (6%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*