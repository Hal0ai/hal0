# EventBus

> 31 nodes

## Key Concepts

- **EventBus** (26 connections) — `src/hal0/events/__init__.py`
- **test_events.py** (20 connections) — `tests/api/test_events.py`
- **FastAPI** (5 connections)
- **TestClient** (5 connections)
- **_build_offline_deps()** (4 connections) — `src/hal0/cli/setup_command.py`
- **test_overflow_surfaces_one_coalesced_gap_when_consumer_catches_up()** (3 connections) — `tests/api/test_events.py`
- **app()** (3 connections) — `tests/api/test_events.py`
- **client()** (3 connections) — `tests/api/test_events.py`
- **test_get_events_cursor_paginates()** (3 connections) — `tests/api/test_events.py`
- **test_get_events_type_glob()** (3 connections) — `tests/api/test_events.py`
- **_parse_sse_frames()** (3 connections) — `tests/api/test_events.py`
- **test_stream_replay_then_live()** (3 connections) — `tests/api/test_events.py`
- **test_stream_since_skips_backfill()** (3 connections) — `tests/api/test_events.py`
- **test_emit_appends_to_ring_and_assigns_monotonic_ids()** (2 connections) — `tests/api/test_events.py`
- **test_ring_buffer_evicts_oldest_when_maxlen_exceeded()** (2 connections) — `tests/api/test_events.py`
- **test_backfill_since_cursor()** (2 connections) — `tests/api/test_events.py`
- **test_backfill_type_glob_filter()** (2 connections) — `tests/api/test_events.py`
- **test_backfill_min_severity_filter()** (2 connections) — `tests/api/test_events.py`
- **test_backfill_limit_keeps_most_recent()** (2 connections) — `tests/api/test_events.py`
- **test_subscribe_yields_emitted_events()** (2 connections) — `tests/api/test_events.py`
- **test_subscriber_full_queue_drops_oldest_not_raises()** (2 connections) — `tests/api/test_events.py`
- **test_get_events_returns_envelope_with_next_since()** (2 connections) — `tests/api/test_events.py`
- **test_get_events_rejects_bad_severity()** (2 connections) — `tests/api/test_events.py`
- **Construct a SlotManager + model registry WITHOUT a running API, mirroring     ho** (1 connections) — `src/hal0/cli/setup_command.py`
- **Fan-out event bus with a bounded ring buffer.      Thread-safety: all methods ar** (1 connections) — `src/hal0/events/__init__.py`
- *... and 6 more nodes in this community*

## Relationships

- [.emit](emit.md) (6 shared connections)
- [FakeUpstreamRegistry](FakeUpstreamRegistry.md) (4 shared connections)
- [create_app](create_app.md) (3 shared connections)
- [lifespan](lifespan.md) (2 shared connections)
- [build_auto_selections](build_auto_selections.md) (1 shared connections)
- [SlotManager](SlotManager.md) (1 shared connections)
- [events.py](events.py.md) (1 shared connections)
- [journal.py](journal.py.md) (1 shared connections)
- [test_journal_routes.py](test_journal_routes.py.md) (1 shared connections)

## Source Files

- `src/hal0/cli/setup_command.py`
- `src/hal0/events/__init__.py`
- `tests/api/test_events.py`

## Audit Trail

- EXTRACTED: 84 (75%)
- INFERRED: 28 (25%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*