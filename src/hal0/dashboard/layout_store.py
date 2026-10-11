"""Persistent file-backed store for the operator's dashboard layout.

Single-operator LAN device — no auth, no per-user keys.  One layout file.

Storage path: ``paths.var_lib() / "dashboard-layout.json"``

Public API:
    load()                              -> dict (empty on missing/corrupt)
    save(layout: dict)                  -> None (atomic write)
    reconcile(layout)                   -> dict (pure, never raises)

``reconcile`` accepts only the v3 fixed-band schema
(:mod:`hal0.dashboard.layout_v3`); a pre-#1061 v2 file on disk reads as empty.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import structlog

from hal0.config import paths
from hal0.dashboard import layout_v3

log: logging.Logger = structlog.get_logger(__name__)

_LAYOUT_FILE = "dashboard-layout.json"

# ── Path helper ───────────────────────────────────────────────────────────────


def _layout_path() -> Path:
    """Return the on-disk path for the layout JSON file.

    Resolves through ``paths.var_lib()`` so tests that set HAL0_HOME
    automatically redirect to a tmp tree without any monkeypatching here.
    """
    return paths.var_lib() / _LAYOUT_FILE


# ── Atomic JSON write ─────────────────────────────────────────────────────────


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    """Write *data* as JSON to *path* atomically (tempfile + fsync + os.replace).

    Mirrors the TOML write pattern in ``hal0.config.loader.write_toml_atomic``:
    create a tempfile in the same directory (same mount), fsync, then rename.
    If the process dies mid-write the prior file is left intact.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp_p: Path | None = None
    try:
        fd, tmp_str = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        tmp_p = Path(tmp_str)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            with contextlib.suppress(OSError):
                os.close(fd)
            raise
        os.replace(tmp_p, path)
        tmp_p = None  # rename succeeded; no cleanup needed
    finally:
        if tmp_p is not None:
            with contextlib.suppress(OSError):
                tmp_p.unlink(missing_ok=True)


# ── Public API ────────────────────────────────────────────────────────────────


def load() -> dict[str, Any]:
    """Load the saved dashboard layout from disk.

    Returns an empty dict when no layout has been saved yet, and also when
    the file exists but contains corrupt JSON (logs a warning, never raises).
    """
    path = _layout_path()
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            log.warning(
                "dashboard layout file has unexpected type; resetting",
                path=str(path),
                got_type=type(data).__name__,
            )
            return {}
        return data
    except (json.JSONDecodeError, OSError) as exc:
        log.warning(
            "dashboard layout file is corrupt; ignoring",
            path=str(path),
            error=str(exc),
        )
        return {}


def save(layout: dict[str, Any]) -> None:
    """Write *layout* to disk atomically.

    Callers should call :func:`reconcile` before saving so the file never
    stores stale pin keys or out-of-range span values.
    """
    _write_json_atomic(_layout_path(), layout)


def reconcile(layout: dict[str, Any]) -> dict[str, Any]:
    """Return a defensively-normalised copy of *layout*.

    A v3 payload (the current fixed-band schema) is reconciled against the
    cell whitelists in :mod:`hal0.dashboard.layout_v3`. Any other shape (a
    pre-#1061 free-form grid file) is treated as "no saved layout" and comes
    back as ``{}``: the dashboard then renders its default bands, and the
    operator's next save overwrites the stale file.
    """
    if isinstance(layout, dict) and layout.get("v") == layout_v3.LAYOUT_VERSION:
        return layout_v3.reconcile(layout)
    return {}
