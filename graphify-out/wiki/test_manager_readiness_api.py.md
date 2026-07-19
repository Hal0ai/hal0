# test_manager_readiness_api.py

> 19 nodes · cohesion 0.15

## Key Concepts

- **test_manager_readiness_api.py** (9 connections) — `tests/slots/test_manager_readiness_api.py`
- **_manager()** (9 connections) — `tests/slots/test_manager_readiness_api.py`
- **test_is_ready_for_dispatch_parametrized()** (5 connections) — `tests/slots/test_manager_readiness_api.py`
- **test_state_cache_hit_returns_cached_state()** (4 connections) — `tests/slots/test_manager_readiness_api.py`
- **test_state_cache_miss_falls_back_to_state_json()** (4 connections) — `tests/slots/test_manager_readiness_api.py`
- **test_state_resolves_alias()** (4 connections) — `tests/slots/test_manager_readiness_api.py`
- **_write_state()** (4 connections) — `tests/slots/test_manager_readiness_api.py`
- **test_is_ready_for_dispatch_offline_unknown_slot()** (3 connections) — `tests/slots/test_manager_readiness_api.py`
- **test_state_unknown_slot_returns_offline()** (3 connections) — `tests/slots/test_manager_readiness_api.py`
- **SlotManager** (1 connections)
- **Tests for SlotManager.state() and is_ready_for_dispatch() — issue #696.  Locked** (1 connections) — `tests/slots/test_manager_readiness_api.py`
- **Every SlotState is classified as dispatchable or not per the locked set (#696).** (1 connections) — `tests/slots/test_manager_readiness_api.py`
- **Unknown slot → OFFLINE → not ready.** (1 connections) — `tests/slots/test_manager_readiness_api.py`
- **Fresh SlotManager filesystem-isolated under tmp_hal0_home.** (1 connections) — `tests/slots/test_manager_readiness_api.py`
- **Write a minimal state.json for *slot_name* under tmp_hal0_home.      HAL0_HOME l** (1 connections) — `tests/slots/test_manager_readiness_api.py`
- **state() returns from in-memory cache when present.** (1 connections) — `tests/slots/test_manager_readiness_api.py`
- **state() reads state.json when the slot is not in the in-memory cache.** (1 connections) — `tests/slots/test_manager_readiness_api.py`
- **state() returns OFFLINE for an unknown slot — no exception raised.** (1 connections) — `tests/slots/test_manager_readiness_api.py`
- **state() transparently resolves back-compat aliases (agent-hermes → agent).** (1 connections) — `tests/slots/test_manager_readiness_api.py`

## Relationships

- [SlotConfigError](SlotConfigError.md) (3 shared connections)
- [SlotState](SlotState.md) (2 shared connections)

## Source Files

- `tests/slots/test_manager_readiness_api.py`

## Audit Trail

- EXTRACTED: 52 (95%)
- INFERRED: 3 (5%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*