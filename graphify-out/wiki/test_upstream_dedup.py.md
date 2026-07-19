# test_upstream_dedup.py

> 27 nodes

## Key Concepts

- **test_upstream_dedup.py** (13 connections) — `tests/api/test_upstream_dedup.py`
- **_FakeSlotManager** (11 connections) — `tests/api/test_upstream_dedup.py`
- **test_priming_is_a_noop_when_operator_defines_a_real_hal0_upstream()** (7 connections) — `tests/api/test_upstream_dedup.py`
- **_two_chat_slots()** (6 connections) — `tests/api/test_upstream_dedup.py`
- **test_no_pseudo_upstream_is_registered_in_the_routing_table()** (6 connections) — `tests/api/test_upstream_dedup.py`
- **test_composite_fetch_aggregates_chat_slot_models()** (5 connections) — `tests/api/test_upstream_dedup.py`
- **test_composite_fetch_caches_for_ttl()** (4 connections) — `tests/api/test_upstream_dedup.py`
- **test_composite_fetch_handles_empty_catalog()** (4 connections) — `tests/api/test_upstream_dedup.py`
- **test_composite_fetch_excludes_slots_without_model_id()** (4 connections) — `tests/api/test_upstream_dedup.py`
- **test_composite_fetch_reads_nested_model_default_from_toml()** (4 connections) — `tests/api/test_upstream_dedup.py`
- **Any** (3 connections)
- **_reset_module_cache()** (3 connections) — `tests/api/test_upstream_dedup.py`
- **test_module_cache_clear_is_callable()** (3 connections) — `tests/api/test_upstream_dedup.py`
- **.__init__()** (2 connections) — `tests/api/test_upstream_dedup.py`
- **.iter_configs()** (2 connections) — `tests/api/test_upstream_dedup.py`
- **R4 H2 regression + P2-composite rebuild — direct-read model catalogue.  The bug:** (1 connections) — `tests/api/test_upstream_dedup.py`
- **Minimal stub returning a hand-rolled slot catalogue.      Mirrors the parts of :** (1 connections) — `tests/api/test_upstream_dedup.py`
- **Two chat-capable slots sharing one port (mirrors the historical     bug at ``por** (1 connections) — `tests/api/test_upstream_dedup.py`
- **Punch the module-level TTL cache between tests.** (1 connections) — `tests/api/test_upstream_dedup.py`
- **Priming the composite catalogue never adds an ``Upstream`` to the     registry —** (1 connections) — `tests/api/test_upstream_dedup.py`
- **If ``hal0`` is already registered (operator override via     upstreams.toml) pri** (1 connections) — `tests/api/test_upstream_dedup.py`
- **``_fetch_hal0_composite_models`` returns the deduped union of     every chat-cap** (1 connections) — `tests/api/test_upstream_dedup.py`
- **Within the TTL window, ``_fetch_hal0_composite_models`` returns     the cached l** (1 connections) — `tests/api/test_upstream_dedup.py`
- **No catastrophic failure when ``iter_configs`` returns nothing     (cold start be** (1 connections) — `tests/api/test_upstream_dedup.py`
- **The cache-punch helper is exposed so slot swap/restart paths can     invalidate** (1 connections) — `tests/api/test_upstream_dedup.py`
- *... and 2 more nodes in this community*

## Relationships

- [lifespan](lifespan.md) (10 shared connections)
- [UpstreamRegistry](UpstreamRegistry.md) (2 shared connections)
- [Upstream](Upstream.md) (1 shared connections)

## Source Files

- `tests/api/test_upstream_dedup.py`

## Audit Trail

- EXTRACTED: 77 (87%)
- INFERRED: 12 (13%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*