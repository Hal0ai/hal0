# WatchdogHost

> 16 nodes

## Key Concepts

- **WatchdogHost** (12 connections) — `src/hal0/slots/watchdog.py`
- **._fail_watch_loop()** (10 connections) — `src/hal0/slots/watchdog.py`
- **._transition()** (5 connections) — `src/hal0/slots/watchdog.py`
- **.update()** (5 connections) — `src/hal0/slots/watchdog.py`
- **watchdog.py** (3 connections) — `src/hal0/slots/watchdog.py`
- **._current_state()** (3 connections) — `src/hal0/slots/watchdog.py`
- **._key()** (3 connections) — `src/hal0/slots/watchdog.py`
- **.load()** (3 connections) — `src/hal0/slots/watchdog.py`
- **.unload()** (3 connections) — `src/hal0/slots/watchdog.py`
- **Any** (2 connections)
- **Slot** (2 connections)
- **.__init__()** (2 connections) — `src/hal0/slots/watchdog.py`
- **Push-driven failure detector (P3-slots §1b-watchdog).  ``SlotWatchdog`` polls a** (1 connections) — `src/hal0/slots/watchdog.py`
- **Narrow seam :class:`SlotWatchdog` needs from ``SlotManager``.** (1 connections) — `src/hal0/slots/watchdog.py`
- **Spawn or cancel the per-slot fail-watcher to match ``new_state``.          Live** (1 connections) — `src/hal0/slots/watchdog.py`
- **Poll the container unit's is-active and flip state when it dies.          Runs a** (1 connections) — `src/hal0/slots/watchdog.py`

## Relationships

- [SlotState](SlotState.md) (8 shared connections)
- [SlotConfigError](SlotConfigError.md) (6 shared connections)
- [compute_config_drift](compute_config_drift.md) (1 shared connections)

## Source Files

- `src/hal0/slots/watchdog.py`

## Audit Trail

- EXTRACTED: 55 (96%)
- INFERRED: 2 (4%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*