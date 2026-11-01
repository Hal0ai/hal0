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
from hal0.install.perms import SHARED_DIR_MODE, ensure_shared_dir
from hal0.security import nofollow

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
        owner: tuple[int, int] | None = None
        if os.geteuid() == 0:
            dir_fd = nofollow.open_dir(path.parent, create=True, mkdir_mode=SHARED_DIR_MODE)
            try:
                # Like config locks, adopt the service-owned state root rather
                # than the root-run installer's identity. No account lookup is
                # needed on development or root-owned installations.
                owner = nofollow.dir_owner(var_lib())
                os.fchown(dir_fd, *owner)
                os.fchmod(dir_fd, SHARED_DIR_MODE)
            finally:
                os.close(dir_fd)
        else:
            ensure_shared_dir(path.parent)
        tmp: str | None
        fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(current, f)
                if owner is not None:
                    # Publish only after ownership is correct; a failed chown
                    # must leave the previous cache intact, never root-only.
                    os.fchown(f.fileno(), *owner)
            os.replace(tmp, path)
            tmp = None
        finally:
            if tmp is not None:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
    except OSError as exc:
        log.warning("components.state_persist_failed", component=component_id, error=str(exc))
