# test_journal_routes.py

> 30 nodes · cohesion 0.13

## Key Concepts

- **test_journal_routes.py** (16 connections) — `tests/api/test_journal_routes.py`
- **_clear_bootstrap_events()** (11 connections) — `tests/api/test_journal_routes.py`
- **TestClient** (10 connections)
- **_bus()** (9 connections) — `tests/api/test_journal_routes.py`
- **test_journal_stream_replay_includes_prior_entries()** (6 connections) — `tests/api/test_journal_routes.py`
- **test_journal_get_since_cursor_pagination()** (5 connections) — `tests/api/test_journal_routes.py`
- **test_journal_get_source_aliases_return_full_stream()** (5 connections) — `tests/api/test_journal_routes.py`
- **test_journal_get_source_filter_narrows()** (5 connections) — `tests/api/test_journal_routes.py`
- **test_journal_get_with_hal0_event_returns_it()** (5 connections) — `tests/api/test_journal_routes.py`
- **_parse_sse_frames()** (4 connections) — `tests/api/test_journal_routes.py`
- **FastAPI** (4 connections)
- **test_journal_get_empty_returns_empty_list()** (4 connections) — `tests/api/test_journal_routes.py`
- **test_journal_get_level_filter()** (4 connections) — `tests/api/test_journal_routes.py`
- **test_journal_get_q_filter_substring()** (4 connections) — `tests/api/test_journal_routes.py`
- **test_journal_get_unknown_source_matches_nothing()** (4 connections) — `tests/api/test_journal_routes.py`
- **test_journal_stream_handshake_returns_sse_content_type()** (4 connections) — `tests/api/test_journal_routes.py`
- **test_journal_stream_yields_event_on_emit()** (4 connections) — `tests/api/test_journal_routes.py`
- **Any** (1 connections)
- **Tests for the unified ``/api/journal`` + ``/api/journal/stream`` routes.  The jo** (1 connections) — `tests/api/test_journal_routes.py`
- **``hal0`` / ``merged`` / ``all`` all mean "no source filter".** (1 connections) — `tests/api/test_journal_routes.py`
- **A real ``?source=`` narrows: exact or ``:``-prefix; ``slot`` matches ``slot:*``.** (1 connections) — `tests/api/test_journal_routes.py`
- **An unknown source is a valid filter that simply matches no events.** (1 connections) — `tests/api/test_journal_routes.py`
- **``since`` is an id cursor — second page sees only newer ids.** (1 connections) — `tests/api/test_journal_routes.py`
- **The stream surface advertises the correct content-type + path resolves.      Dri** (1 connections) — `tests/api/test_journal_routes.py`
- **Subscribe to the SSE stream, emit a hal0 event, expect a frame.      Drives ``st** (1 connections) — `tests/api/test_journal_routes.py`
- *... and 5 more nodes in this community*

## Relationships

- [journal.py](journal.py.md) (3 shared connections)
- [EventBus](EventBus.md) (1 shared connections)
- [RequestSeam](RequestSeam.md) (1 shared connections)

## Source Files

- `tests/api/test_journal_routes.py`

## Audit Trail

- EXTRACTED: 113 (97%)
- INFERRED: 4 (3%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*