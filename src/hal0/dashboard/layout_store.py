"""Persistent file-backed store for the operator's dashboard layout.

Single-operator LAN device — no auth, no per-user keys.  One layout file.

Storage path: ``paths.var_lib() / "dashboard-layout.json"``

Public API:
    load()                              -> dict (empty on missing/corrupt)
    save(layout: dict)                  -> None (atomic write)
    reconcile(layout, slot_names)       -> dict (pure, never raises)
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


def reconcile(layout: dict[str, Any], slot_names: list[str]) -> dict[str, Any]:
    """Return a defensively-normalised copy of *layout*.

    Rules applied (pure — never raises, never mutates the input):

    1. Every name in ``pinned`` must have a ``"pin:<name>"`` entry in
       ``order``; if missing, insert it right *after* the ``"slots"`` entry
       (or append it).
    2. Drop ``"pin:<name>"`` keys from ``order`` (and ``spans``) when
       ``<name>`` is not in ``pinned`` and not in ``slot_names`` (stale slot
       that no longer exists).
    3. Clamp every value in ``spans`` to [1, 12].

    Args:
        layout:     The raw layout dict (may be empty ``{}``).
        slot_names: The names of currently-known slots from the slot manager.

    Returns:
        A new dict with the same top-level keys, normalised.
    """
    if not layout:
        return layout

    # Work on shallow copies of the mutable collections.
    order: list[str] = list(layout.get("order", []))
    spans: dict[str, int] = dict(layout.get("spans", {}))
    pinned: list[str] = list(layout.get("pinned", []))
    enabled: dict[str, bool] = dict(layout.get("enabled", {}))

    slot_name_set = set(slot_names)
    pinned_set = set(pinned)

    # ── Rule 3: clamp spans ────────────────────────────────────────────────
    for key in list(spans):
        try:
            val = int(spans[key])
        except (TypeError, ValueError):
            val = 1
        spans[key] = max(1, min(12, val))

    # ── Rule 2: drop stale pin:<name> keys ────────────────────────────────
    # A "pin:<name>" key is stale when <name> is not in pinned AND not in
    # slot_names (i.e. the slot was deleted and was never re-pinned).
    def _is_stale_pin(key: str) -> bool:
        if not key.startswith("pin:"):
            return False
        name = key[4:]
        return name not in pinned_set and name not in slot_name_set

    order = [k for k in order if not _is_stale_pin(k)]
    for stale in [k for k in spans if _is_stale_pin(k)]:
        del spans[stale]

    # ── Rule 1: ensure pin:<name> present in order for every pinned slot ──
    present_pins = {k[4:] for k in order if k.startswith("pin:")}
    missing_pins = [n for n in pinned if n not in present_pins]

    if missing_pins:
        # Find insertion point: right after "slots" entry, else append.
        try:
            slots_idx = order.index("slots")
            insert_at = slots_idx + 1
        except ValueError:
            insert_at = len(order)

        for i, name in enumerate(missing_pins):
            order.insert(insert_at + i, f"pin:{name}")

    result = dict(layout)
    result["order"] = order
    result["spans"] = spans
    result["pinned"] = pinned
    result["enabled"] = enabled
    return result
