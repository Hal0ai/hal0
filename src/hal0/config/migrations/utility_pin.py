"""One-time upgrade pass: give an existing ``utility`` slot an explicit pin.

hal0 used to seed a slot named ``utility`` and kept it warm through a name list
in the idle reaper (:data:`hal0.slots.reaper._PINNED_BY_DEFAULT`). New installs
seed ``memory`` instead and the name no longer carries meaning, so the pin of an
existing ``utility`` slot is written down as ``pinned = true`` in the slot's own
file: the slot stays pinned against the idle reaper whether or not the name list
still holds ``utility``.

This is a converter step on slot files, not a runtime fallback, and it is as
narrow as it can be:

* It acts only on a slot literally named ``utility`` that has no ``pinned`` key.
  An explicit ``pinned = true`` or ``pinned = false`` is the operator's choice
  and is never touched.
* It never creates, renames or deletes a slot, and a second run changes nothing.
* It edits the file in place, so the operator's comments survive, and falls back
  to a re-serialised file only when the line edit cannot be proven to produce the
  intended table.

It sits beside the other slot-file passes of ``hal0 update`` rather than in the
numbered ``vN`` chain: those transforms receive only the ``hal0.toml`` dict.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

UTILITY_SLOT = "utility"

_SLOT_TABLE_RE = re.compile(r"^\s*\[\s*slot\s*\]\s*(#.*)?$")
_ANY_TABLE_RE = re.compile(r"^\s*\[")


def _wants_pin(raw: dict[str, Any], stem: str) -> bool:
    """True for a slot named ``utility`` that carries no ``pinned`` key anywhere."""
    from hal0.slot_config import slot_scalar_table

    table = slot_scalar_table(raw)
    if "pinned" in table or "pinned" in raw:
        return False
    name = table.get("name")
    return (name if isinstance(name, str) and name else stem) == UTILITY_SLOT


def _pinned_text(text: str, raw: dict[str, Any]) -> str:
    """``text`` with ``pinned = true`` added to the slot's scalar table."""
    import tomli_w

    from hal0.slot_config import merge_slot_config

    expected = merge_slot_config(raw, {"pinned": True})
    nested = isinstance(raw.get("slot"), dict)
    lines = text.splitlines(keepends=True)
    insert_at = len(lines)
    for i, line in enumerate(lines):
        if nested and _SLOT_TABLE_RE.match(line):
            insert_at = i + 1
            break
        if not nested and _ANY_TABLE_RE.match(line):
            insert_at = i
            break
    if insert_at > 0 and not lines[insert_at - 1].endswith("\n"):
        lines[insert_at - 1] += "\n"
    edited = "".join([*lines[:insert_at], "pinned = true\n", *lines[insert_at:]])
    try:
        if tomllib.loads(edited) == expected:
            return edited
    except tomllib.TOMLDecodeError:
        pass
    return tomli_w.dumps(expected)


def _candidates(slots_dir: Path) -> list[Path]:
    found: list[Path] = []
    for path in sorted(slots_dir.glob("*.toml")):
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
            log.warning("updater.utility_pin_slot_unreadable", slot=path.stem, error=str(exc))
            continue
        if _wants_pin(raw, path.stem):
            found.append(path)
    return found


# HAL0-SUNSET: v2.0.0  — one-time pin of an existing `utility` slot; delete this module, its call in the updater's post-activation pass and its tests once every supported box has run it (then drop `utility` from the reaper's default-pinned set).
def pin_utility_slot(*, job_id: str | None = None) -> int:
    """Write ``pinned = true`` into an existing ``utility`` slot that has no pin key.

    Returns the number of slot files changed. A file that cannot be read or
    written is logged and skipped. Reads first without the slot write lock, so a
    box with nothing to convert (every fresh install, and every later run) needs
    no write access; the lock is taken, and each file re-read, only to write.
    """
    from hal0.config.migrations.runner_system import _replace_file
    from hal0.config.paths import slots_config_dir
    from hal0.slot_config import slot_write_lock

    slots_dir = slots_config_dir()
    if not slots_dir.is_dir() or not _candidates(slots_dir):
        return 0

    changed = 0
    with slot_write_lock():
        for path in _candidates(slots_dir):
            try:
                text = path.read_text(encoding="utf-8")
                _replace_file(path, _pinned_text(text, tomllib.loads(text)).encode("utf-8"))
            except Exception as exc:
                log.warning("updater.utility_pin_write_failed", slot=path.stem, error=str(exc))
                continue
            changed += 1
            log.info("updater.utility_slot_pinned", job_id=job_id, slot=path.stem)
    return changed


__all__ = ["UTILITY_SLOT", "pin_utility_slot"]
