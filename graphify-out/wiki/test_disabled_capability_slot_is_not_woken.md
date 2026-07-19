# test_disabled_capability_slot_is_not_woken

> 21 nodes · cohesion 0.20

## Key Concepts

- **test_disabled_capability_slot_is_not_woken()** (14 connections) — `tests/dispatcher/test_capability_wake_on_evict.py`
- **test_capability_wake_on_evict.py** (12 connections) — `tests/dispatcher/test_capability_wake_on_evict.py`
- **test_capability_slot_wakes_on_request_after_eviction()** (11 connections) — `tests/dispatcher/test_capability_wake_on_evict.py`
- **test_evicted_capability_slot_404s_without_wake()** (9 connections) — `tests/dispatcher/test_capability_wake_on_evict.py`
- **wired()** (9 connections) — `tests/dispatcher/test_capability_wake_on_evict.py`
- **_make_request()** (7 connections) — `tests/dispatcher/test_capability_wake_on_evict.py`
- **SlotManager** (6 connections)
- **FakeContainerProvider** (5 connections)
- **_write_min_slot()** (5 connections) — `tests/dispatcher/test_capability_wake_on_evict.py`
- **_evict()** (4 connections) — `tests/dispatcher/test_capability_wake_on_evict.py`
- **_rerank_load_count()** (4 connections) — `tests/dispatcher/test_capability_wake_on_evict.py`
- **MonkeyPatch** (3 connections)
- **Path** (3 connections)
- **Request** (1 connections)
- **DR-1 regression: idle-EVICTED capability slots must wake on request.  The idle s** (1 connections) — `tests/dispatcher/test_capability_wake_on_evict.py`
- **Negative control: after eviction the upstream is deregistered, so a raw     disp** (1 connections) — `tests/dispatcher/test_capability_wake_on_evict.py`
- **DR-1: a /v1/rerankings request through the route reloads an evicted     rerank s** (1 connections) — `tests/dispatcher/test_capability_wake_on_evict.py`
- **DR-1 ↔ SC-1 interaction: the wake path must NOT revive a DISABLED slot.      Whe** (1 connections) — `tests/dispatcher/test_capability_wake_on_evict.py`
- **Write a minimal llama-server slot TOML (mirrors the slots suite helper).** (1 connections) — `tests/dispatcher/test_capability_wake_on_evict.py`
- **A Starlette Request whose ``app.state.slot_manager`` is wired.      ``app.state`** (1 connections) — `tests/dispatcher/test_capability_wake_on_evict.py`
- **A SlotManager + Dispatcher sharing ONE upstream registry.      Mirrors the real** (1 connections) — `tests/dispatcher/test_capability_wake_on_evict.py`

## Relationships

- [Dispatcher](Dispatcher.md) (6 shared connections)
- [UpstreamRegistry](UpstreamRegistry.md) (4 shared connections)
- [FakeContainerProvider](FakeContainerProvider.md) (2 shared connections)
- [v1.py](v1.py.md) (2 shared connections)
- [types.py](types.py.md) (1 shared connections)
- [conftest.py](conftest.py.md) (1 shared connections)

## Source Files

- `tests/dispatcher/test_capability_wake_on_evict.py`

## Audit Trail

- EXTRACTED: 96 (96%)
- INFERRED: 4 (4%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*