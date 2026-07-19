# slot

> 36 nodes · cohesion 0.08

## Key Concepts

- **slot** (15 connections) — `src/hal0/db/migrations/004_slots_ports.sql`
- **SlotWatchdog** (14 connections) — `src/hal0/slots/watchdog.py`
- **WatchdogHost** (12 connections) — `src/hal0/slots/watchdog.py`
- **._fail_watch_loop()** (10 connections) — `src/hal0/slots/watchdog.py`
- **.__init__()** (7 connections) — `src/hal0/slots/manager.py`
- **_FakeSM** (6 connections) — `tests/api/test_health_degraded.py`
- **.update()** (5 connections) — `src/hal0/slots/watchdog.py`
- **._transition()** (5 connections) — `src/hal0/slots/watchdog.py`
- **test_health_degraded.py** (5 connections) — `tests/api/test_health_degraded.py`
- **_slot()** (5 connections) — `tests/api/test_health_degraded.py`
- **test_health_system_degraded_when_slot_errored()** (4 connections) — `tests/api/test_health_degraded.py`
- **test_health_system_ok_when_no_errored_slots()** (4 connections) — `tests/api/test_health_degraded.py`
- **004_slots_ports.sql** (3 connections) — `src/hal0/db/migrations/004_slots_ports.sql`
- **watchdog.py** (3 connections) — `src/hal0/slots/watchdog.py`
- **._current_state()** (3 connections) — `src/hal0/slots/watchdog.py`
- **._key()** (3 connections) — `src/hal0/slots/watchdog.py`
- **.load()** (3 connections) — `src/hal0/slots/watchdog.py`
- **.unload()** (3 connections) — `src/hal0/slots/watchdog.py`
- **port_claim** (2 connections) — `src/hal0/db/migrations/004_slots_ports.sql`
- **slot_link** (2 connections) — `src/hal0/db/migrations/004_slots_ports.sql`
- **Lock** (2 connections)
- **Any** (2 connections)
- **.__init__()** (2 connections) — `src/hal0/slots/watchdog.py`
- **.__init__()** (2 connections) — `tests/api/test_health_degraded.py`
- **.list()** (2 connections) — `tests/api/test_health_degraded.py`
- *... and 11 more nodes in this community*

## Relationships

- [SlotConfigError](SlotConfigError.md) (15 shared connections)
- [SlotState](SlotState.md) (7 shared connections)
- [RoutingHost](RoutingHost.md) (2 shared connections)
- [_slot](_slot.md) (2 shared connections)
- [SlotManager](SlotManager.md) (2 shared connections)
- [slots.py](slots.py.md) (1 shared connections)
- [ReaperHost](ReaperHost.md) (1 shared connections)
- [MapContainerProvider](MapContainerProvider.md) (1 shared connections)

## Source Files

- `src/hal0/db/migrations/004_slots_ports.sql`
- `src/hal0/slots/manager.py`
- `src/hal0/slots/watchdog.py`
- `tests/api/test_health_degraded.py`
- `tests/slot_view/test_aggregator.py`

## Audit Trail

- EXTRACTED: 126 (92%)
- INFERRED: 11 (8%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*