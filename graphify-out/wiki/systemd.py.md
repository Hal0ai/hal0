# systemd.py

> 16 nodes · cohesion 0.19

## Key Concepts

- **systemd.py** (7 connections) — `src/hal0/services/systemd.py`
- **unit_action()** (7 connections) — `src/hal0/services/systemd.py`
- **_run()** (6 connections) — `src/hal0/services/systemd.py`
- **timer_schedule()** (6 connections) — `src/hal0/services/systemd.py`
- **valid_unit()** (6 connections) — `src/hal0/services/systemd.py`
- **unit_state()** (5 connections) — `src/hal0/services/systemd.py`
- **unit_is_active()** (4 connections) — `src/hal0/services/systemd.py`
- **run_honcho_sync_now()** (3 connections) — `src/hal0/api/routes/memory.py`
- **Trigger one graph-sync run now, non-blocking.      Starts the oneshot ``hal0-hon** (1 connections) — `src/hal0/api/routes/memory.py`
- **Shared systemctl helpers for the companion-service management layer.  Until now** (1 connections) — `src/hal0/services/systemd.py`
- **Return a ``.timer`` unit's calendar expression + last/next trigger.      Shape (** (1 connections) — `src/hal0/services/systemd.py`
- **True when ``systemctl is-active <unit>`` reports ``active``.** (1 connections) — `src/hal0/services/systemd.py`
- **Run one allow-listed lifecycle verb against ``unit``.      Returns ``{"ok": bool** (1 connections) — `src/hal0/services/systemd.py`
- **True when ``unit`` is a plausible systemd unit name.** (1 connections) — `src/hal0/services/systemd.py`
- **Run ``systemctl <args>``; (rc, stdout, stderr), rc=None on no-systemd/timeout.** (1 connections) — `src/hal0/services/systemd.py`
- **Return the unit's systemd state for display.      Shape (all values fail-soft)::** (1 connections) — `src/hal0/services/systemd.py`

## Relationships

- [memory.py](memory.py.md) (5 shared connections)
- [get_executor](get_executor.md) (1 shared connections)

## Source Files

- `src/hal0/api/routes/memory.py`
- `src/hal0/services/systemd.py`

## Audit Trail

- EXTRACTED: 45 (87%)
- INFERRED: 7 (13%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*