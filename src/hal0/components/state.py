"""Per-component converge bookkeeping: /var/lib/hal0/state/components.json.

One JSON object keyed by component id; each value is the last converge
status dict plus a ``ts`` stamp. Written atomically (same-dir tmp +
replace); reads tolerate a missing or corrupt file by returning ``{}`` —
this file is a courtesy cache over live probes, never load-bearing state.
Writes are fail-soft: losing a breadcrumb must never fail an update.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import structlog

from hal0.config.paths import var_lib

log = structlog.get_logger(__name__)


def components_state_path() -> Path:
    return var_lib() / "state" / "components.json"


def load_component_state() -> dict[str, dict[str, Any]]:
    try:
        raw = components_state_path().read_text(encoding="utf-8")
        loaded = json.loads(raw)
    except (OSError, ValueError):
        return {}
    if not isinstance(loaded, dict):
        return {}
    return {str(k): v for k, v in loaded.items() if isinstance(v, dict)}


def record_component_result(component_id: str, result: dict[str, Any]) -> None:
    entry = {**result, "ts": time.time()}
    current = load_component_state()
    current[component_id] = entry
    path = components_state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(current, f)
            os.replace(tmp, path)
            tmp = None
        finally:
            if tmp is not None:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
    except OSError as exc:
        log.warning("components.state_persist_failed", component=component_id, error=str(exc))
