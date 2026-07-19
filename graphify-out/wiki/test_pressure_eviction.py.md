# test_pressure_eviction.py

> 28 nodes · cohesion 0.20

## Key Concepts

- **test_pressure_eviction.py** (14 connections) — `tests/slots/test_pressure_eviction.py`
- **_write_slot()** (12 connections) — `tests/slots/test_pressure_eviction.py`
- **_patch_free_mb()** (10 connections) — `tests/slots/test_pressure_eviction.py`
- **MonkeyPatch** (10 connections)
- **Path** (10 connections)
- **SlotManager** (10 connections)
- **FakeContainerProvider** (9 connections)
- **test_pressure_evict_disabled_when_floor_is_zero()** (8 connections) — `tests/slots/test_pressure_eviction.py`
- **test_pressure_evict_never_evicts_agent()** (8 connections) — `tests/slots/test_pressure_eviction.py`
- **test_pressure_evict_noop_when_above_floor()** (8 connections) — `tests/slots/test_pressure_eviction.py`
- **test_pressure_evict_skips_non_lru_slot()** (8 connections) — `tests/slots/test_pressure_eviction.py`
- **test_pressure_evict_skips_serving_slot()** (8 connections) — `tests/slots/test_pressure_eviction.py`
- **test_pressure_evict_unloads_lru_slot_under_floor()** (8 connections) — `tests/slots/test_pressure_eviction.py`
- **test_pressure_evict_lru_order()** (7 connections) — `tests/slots/test_pressure_eviction.py`
- **test_pressure_evict_noop_when_probe_fails()** (7 connections) — `tests/slots/test_pressure_eviction.py`
- **test_pressure_evict_stops_when_floor_met()** (7 connections) — `tests/slots/test_pressure_eviction.py`
- **Tests for host-memory-pressure LRU eviction (#903).  Covers _pressure_evict_once** (1 connections) — `tests/slots/test_pressure_eviction.py`
- **A slot without lru=true in its TOML is never evicted under pressure.** (1 connections) — `tests/slots/test_pressure_eviction.py`
- **The canonical ``agent`` slot is never evicted under pressure (#903).** (1 connections) — `tests/slots/test_pressure_eviction.py`
- **A slot mid-request is never evicted even when free RAM is critically low.** (1 connections) — `tests/slots/test_pressure_eviction.py`
- **When free RAM < floor, an idle lru-eligible slot is unloaded.** (1 connections) — `tests/slots/test_pressure_eviction.py`
- **A failed host-RAM probe is fail-SAFE: it reports ``inf`` free RAM so the     flo** (1 connections) — `tests/slots/test_pressure_eviction.py`
- **When multiple lru-eligible slots exist, oldest last_used is evicted first.** (1 connections) — `tests/slots/test_pressure_eviction.py`
- **Eviction stops as soon as free RAM recovers above the floor.      Three lru-elig** (1 connections) — `tests/slots/test_pressure_eviction.py`
- **Write a minimal slot TOML, optionally marking it lru-eligible.** (1 connections) — `tests/slots/test_pressure_eviction.py`
- *... and 3 more nodes in this community*

## Relationships

- [conftest.py](conftest.py.md) (1 shared connections)
- [FakeContainerProvider](FakeContainerProvider.md) (1 shared connections)

## Source Files

- `tests/slots/test_pressure_eviction.py`

## Audit Trail

- EXTRACTED: 156 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*