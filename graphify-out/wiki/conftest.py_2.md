# conftest.py

> 16 nodes · cohesion 0.14

## Key Concepts

- **conftest.py** (16 connections) — `tests/slots/conftest.py`
- **test_health_probe_cfg.py** (5 connections) — `tests/slots/test_health_probe_cfg.py`
- **container_stub()** (4 connections) — `tests/slots/conftest.py`
- **test_container_readiness_check_passes_slot_cfg()** (4 connections) — `tests/slots/test_health_probe_cfg.py`
- **test_probe_health_passes_slot_cfg()** (4 connections) — `tests/slots/test_health_probe_cfg.py`
- **pytest_configure()** (3 connections) — `tests/slots/conftest.py`
- **slot_root()** (3 connections) — `tests/slots/conftest.py`
- **FakeContainerProvider** (2 connections)
- **Path** (2 connections)
- **MonkeyPatch** (1 connections)
- **Path** (1 connections)
- **Pytest fixtures and marker registration for the slots subtree.  Phase E (#687):** (1 connections) — `tests/slots/conftest.py`
- **Replace the process-wide ContainerProvider with the in-memory fake.      SlotMan** (1 connections) — `tests/slots/conftest.py`
- **Yield the slots-config root and ensure a sample slot exists on disk.** (1 connections) — `tests/slots/conftest.py`
- **Register the integration marker so --strict-markers stays clean.      The integr** (1 connections) — `tests/slots/conftest.py`
- **The manager's health probes pass the slot config to the provider.  ``ContainerPr** (1 connections) — `tests/slots/test_health_probe_cfg.py`

## Relationships

- [FakeContainerProvider](FakeContainerProvider.md) (3 shared connections)
- [SlotManager](SlotManager.md) (3 shared connections)
- [test_disabled_capability_slot_is_not_woken](test_disabled_capability_slot_is_not_woken.md) (1 shared connections)
- [test_adopted_slot_eviction.py](test_adopted_slot_eviction.py.md) (1 shared connections)
- [compute_config_drift](compute_config_drift.md) (1 shared connections)
- [test_fail_watcher.py](test_fail_watcher.py.md) (1 shared connections)
- [test_fail_watcher_warming.py](test_fail_watcher_warming.py.md) (1 shared connections)
- [test_pressure_eviction.py](test_pressure_eviction.py.md) (1 shared connections)
- [test_pulling_serving_idle.py](test_pulling_serving_idle.py.md) (1 shared connections)
- [test_restart_errored_slot.py](test_restart_errored_slot.py.md) (1 shared connections)
- [Path](Path.md) (1 shared connections)
- [planner.py](planner.py.md) (1 shared connections)

## Source Files

- `tests/slots/conftest.py`
- `tests/slots/test_health_probe_cfg.py`

## Audit Trail

- EXTRACTED: 48 (96%)
- INFERRED: 2 (4%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*