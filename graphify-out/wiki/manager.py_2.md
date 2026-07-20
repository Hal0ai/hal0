# manager.py

> 8 nodes · cohesion 0.25

## Key Concepts

- **manager.py** (11 connections) — `src/hal0/slots/manager.py`
- **Slot lifecycle manager (container runtime).  SlotManager owns every aspect of sl** (1 connections) — `src/hal0/slots/manager.py`
- **# NOTE: ``extra=`` MUST NOT use "message" as a key — that's** (1 connections) — `src/hal0/slots/manager.py`
- **# NOTE: fail-watch tunables (_FAIL_WATCH_INTERVAL_S, _FAIL_WATCH_LIVE_STATES,** (1 connections) — `src/hal0/slots/manager.py`
- **# NOTE: idle-monitor tunables (_IDLE_AFTER_S, _IDLE_MONITOR_INTERVAL_S,** (1 connections) — `src/hal0/slots/manager.py`
- **# NOTE: callers wire this through ``Dispatcher.forward``; the** (1 connections) — `src/hal0/slots/manager.py`
- **# NOTE: _argv_values / _resolve_drift_flags / _config_drift_values_equal /** (1 connections) — `src/hal0/slots/manager.py`
- **# NOTE: _cfg_effective_backend / _base_profile_for_backend /** (1 connections) — `src/hal0/slots/manager.py`

## Relationships

- [SlotConfigError](SlotConfigError.md) (2 shared connections)
- [errors.py](errors.py.md) (1 shared connections)
- [SlotManager](SlotManager.md) (1 shared connections)

## Source Files

- `src/hal0/slots/manager.py`

## Audit Trail

- EXTRACTED: 18 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*