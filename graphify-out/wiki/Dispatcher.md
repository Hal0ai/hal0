# Dispatcher

> 99 nodes · cohesion 0.05

## Key Concepts

- **Dispatcher** (90 connections) — `src/hal0/dispatcher/router.py`
- **FakeUpstreamRegistry** (34 connections) — `tests/dispatcher/test_router.py`
- **FakeModelRegistry** (31 connections) — `tests/dispatcher/test_router.py`
- **test_router.py** (30 connections) — `tests/dispatcher/test_router.py`
- **LegacyResolutionFailed** (23 connections) — `src/hal0/dispatcher/_capability_resolve.py`
- **NoRouteFound** (23 connections) — `src/hal0/dispatcher/router.py`
- **make_request()** (22 connections) — `tests/dispatcher/test_router.py`
- **.dispatch()** (19 connections) — `src/hal0/dispatcher/router.py`
- **_FakeSlotManager** (18 connections) — `tests/dispatcher/test_router.py`
- **DispatchError** (15 connections) — `src/hal0/dispatcher/router.py`
- **make_slot()** (12 connections) — `tests/dispatcher/test_router.py`
- **RegistryLoadFailed** (11 connections) — `src/hal0/dispatcher/router.py`
- **UnknownUpstream** (11 connections) — `src/hal0/dispatcher/router.py`
- **make_remote()** (10 connections) — `tests/dispatcher/test_router.py`
- **test_registry_binding_to_disabled_upstream_falls_through()** (10 connections) — `tests/dispatcher/test_router.py`
- **test_cold_cache_prefetch_populates_then_routes()** (9 connections) — `tests/dispatcher/test_router.py`
- **test_container_slot_preempts_stale_registry_binding()** (9 connections) — `tests/dispatcher/test_router.py`
- **test_disabled_remote_excluded_from_cold_prefetch()** (9 connections) — `tests/dispatcher/test_router.py`
- **test_disabled_remote_skipped_on_warm_cache()** (9 connections) — `tests/dispatcher/test_router.py`
- **test_passthrough_when_upstream_cache_has_model()** (9 connections) — `tests/dispatcher/test_router.py`
- **.get()** (8 connections) — `tests/dispatcher/test_router.py`
- **test_legacy_fallback_with_no_primary_raises_typed_no_route()** (8 connections) — `tests/dispatcher/test_router.py`
- **test_prefetch_respects_configurable_timeout()** (8 connections) — `tests/dispatcher/test_router.py`
- **test_prefetch_respects_parallel_cap()** (8 connections) — `tests/dispatcher/test_router.py`
- **test_registry_load_failure_raises_typed_error()** (8 connections) — `tests/dispatcher/test_router.py`
- *... and 74 more nodes in this community*

## Relationships

- [Upstream](Upstream.md) (27 shared connections)
- [UpstreamCall](UpstreamCall.md) (21 shared connections)
- [SlotState](SlotState.md) (17 shared connections)
- [FakeUpstreamRegistry](FakeUpstreamRegistry.md) (10 shared connections)
- [router.py](router.py.md) (10 shared connections)
- [SlotLoading](SlotLoading.md) (9 shared connections)
- [SingleFlightGroup](SingleFlightGroup.md) (6 shared connections)
- [test_disabled_capability_slot_is_not_woken](test_disabled_capability_slot_is_not_woken.md) (6 shared connections)
- [_ArbiterSlotManager](_ArbiterSlotManager.md) (4 shared connections)
- [test_pool_bounds.py](test_pool_bounds.py.md) (4 shared connections)
- [GpuImageMode](GpuImageMode.md) (3 shared connections)
- [test_forward.py](test_forward.py.md) (2 shared connections)

## Source Files

- `src/hal0/dispatcher/_capability_resolve.py`
- `src/hal0/dispatcher/router.py`
- `tests/dispatcher/test_router.py`
- `tests/fixtures/hermes/contracts/memory_provider.py`

## Audit Trail

- EXTRACTED: 439 (71%)
- INFERRED: 182 (29%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*