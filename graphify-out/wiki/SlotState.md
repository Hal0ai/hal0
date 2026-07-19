# SlotState

> 138 nodes

## Key Concepts

- **SlotState** (105 connections) — `src/hal0/slots/state.py`
- **.create()** (29 connections) — `src/hal0/slots/manager.py`
- **.update_config()** (24 connections) — `src/hal0/slots/manager.py`
- **.load()** (23 connections) — `src/hal0/slots/manager.py`
- **Any** (22 connections)
- **._transition()** (22 connections) — `src/hal0/slots/manager.py`
- **.status()** (22 connections) — `src/hal0/slots/manager.py`
- **_cfg_to_dict()** (20 connections) — `src/hal0/slots/_cfg_helpers.py`
- **._resolve_alias()** (20 connections) — `src/hal0/slots/manager.py`
- **._key()** (20 connections) — `src/hal0/slots/manager.py`
- **.rename()** (20 connections) — `src/hal0/slots/manager.py`
- **.state()** (18 connections) — `src/hal0/slots/manager.py`
- **._current_state()** (16 connections) — `src/hal0/slots/manager.py`
- **.delete()** (15 connections) — `src/hal0/slots/manager.py`
- **._load_slot_config()** (15 connections) — `src/hal0/slots/manager.py`
- **._maybe_adopt_running_slot()** (15 connections) — `src/hal0/slots/manager.py`
- **container_provider()** (14 connections) — `src/hal0/providers/container.py`
- **.unload()** (14 connections) — `src/hal0/slots/manager.py`
- **._ensure_known()** (13 connections) — `src/hal0/slots/manager.py`
- **_cfg_port()** (12 connections) — `src/hal0/slots/_cfg_helpers.py`
- **_model_default()** (12 connections) — `src/hal0/slots/_cfg_helpers.py`
- **._config_file()** (11 connections) — `src/hal0/slots/manager.py`
- **.restart()** (11 connections) — `src/hal0/slots/manager.py`
- **._maybe_load_config()** (11 connections) — `src/hal0/slots/manager.py`
- **._await_ready()** (11 connections) — `src/hal0/slots/manager.py`
- *... and 113 more nodes in this community*

## Relationships

- [SlotManager](SlotManager.md) (68 shared connections)
- [SlotConfigError](SlotConfigError.md) (60 shared connections)
- [write_slot_toml](write_slot_toml.md) (12 shared connections)
- [_RecordingSlotManager](_RecordingSlotManager.md) (11 shared connections)
- [SlotConfig](SlotConfig.md) (9 shared connections)
- [Dispatcher](Dispatcher.md) (8 shared connections)
- [WatchdogHost](WatchdogHost.md) (8 shared connections)
- [_reconcile_device_profile](_reconcile_device_profile.md) (6 shared connections)
- [compute_config_drift](compute_config_drift.md) (5 shared connections)
- [StackApplyEngine](StackApplyEngine.md) (5 shared connections)
- [_slot](_slot.md) (5 shared connections)
- [Any](Any.md) (4 shared connections)

## Source Files

- `src/hal0/api/routes/slots.py`
- `src/hal0/providers/container.py`
- `src/hal0/providers/flm.py`
- `src/hal0/slots/_cfg_helpers.py`
- `src/hal0/slots/config_write.py`
- `src/hal0/slots/manager.py`
- `src/hal0/slots/npu/trio.py`
- `src/hal0/slots/state.py`
- `src/hal0/slots/watchdog.py`
- `tests/slots/test_dispatchable_ready_set_single_source.py`
- `tests/slots/test_state_transitions.py`

## Audit Trail

- EXTRACTED: 679 (75%)
- INFERRED: 229 (25%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*