# SlotConfigError

> 125 nodes

## Key Concepts

- **SlotConfigError** (56 connections) — `src/hal0/slots/state.py`
- **SlotStateRecord** (35 connections) — `src/hal0/slots/state.py`
- **Slot** (31 connections) — `src/hal0/slots/manager.py`
- **SlotReaper** (19 connections) — `src/hal0/slots/reaper.py`
- **state.py** (19 connections) — `src/hal0/slots/state.py`
- **ReaperHost** (18 connections) — `src/hal0/slots/reaper.py`
- **SlotInterface** (17 connections) — `src/hal0/slots/interface.py`
- **RegistryUnavailableError** (17 connections) — `src/hal0/slots/manager.py`
- **read_state()** (16 connections) — `src/hal0/slots/state.py`
- **SlotWatchdog** (14 connections) — `src/hal0/slots/watchdog.py`
- **.pressure_evict_once()** (13 connections) — `src/hal0/slots/reaper.py`
- **SlotError** (13 connections) — `src/hal0/slots/state.py`
- **.swap()** (12 connections) — `src/hal0/slots/manager.py`
- **IllegalSlotTransition** (12 connections) — `src/hal0/slots/state.py`
- **write_state_atomic()** (12 connections) — `src/hal0/slots/state.py`
- **manager.py** (11 connections) — `src/hal0/slots/manager.py`
- **test_state_transitions.py** (11 connections) — `tests/slots/test_state_transitions.py`
- **SlotPinned** (10 connections) — `src/hal0/slots/state.py`
- **.sweep_idle_once()** (9 connections) — `src/hal0/slots/reaper.py`
- **SlotNotFound** (9 connections) — `src/hal0/slots/state.py`
- **TestListDegradesOnUnreadableSlot** (9 connections) — `tests/slots/test_manager.py`
- **.__init__()** (7 connections) — `src/hal0/slots/manager.py`
- **.evict_timeout_for()** (7 connections) — `src/hal0/slots/reaper.py`
- **reaper.py** (6 connections) — `src/hal0/slots/reaper.py`
- **test_state_record_round_trip()** (6 connections) — `tests/slots/test_state_transitions.py`
- *... and 100 more nodes in this community*

## Relationships

- [SlotState](SlotState.md) (60 shared connections)
- [SlotManager](SlotManager.md) (36 shared connections)
- [_make_env](_make_env.md) (10 shared connections)
- [RoutingHost](RoutingHost.md) (7 shared connections)
- [StackApplyEngine](StackApplyEngine.md) (6 shared connections)
- [WatchdogHost](WatchdogHost.md) (6 shared connections)
- [write_slot_toml](write_slot_toml.md) (3 shared connections)
- [_write_slot](_write_slot.md) (3 shared connections)
- [test_manager_readiness_api.py](test_manager_readiness_api.py.md) (3 shared connections)
- [errors.py](errors.py.md) (2 shared connections)
- [Hal0Error](Hal0Error.md) (2 shared connections)
- [FLMProvider](FLMProvider.md) (2 shared connections)

## Source Files

- `src/hal0/slots/interface.py`
- `src/hal0/slots/manager.py`
- `src/hal0/slots/reaper.py`
- `src/hal0/slots/state.py`
- `src/hal0/slots/watchdog.py`
- `tests/slots/test_manager.py`
- `tests/slots/test_state_transitions.py`

## Audit Trail

- EXTRACTED: 399 (68%)
- INFERRED: 188 (32%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*