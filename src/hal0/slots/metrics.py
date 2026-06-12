"""Slim Prometheus exposition over slot state.

Replacement for the legacy daemon-polling metrics shim: the only
always-on inference processes are the per-slot containers, whose
lifecycle state SlotManager already tracks. This module renders that
state in Prometheus text format 0.0.4 for ``GET /api/metrics/prometheus``.

Exposed series:

* ``hal0_slot_up{slot=...}`` — 1 when the slot is in the dispatchable
  ready-set (READY / SERVING / IDLE, per #696), else 0.
* ``hal0_slot_state{slot=...,state=...}`` — one-hot state indicator
  (1 for the slot's current state).
* ``hal0_slots_ready_total`` — count of dispatchable slots.

Per-slot llama-server native metrics (tokens/sec, KV usage) are a
follow-up: scrape each container's own ``/metrics`` when the toolbox
images enable it.
"""

from __future__ import annotations

from typing import Any

#: Dispatchable ready-set (#696) — mirrors SlotManager.is_ready_for_dispatch.
_READY_STATES = frozenset({"ready", "serving", "idle"})


def _escape_label(value: str) -> str:
    """Escape a Prometheus label value (backslash, quote, newline)."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def render_slot_metrics(slots: list[Any]) -> str:
    """Render the slot-state exposition body.

    ``slots`` is a list of :class:`hal0.slots.manager.Slot` snapshots
    (anything with ``name`` and ``state`` attributes works — the
    ``state`` may be a ``SlotState`` or a plain string).

    Returns the exposition text, always terminated by a newline when
    non-empty; an empty slot list yields the headers with a zero total
    so scrapers see "up and empty" rather than "no data".
    """
    lines: list[str] = [
        "# HELP hal0_slot_up Slot is dispatchable (READY/SERVING/IDLE).",
        "# TYPE hal0_slot_up gauge",
    ]
    ready_total = 0
    state_lines: list[str] = [
        "# HELP hal0_slot_state One-hot slot lifecycle state indicator.",
        "# TYPE hal0_slot_state gauge",
    ]
    for slot in slots:
        name = _escape_label(str(getattr(slot, "name", "") or ""))
        if not name:
            continue
        raw_state = getattr(slot, "state", "")
        state = str(getattr(raw_state, "value", raw_state) or "").lower()
        up = 1 if state in _READY_STATES else 0
        ready_total += up
        lines.append(f'hal0_slot_up{{slot="{name}"}} {up}')
        state_lines.append(f'hal0_slot_state{{slot="{name}",state="{_escape_label(state)}"}} 1')
    lines.extend(state_lines)
    lines.append("# HELP hal0_slots_ready_total Count of dispatchable slots.")
    lines.append("# TYPE hal0_slots_ready_total gauge")
    lines.append(f"hal0_slots_ready_total {ready_total}")
    return "\n".join(lines) + "\n"


__all__ = ["render_slot_metrics"]
