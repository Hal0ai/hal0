"""control.py — web-driven run queue + worker control state.

The dashboard lets an operator queue benchmark runs and start/pause/stop the
worker that drains them. That needs a tiny bit of shared state between three
processes — the read-only web API (writes intent), the worker (drains the queue
and drives sessions), and any CLI — so it lives as small JSON files under the
state root, next to records.jsonl:

    control.json   {"state": "stopped"|"running"|"paused", "exclusive": bool}
    queue.json     {"items": [{"id", "label", "suite"|null, "model"|null, "enqueued"}]}
    status.json    {"active": {...}|null, "updated": ".."}   # the worker writes this

Design choices (deliberate, safety-first):
  * ``state`` defaults to ``stopped`` — the worker does NOTHING (never touches the
    GPU / competing slots) until an operator explicitly hits Start. So installing
    the worker service is inert until armed from the UI.
  * ``exclusive`` mirrors the "shut down competing slots" toggle: when true the
    worker runs each item with the seam's ``--exclusive`` (stop/restart GPU slots
    for clean numbers); when false it runs politely and marks contended results.
  * Writes are whole-file + atomic (write temp, ``os.replace``) so a concurrent
    reader never sees a half-written file, and every read-modify-write cycle
    (enqueue/dequeue/set_control) holds the shared state lock — the old
    lock-free RMW could lose a queue item when the API enqueued while the
    worker dequeued.

Pausing is between-ITEM, not mid-sweep: a llama-bench sweep can't be suspended,
so Pause/Stop take effect at the next cell boundary (the worker checks control
between cells via run_session's should_continue hook). This is documented in the
UI.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .store import state_lock, state_root

_CONTROL_DEFAULT: dict[str, Any] = {"state": "stopped", "exclusive": True}


def _path(name: str) -> Path:
    return state_root() / name


def _read(name: str, default: Any) -> Any:
    p = _path(name)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return default


def _write(name: str, obj: Any) -> None:
    p = _path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False))
    os.replace(tmp, p)  # atomic


# -- control ---------------------------------------------------------------- #


def read_control() -> dict[str, Any]:
    c = _read("control.json", dict(_CONTROL_DEFAULT))
    return {"state": c.get("state", "stopped"), "exclusive": bool(c.get("exclusive", True))}


def set_control(state: str | None = None, exclusive: bool | None = None) -> dict[str, Any]:
    if state is not None and state not in ("stopped", "running", "paused"):
        raise ValueError(f"bad control state {state!r}")
    with state_lock(state_root()):
        c = read_control()
        if state is not None:
            c["state"] = state
        if exclusive is not None:
            c["exclusive"] = bool(exclusive)
        _write("control.json", c)
    return c


def worker_should_run() -> bool:
    """True iff the worker may drive a session right now (Start pressed)."""
    return read_control().get("state") == "running"


# -- queue ------------------------------------------------------------------ #


def read_queue() -> list[dict[str, Any]]:
    return list(_read("queue.json", {"items": []}).get("items", []))


def enqueue(item: dict[str, Any]) -> list[dict[str, Any]]:
    with state_lock(state_root()):
        items = read_queue()
        items.append(item)
        _write("queue.json", {"items": items})
    return items


def dequeue(item_id: str) -> list[dict[str, Any]]:
    with state_lock(state_root()):
        items = [i for i in read_queue() if i.get("id") != item_id]
        _write("queue.json", {"items": items})
    return items


# -- status (worker-written) ------------------------------------------------ #


def read_status() -> dict[str, Any]:
    return _read("status.json", {"active": None, "updated": None})


def write_status(active: dict[str, Any] | None, updated: str) -> None:
    _write("status.json", {"active": active, "updated": updated})
