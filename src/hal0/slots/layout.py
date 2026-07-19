"""Slot on-disk layout primitives — the bilingual (name-or-id) key seam.

A slot's two durable on-disk artefacts are addressed by a *stem*:

  * ``/etc/hal0/slots/<stem>.toml``            — the slot config;
  * ``/var/lib/hal0/slots/<stem>/state.json``  — the live state record.

Historically the stem was always the slot's mutable ``name``. The M5 id-flip
(rework §11.1, migrated by :mod:`hal0.slots.migrate_id_keying`) re-keys those
artefacts to the stable opaque ``id`` — so a converged box carries a mix while
a migration rolls forward, and every reader must accept BOTH shapes.

These are pure functions (no I/O beyond ``Path`` composition and a directory
glob) so the loader, the manager, and their tests share one classification
rule. The HARD INVARIANT they encode: a *digit-only* stem is an id key; every
other stem is a name key. An id key never collides with a name because slot
ids are SQLite ``AUTOINCREMENT`` integers (>= 1) and slot names are never
all-digit.
"""

from __future__ import annotations

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


__all__ = [
    "classify_layout",
    "is_id_stem",
    "slot_state_path",
    "slot_toml_path",
]
