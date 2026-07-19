# test_adopted_slot_eviction.py

> 14 nodes · cohesion 0.23

## Key Concepts

- **test_adopted_slot_eviction.py** (8 connections) — `tests/slots/test_adopted_slot_eviction.py`
- **FakeContainerProvider** (5 connections)
- **Path** (5 connections)
- **test_adoption_bumps_last_used()** (5 connections) — `tests/slots/test_adopted_slot_eviction.py`
- **test_adoption_records_effective_backend_not_hardcoded_vulkan()** (5 connections) — `tests/slots/test_adopted_slot_eviction.py`
- **test_idle_sweep_evicts_slot_missing_from_last_used()** (5 connections) — `tests/slots/test_adopted_slot_eviction.py`
- **test_pressure_sweep_sees_slot_missing_from_last_used()** (5 connections) — `tests/slots/test_adopted_slot_eviction.py`
- **test_sweep_candidates_falls_back_to_state_updated_at()** (5 connections) — `tests/slots/test_adopted_slot_eviction.py`
- **Adopted / restart-surviving slots participate in idle + pressure eviction.  Two** (1 connections) — `tests/slots/test_adopted_slot_eviction.py`
- **Pressure eviction reclaims an lru slot known only via state.json.** (1 connections) — `tests/slots/test_adopted_slot_eviction.py`
- **Adopting a running slot starts its idle clock.** (1 connections) — `tests/slots/test_adopted_slot_eviction.py`
- **The adopted extras carry the device/backend-derived token.** (1 connections) — `tests/slots/test_adopted_slot_eviction.py`
- **A READY slot absent from _last_used surfaces via state.json updated_at.** (1 connections) — `tests/slots/test_adopted_slot_eviction.py`
- **The TTL sweep unloads a dispatchable slot it previously couldn't see.** (1 connections) — `tests/slots/test_adopted_slot_eviction.py`

## Relationships

- [SlotManager](SlotManager.md) (5 shared connections)
- [conftest.py](conftest.py.md) (1 shared connections)
- [FakeContainerProvider](FakeContainerProvider.md) (1 shared connections)

## Source Files

- `tests/slots/test_adopted_slot_eviction.py`

## Audit Trail

- EXTRACTED: 44 (90%)
- INFERRED: 5 (10%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*