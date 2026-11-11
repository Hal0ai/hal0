"""Pause memory writes while the store is being rebuilt (#3518, ADR-0058 decision 2).

hindsight-api has no engine-wide write pause, so ``hal0 memory embedder
switch`` makes its own window safe: while it runs it keeps one flag file in
the state directory, and every hal0 path that changes memory checks for it
first::

    /var/lib/hal0/state/memory-writes-paused.json
        {"version": 1, "reason": "embedder_switch", "run_id": "3f9a1c2e",
         "since": "2026-11-11T02:14:09+00:00", "pid": 41822}

The flag is a file, not process state, because the command and hal0-api are
different processes (and often different accounts).

One check, :func:`require_writes_allowed`, raises :class:`MemoryWritesPaused`
(``503``, code ``memory.writes_paused``). It is called by:

* ``HindsightProvider``'s mutating methods, which is every hal0 writer: the
  ``/api/memory/*`` routes, the ``hal0-memory`` MCP tools, the agent bridge,
  the brain;
* the mutating ``/api/memory/banks/*`` admin routes and the
  ``/api/memory/engine/*`` passthrough, which reach the engine without the
  provider (:func:`require_engine_call_allowed` tells a write from a read
  by method and path);
* ``POST /api/memory/add`` and the MCP ``memory_add`` tool directly, so the
  refusal comes before any other preflight.

Recall, reflect and every read stay open.

What the gate cannot see is a client that writes to the engine's own port
without going through hal0. The switch covers that case by comparing each
bank with its archive before it deletes anything.

A flag file that exists but cannot be read still pauses writes: the file
being there is the signal, its contents only explain it.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from hal0.config.paths import var_lib
from hal0.errors import Hal0Error
from hal0.install import perms
from hal0.security import nofollow

log = structlog.get_logger(__name__)

GATE_VERSION = 1

#: The only reason hal0 pauses memory writes today.
REASON_EMBEDDER_SWITCH = "embedder_switch"

#: What a refused caller is told to wait before trying again. A switch runs
#: for minutes, so this is a polling hint, not an estimate of the end.
RETRY_AFTER_S = 30

#: 0644: hal0-api reads the file as the service account even when a root-run
#: command wrote it, and it holds nothing secret.
_GATE_MODE = 0o644

_REASON_TEXT = {
    REASON_EMBEDDER_SWITCH: (
        "the memory store is being moved to another embedder (`hal0 memory embedder switch`)"
    ),
}


class MemoryWritesPaused(Hal0Error):
    """503: memory writes are paused for maintenance; reads still work."""

    code = "memory.writes_paused"
    status = 503

    def __init__(self, gate: dict[str, Any]) -> None:
        reason = str(gate.get("reason") or "maintenance")
        why = _REASON_TEXT.get(reason, "memory maintenance is running")
        super().__init__(
            f"Memory writes are paused: {why}. Nothing was written; recall still works. "
            "Retry when it has finished.",
            details={
                "reason": reason,
                "since": gate.get("since"),
                "retry_after_s": RETRY_AFTER_S,
            },
        )


def gate_path() -> Path:
    """``/var/lib/hal0/state/memory-writes-paused.json`` (sandboxed under ``HAL0_HOME``)."""
    return var_lib() / "state" / "memory-writes-paused.json"


def read_gate(path: Path | None = None) -> dict[str, Any] | None:
    """The gate in force, or ``None`` when writes are allowed.

    One ``open`` per call and no cache: a write is rare next to the engine
    round-trip it guards, and a cached answer could outlive the gate.
    """
    target = path if path is not None else gate_path()
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError):
        return {"reason": "unreadable", "since": None}
    try:
        data = json.loads(raw)
    except ValueError:
        return {"reason": "unreadable", "since": None}
    return data if isinstance(data, dict) else {"reason": "unreadable", "since": None}


def require_writes_allowed(path: Path | None = None) -> None:
    """Raise :class:`MemoryWritesPaused` while the gate is set. The one shared check."""
    gate = read_gate(path)
    if gate is not None:
        raise MemoryWritesPaused(gate)


#: Engine ``POST`` routes that change nothing: recall, reflect, and the
#: preview / dry-run / probe endpoints (hindsight-api 1.2.0 OpenAPI).
_READ_ONLY_POST = re.compile(
    r"/(memories/recall|reflect|prompts/preview|consolidation-strategies/preview"
    r"|memories/dry-run-extract|dry-run-refresh|health/llm)/?$"
)


def engine_call_writes(method: str, path: str) -> bool:
    """True when an engine call with this method and path changes the store."""
    return method.upper() != "GET" and not _READ_ONLY_POST.search(path)


def require_engine_call_allowed(method: str, path: str) -> None:
    """:func:`require_writes_allowed` for a forwarded engine call that writes."""
    if engine_call_writes(method, path):
        require_writes_allowed()


def engage_gate(
    *, reason: str = REASON_EMBEDDER_SWITCH, run_id: str | None = None, path: Path | None = None
) -> dict[str, Any]:
    """Set the gate and return what was written. Raises ``OSError`` on failure.

    Atomic, and owned by the state root's owner when a root caller writes it,
    like the other files under ``state/``.
    """
    target = path if path is not None else gate_path()
    body = {
        "version": GATE_VERSION,
        "reason": reason,
        "run_id": run_id,
        "since": datetime.now(UTC).isoformat(timespec="seconds"),
        "pid": os.getpid(),
    }
    nofollow.write_file(
        target,
        json.dumps(body, indent=2) + "\n",
        mode=_GATE_MODE,
        owner=perms.state_root_owner(),
    )
    log.info("hal0.memory.write_gate_engaged", reason=reason, run_id=run_id)
    return body


def lift_gate(path: Path | None = None) -> bool:
    """Remove the gate. ``True`` when a gate was removed. Raises ``OSError`` on failure."""
    target = path if path is not None else gate_path()
    try:
        target.unlink()
    except FileNotFoundError:
        return False
    log.info("hal0.memory.write_gate_lifted")
    return True


__all__ = [
    "REASON_EMBEDDER_SWITCH",
    "RETRY_AFTER_S",
    "MemoryWritesPaused",
    "engage_gate",
    "engine_call_writes",
    "gate_path",
    "lift_gate",
    "read_gate",
    "require_engine_call_allowed",
    "require_writes_allowed",
]
