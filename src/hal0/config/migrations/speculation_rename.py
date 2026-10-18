"""One-shot rename of the profile ``mtp`` tune to ``speculation`` (ADR-0031 §7).

ADR-0029 gave a profile ``mtp = "auto" | "off"``. One switch now has to cover
every speculation kind (a paired drafter, the file's MTP heads, n-gram), so the
profile field is ``speculation = "auto" | "off" | "ngram"``. The model's own
``mtp`` fact (the file has draft heads) is not renamed; only the profile tune.

The rewrite, per ``[profile.<name>]`` entry holding a string ``mtp``:

* ``mtp = "off"``  becomes ``speculation = "off"``;
* ``mtp = "auto"`` is dropped (``auto`` is the default);
* a ``speculation`` already present wins and the stale ``mtp`` is dropped.

A boolean ``mtp`` is the pre-ADR-0029 shape: the profile-centric converter owns
it, so it is left alone here. There is no alias period: after the rewrite
``ProfileConfig`` (``extra="forbid"``) and the API refuse ``mtp``. The original
file is copied to ``<file>.bak-<UTC stamp>`` beside it first, so a rollback to
1.4.x (which would quarantine every profile carrying ``speculation``) can put it
back.

HAL0-SUNSET: v1.7.0 — goes with the profile-centric conversion chain.
"""

from __future__ import annotations

import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog
import tomli_w

from hal0.config.migrations.runner_system import _replace_file

log = structlog.get_logger(__name__)


@dataclass
class RenameReport:
    """What a run did (or, for a dry run, would do)."""

    changed: list[str] = field(default_factory=list)
    backup: Path | None = None


def _rewrite(entry: dict[str, Any]) -> bool:
    """Rewrite one profile entry in place; True when it changed."""
    mtp = entry.get("mtp")
    if not isinstance(mtp, str):
        return False
    del entry["mtp"]
    if mtp == "off" and "speculation" not in entry:
        entry["speculation"] = "off"
    return True


def _backup_path(path: Path) -> Path:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    target = path.with_name(f"{path.name}.bak-{stamp}")
    n = 0
    while target.exists():  # never overwrite an earlier restore point
        n += 1
        target = path.with_name(f"{path.name}.bak-{stamp}-{n}")
    return target


def convert(path: Path, *, dry_run: bool) -> RenameReport:
    """Rewrite ``profiles.toml`` at *path*; a missing or clean file is a no-op."""
    try:
        original = path.read_bytes()
    except FileNotFoundError:
        return RenameReport()
    data = tomllib.loads(original.decode("utf-8"))
    profiles = data.get("profile")
    if not isinstance(profiles, dict):
        return RenameReport()
    changed = sorted(
        name for name, entry in profiles.items() if isinstance(entry, dict) and _rewrite(entry)
    )
    if not changed or dry_run:
        return RenameReport(changed=changed)
    backup = _backup_path(path)
    _replace_file(backup, original, default_mode=0o600)
    _replace_file(path, tomli_w.dumps(data).encode("utf-8"))
    log.info("migrate.speculation_rename.done", profiles=changed, backup=str(backup))
    return RenameReport(changed=changed, backup=backup)
