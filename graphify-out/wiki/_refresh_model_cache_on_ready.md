# _refresh_model_cache_on_ready

> 20 nodes · cohesion 0.17

## Key Concepts

- **_refresh_model_cache_on_ready()** (13 connections) — `src/hal0/api/__init__.py`
- **FakeUpstreamRegistry** (9 connections) — `tests/api/test_model_cache_refresh.py`
- **test_model_cache_refresh.py** (8 connections) — `tests/api/test_model_cache_refresh.py`
- **FakeSlotManager** (7 connections) — `tests/api/test_model_cache_refresh.py`
- **test_fetch_exception_does_not_kill_refresher()** (7 connections) — `tests/api/test_model_cache_refresh.py`
- **test_non_ready_transitions_do_not_refresh()** (7 connections) — `tests/api/test_model_cache_refresh.py`
- **test_ready_event_refreshes_stale_cache_for_that_slot()** (7 connections) — `tests/api/test_model_cache_refresh.py`
- **test_event_for_unregistered_slot_is_ignored()** (6 connections) — `tests/api/test_model_cache_refresh.py`
- **_make_slot()** (5 connections) — `tests/api/test_model_cache_refresh.py`
- **.get()** (2 connections) — `tests/api/test_model_cache_refresh.py`
- **.__init__()** (2 connections) — `tests/api/test_model_cache_refresh.py`
- **.list()** (2 connections) — `tests/api/test_model_cache_refresh.py`
- **Re-fetch ``model_cache[slot]`` whenever a slot transitions to ready.      The ca** (1 connections) — `src/hal0/api/__init__.py`
- **.iter_configs()** (1 connections) — `tests/api/test_model_cache_refresh.py`
- **Regression test for the stale-model_cache slot-routing bug.  A slot's GGUF can c** (1 connections) — `tests/api/test_model_cache_refresh.py`
- **Only the ready edge should trigger a re-fetch — starting/idle/error must not.** (1 connections) — `tests/api/test_model_cache_refresh.py`
- **A slot.state for a slot with no upstream entry must not crash the task.** (1 connections) — `tests/api/test_model_cache_refresh.py`
- **A transient /v1/models fetch failure must not stop future refreshes.** (1 connections) — `tests/api/test_model_cache_refresh.py`
- **Minimal stand-in for the ``slot_manager`` arg added when the composite     ``mod** (1 connections) — `tests/api/test_model_cache_refresh.py`
- **slot.state ready → ``fetch_and_cache`` runs against that slot's upstream.** (1 connections) — `tests/api/test_model_cache_refresh.py`

## Relationships

- [lifespan](lifespan.md) (5 shared connections)
- [EventBus](EventBus.md) (5 shared connections)
- [Upstream](Upstream.md) (5 shared connections)
- [UpstreamRegistry](UpstreamRegistry.md) (2 shared connections)

## Source Files

- `src/hal0/api/__init__.py`
- `tests/api/test_model_cache_refresh.py`

## Audit Trail

- EXTRACTED: 71 (86%)
- INFERRED: 12 (14%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*