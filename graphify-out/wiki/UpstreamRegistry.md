# UpstreamRegistry

> 74 nodes

## Key Concepts

- **UpstreamRegistry** (94 connections) — `src/hal0/upstreams/registry.py`
- **test_registry.py** (39 connections) — `tests/upstreams/test_registry.py`
- **_slot()** (20 connections) — `tests/upstreams/test_registry.py`
- **_registry_with_openrouter()** (13 connections) — `tests/upstreams/test_registry.py`
- **_remote()** (12 connections) — `tests/upstreams/test_registry.py`
- **MonkeyPatch** (12 connections)
- **TestCreateRemovePersistent** (11 connections) — `tests/upstreams/test_registry.py`
- **_write_upstreams_toml()** (10 connections) — `tests/upstreams/test_registry.py`
- **UpstreamAlreadyExists** (8 connections) — `src/hal0/upstreams/registry.py`
- **TestApplyPersistentPatch** (8 connections) — `tests/upstreams/test_registry.py`
- **test_warmup_uses_override()** (7 connections) — `tests/upstreams/test_registry.py`
- **._entry()** (7 connections) — `tests/upstreams/test_registry.py`
- **test_warmup_backoff_jitter_within_25_percent()** (6 connections) — `tests/upstreams/test_registry.py`
- **test_warmup_total_grace_caps_at_180s()** (6 connections) — `tests/upstreams/test_registry.py`
- **test_warmup_strategy_none_just_probes()** (6 connections) — `tests/upstreams/test_registry.py`
- **test_warmup_backoff_step_sequence()** (5 connections) — `tests/upstreams/test_registry.py`
- **test_warmup_returns_true_when_healthy()** (5 connections) — `tests/upstreams/test_registry.py`
- **test_load_slot_overrides_from_hardware_json()** (5 connections) — `tests/upstreams/test_registry.py`
- **Path** (5 connections)
- **.test_create_duplicate_in_toml_only_raises()** (5 connections) — `tests/upstreams/test_registry.py`
- **.test_remove_protects_composite_and_slots()** (5 connections) — `tests/upstreams/test_registry.py`
- **test_add_duplicate_raises()** (4 connections) — `tests/upstreams/test_registry.py`
- **test_list_and_priority_order()** (4 connections) — `tests/upstreams/test_registry.py`
- **test_from_slot()** (4 connections) — `tests/upstreams/test_registry.py`
- **test_auth_bearer()** (4 connections) — `tests/upstreams/test_registry.py`
- *... and 49 more nodes in this community*

## Relationships

- [Upstream](Upstream.md) (40 shared connections)
- [Path](Path.md) (8 shared connections)
- [resolve_by_capability](resolve_by_capability.md) (6 shared connections)
- [lifespan](lifespan.md) (5 shared connections)
- [test_disabled_capability_slot_is_not_woken](test_disabled_capability_slot_is_not_woken.md) (4 shared connections)
- [ModelFilters](ModelFilters.md) (2 shared connections)
- [FakeUpstreamRegistry](FakeUpstreamRegistry.md) (2 shared connections)
- [test_upstream_dedup.py](test_upstream_dedup.py.md) (2 shared connections)
- [SingleFlightGroup](SingleFlightGroup.md) (1 shared connections)
- [Dispatcher](Dispatcher.md) (1 shared connections)

## Source Files

- `src/hal0/upstreams/registry.py`
- `tests/upstreams/test_registry.py`

## Audit Trail

- EXTRACTED: 402 (94%)
- INFERRED: 27 (6%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*