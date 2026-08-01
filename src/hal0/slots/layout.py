"""Slot on-disk layout primitives — the bilingual (name-or-id) key seam.

A slot's two durable on-disk artefacts are addressed by a *stem*:

  * ``/etc/hal0/slots/<stem>.toml``            — the slot config;
  * ``/var/lib/hal0/slots/<stem>/state.json``  — the live state record.

Historically the stem was always the slot's mutable ``name``. The M5 id-flip
(rework §11.1, migrated by :mod:`hal0.slots.migrate_id_keying`) re-keys those
artefacts to the stable opaque ``id`` — so a converged box carries a mix while
a migration rolls forward, and every reader must accept BOTH shapes.

These are pure functions (no I/O beyond ``Path`` composition, a directory
glob, and — for the name→stem resolver — reading the slot TOMLs themselves) so
the loader, the manager, and their tests share one classification rule. The
HARD INVARIANT they encode: a *digit-only* stem is an id key; every other stem
is a name key. An id key never collides with a name because slot ids are
SQLite ``AUTOINCREMENT`` integers (>= 1) and slot names are never all-digit.

:func:`resolve_slot_stem` closes the other half of the seam: callers that hold
a slot's *display name* (a stack entry, a capability selection, an operator
request) and need the on-disk stem. It works from the file's own embedded
``name`` — ``loader.save_slot_config`` guarantees every bilingual writer emits
it precisely so a reader can recover the display name WITHOUT the identity DB,
which is what lets the stacks dry-run path resolve slots with no SlotManager
wired.
"""

from __future__ import annotations

import tomllib
from pathlib import Path


def is_id_stem(stem: str) -> bool:
    """True when *stem* is an id-key (all ASCII digits), else a name-key.

    ``"143"`` → id-keyed; ``"brain"`` / ``"flm-stt"`` / ``""`` → name-keyed.
    Uses ``str.isdigit`` restricted to ASCII so a unicode-digit name can never
    be mistaken for an id (``"١٢٣".isdigit()`` is True in Python — guard it).
    """
    return stem.isascii() and stem.isdigit()


def slot_toml_path(config_dir: Path | str, key: str | int) -> Path:
    """The ``<key>.toml`` config path under *config_dir* for a name-or-id key."""
    return Path(config_dir) / f"{key}.toml"


def slot_state_path(data_dir: Path | str, key: str | int) -> Path:
    """The ``<key>/state.json`` state path under *data_dir* for a name-or-id key."""
    return Path(data_dir) / str(key) / "state.json"


def classify_layout(config_dir: Path | str) -> dict[str, str]:
    """Map every slot-TOML stem under *config_dir* to ``"id"`` or ``"name"``.

    Dotfiles (atomic-write temporaries like ``.chat.toml.XXXX.tmp``) are
    skipped so a mid-write glob never reports a half-file. Returns an empty
    dict when the directory is absent.
    """
    d = Path(config_dir)
    if not d.exists():
        return {}
    out: dict[str, str] = {}
    for p in sorted(d.glob("*.toml")):
        if p.name.startswith("."):
            continue
        out[p.stem] = "id" if is_id_stem(p.stem) else "name"
    return out


def read_slot_display_name(path: Path) -> str | None:
    """The display name embedded in one slot TOML, or ``None``.

    Accepts BOTH on-disk shapes: the flat body the runtime writes (top-level
    ``name``) and the older nested one (``[slot] name``). An unreadable or
    unparseable file yields ``None`` rather than raising — a single corrupt
    TOML must never break enumeration for the rest.

    A digit ``name`` is rejected (``None``): a slot name is never all-digit, so
    a digit there means the file was written by something that confused the id
    for the name, and honouring it would let an id-keyed stem masquerade as a
    display name.
    """
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    if not isinstance(name, str):
        section = raw.get("slot")
        name = section.get("name") if isinstance(section, dict) else None
    if not isinstance(name, str) or not name or is_id_stem(name):
        return None
    return name


def slot_stems_by_name(config_dir: Path | str) -> dict[str, str]:
    """Map each slot's display name to its on-disk stem.

    On a name-keyed box this is the identity map. On an id-keyed box it is
    ``{"agent": "1", "rerank": "13", ...}``. Dotfiles (atomic-write
    temporaries) are skipped. A file whose display name cannot be recovered is
    omitted — it is still reachable by stem.

    First writer wins on a duplicate display name (stems are visited sorted),
    which keeps the mapping deterministic while a rename is half-applied.
    """
    d = Path(config_dir)
    if not d.exists():
        return {}
    out: dict[str, str] = {}
    for p in sorted(d.glob("*.toml")):
        if p.name.startswith("."):
            continue
        name = read_slot_display_name(p)
        if name is not None and name not in out:
            out[name] = p.stem
    return out


def resolve_slot_stem(config_dir: Path | str, key: str) -> str | None:
    """The on-disk stem for a slot addressed by display name OR stem.

    ``None`` when no such slot exists — callers distinguish "this slot lives
    under a different stem" from "this slot must be created".

    Stem-first: a file literally named ``<key>.toml`` wins, so a name-keyed box
    (and every caller that already holds a stem) resolves with a single
    ``exists()`` and never reads a TOML. Only when that misses do we fall back
    to the display-name index — which is the id-keyed case.
    """
    if not key:
        return None
    d = Path(config_dir)
    if (d / f"{key}.toml").exists():
        return key
    return slot_stems_by_name(d).get(key)


__all__ = [
    "classify_layout",
    "is_id_stem",
    "read_slot_display_name",
    "resolve_slot_stem",
    "slot_state_path",
    "slot_stems_by_name",
    "slot_toml_path",
]
