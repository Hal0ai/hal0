# SlotConfigError

> 193 nodes · cohesion 0.02

## Key Concepts

- **SlotConfigError** (56 connections) — `src/hal0/slots/state.py`
- **SlotStateRecord** (35 connections) — `src/hal0/slots/state.py`
- **Slot** (32 connections) — `src/hal0/slots/manager.py`
- **.create()** (29 connections) — `src/hal0/slots/manager.py`
- **.update_config()** (24 connections) — `src/hal0/slots/manager.py`
- **.load()** (23 connections) — `src/hal0/slots/manager.py`
- **Any** (22 connections)
- **.status()** (22 connections) — `src/hal0/slots/manager.py`
- **._transition()** (22 connections) — `src/hal0/slots/manager.py`
- **_cfg_to_dict()** (20 connections) — `src/hal0/slots/_cfg_helpers.py`
- **._key()** (20 connections) — `src/hal0/slots/manager.py`
- **.rename()** (20 connections) — `src/hal0/slots/manager.py`
- **._resolve_alias()** (20 connections) — `src/hal0/slots/manager.py`
- **SlotReaper** (19 connections) — `src/hal0/slots/reaper.py`
- **state.py** (19 connections) — `src/hal0/slots/state.py`
- **RegistryUnavailableError** (17 connections) — `src/hal0/slots/manager.py`
- **._current_state()** (16 connections) — `src/hal0/slots/manager.py`
- **read_state()** (16 connections) — `src/hal0/slots/state.py`
- **.delete()** (15 connections) — `src/hal0/slots/manager.py`
- **._load_slot_config()** (15 connections) — `src/hal0/slots/manager.py`
- **._maybe_adopt_running_slot()** (15 connections) — `src/hal0/slots/manager.py`
- **container_provider()** (14 connections) — `src/hal0/providers/container.py`
- **.unload()** (14 connections) — `src/hal0/slots/manager.py`
- **._ensure_known()** (13 connections) — `src/hal0/slots/manager.py`
- **SlotError** (13 connections) — `src/hal0/slots/state.py`
- *... and 168 more nodes in this community*

## Relationships

- [SlotManager](SlotManager.md) (97 shared connections)
- [ReaperHost](ReaperHost.md) (16 shared connections)
- [slot](slot.md) (15 shared connections)
- [SlotState](SlotState.md) (15 shared connections)
- [write_slot_toml](write_slot_toml.md) (14 shared connections)
- [_reconcile_device_profile](_reconcile_device_profile.md) (11 shared connections)
- [SlotConfig](SlotConfig.md) (9 shared connections)
- [RoutingHost](RoutingHost.md) (8 shared connections)
- [SlotInterface](SlotInterface.md) (7 shared connections)
- [GpuArbiter](GpuArbiter.md) (4 shared connections)
- [StackApplyEngine](StackApplyEngine.md) (4 shared connections)
- [compute_config_drift](compute_config_drift.md) (3 shared connections)

## Source Files

- `src/hal0/providers/container.py`
- `src/hal0/providers/flm.py`
- `src/hal0/slots/_cfg_helpers.py`
- `src/hal0/slots/manager.py`
- `src/hal0/slots/npu/trio.py`
- `src/hal0/slots/reaper.py`
- `src/hal0/slots/state.py`
- `src/hal0/slots/watchdog.py`
- `tests/slots/test_manager.py`
- `tests/slots/test_state_transitions.py`

## Audit Trail

- EXTRACTED: 837 (74%)
- INFERRED: 301 (26%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*