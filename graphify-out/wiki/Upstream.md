# Upstream

> 78 nodes

## Key Concepts

- **Upstream** (81 connections) — `src/hal0/upstreams/registry.py`
- **.get()** (18 connections) — `src/hal0/upstreams/registry.py`
- **registry.py** (14 connections) — `src/hal0/upstreams/registry.py`
- **.apply_persistent_patch()** (12 connections) — `src/hal0/upstreams/registry.py`
- **UpstreamNotFound** (11 connections) — `src/hal0/upstreams/registry.py`
- **.create_persistent()** (10 connections) — `src/hal0/upstreams/registry.py`
- **_filters_to_runtime()** (9 connections) — `src/hal0/upstreams/registry.py`
- **UpstreamProtected** (8 connections) — `src/hal0/upstreams/registry.py`
- **upstream_from_entry()** (8 connections) — `src/hal0/upstreams/registry.py`
- **.remove_persistent()** (8 connections) — `src/hal0/upstreams/registry.py`
- **.test()** (8 connections) — `src/hal0/upstreams/registry.py`
- **UpstreamError** (7 connections) — `src/hal0/upstreams/registry.py`
- **Any** (7 connections)
- **.update()** (7 connections) — `src/hal0/upstreams/registry.py`
- **.warmup()** (7 connections) — `src/hal0/upstreams/registry.py`
- **.list()** (6 connections) — `src/hal0/upstreams/registry.py`
- **._effective_backoff_steps()** (6 connections) — `src/hal0/upstreams/registry.py`
- **.auth_headers()** (6 connections) — `src/hal0/upstreams/registry.py`
- **.health()** (6 connections) — `src/hal0/upstreams/registry.py`
- **.fetch_models()** (6 connections) — `src/hal0/upstreams/registry.py`
- **_filters_to_config()** (5 connections) — `src/hal0/upstreams/registry.py`
- **.add()** (5 connections) — `src/hal0/upstreams/registry.py`
- **._effective_total_grace_s()** (5 connections) — `src/hal0/upstreams/registry.py`
- **._get_client()** (5 connections) — `src/hal0/upstreams/registry.py`
- **_fields_for_entry()** (4 connections) — `src/hal0/upstreams/registry.py`
- *... and 53 more nodes in this community*

## Relationships

- [UpstreamRegistry](UpstreamRegistry.md) (40 shared connections)
- [resolve_by_capability](resolve_by_capability.md) (16 shared connections)
- [FakeUpstreamRegistry](FakeUpstreamRegistry.md) (9 shared connections)
- [ModelFilters](ModelFilters.md) (7 shared connections)
- [UpstreamCall](UpstreamCall.md) (7 shared connections)
- [Dispatcher](Dispatcher.md) (7 shared connections)
- [ConfigParseError](ConfigParseError.md) (6 shared connections)
- [SlotLoading](SlotLoading.md) (3 shared connections)
- [test_v1_audio.py](test_v1_audio.py.md) (3 shared connections)
- [lifespan](lifespan.md) (2 shared connections)
- [test_models_routes.py](test_models_routes.py.md) (2 shared connections)
- [FastAPI](FastAPI.md) (2 shared connections)

## Source Files

- `src/hal0/upstreams/registry.py`
- `tests/dispatcher/test_rerank_path_routing.py`
- `tests/dispatcher/test_router.py`
- `tests/dispatcher/test_tts_path_routing.py`
- `tests/slots/test_slot_aliases.py`
- `tests/upstreams/test_registry.py`

## Audit Trail

- EXTRACTED: 315 (87%)
- INFERRED: 48 (13%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*