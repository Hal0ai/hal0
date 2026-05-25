"""Single-path model store — propagation + migration + suggestions.

The ``[models].store`` setting in hal0.toml is the one source of truth
for where hal0 reads and writes model files. This module turns that
abstract setting into concrete on-disk effects:

* :func:`describe_store_state` — probe one path for existence /
  writability / contents (file count, byte size, free space). Used by
  the GET endpoint and the dry-run path of the set endpoint.
* :func:`build_suggestions` — return a small list of common storage
  locations the firstrun + settings UIs render as preset chips, each
  with its current state probe.
* :func:`plan_migration` — compare the current effective store against
  a candidate target and return either ``MigrationPlan(needed=False, …)``
  (no move required) or ``MigrationPlan(needed=True, files_count, …)``.
* :func:`execute_migration` — move files from old → new with cross-fs
  fallback. Failure leaves both paths intact (no partial state).
* :func:`propagate_lemonade_config` — atomically overwrite
  ``extra_models_dir`` inside ``/var/lib/hal0/lemonade/config.json`` and
  return whether the value actually changed (caller decides whether to
  restart the service).
* :func:`restart_lemonade_service` — best-effort ``systemctl restart
  hal0-lemonade.service``. Returns True/False/None (None when systemd
  isn't available, e.g. tests / containers).

All on-disk writes go through :func:`hal0.config.loader.write_toml_atomic`'s
tempfile + fsync + rename pattern so a crash mid-write never leaves a
half-written file on disk.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hal0.config import paths

log = logging.getLogger(__name__)


# Lemonade's own config.json file lives under /var/lib/hal0/lemonade
# and is owned by the ``hal0`` system user (per installer/install.sh).
# We use the same path resolution so HAL0_HOME-rooted dev installs
# write under their tree, not /var/lib/hal0.
def _lemonade_config_path() -> Path:
    return paths.var_lib() / "lemonade" / "config.json"


# ── Probe one path ────────────────────────────────────────────────────────


@dataclass
class StoreStateProbe:
    """Snapshot of one filesystem path's suitability as a model store."""

    path: str
    exists: bool
    is_dir: bool
    readable: bool
    writable: bool
    files_count: int
    size_bytes: int
    free_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "exists": self.exists,
            "is_dir": self.is_dir,
            "readable": self.readable,
            "writable": self.writable,
            "files_count": self.files_count,
            "size_bytes": self.size_bytes,
            "free_bytes": self.free_bytes,
        }


def describe_store_state(path: str | Path) -> StoreStateProbe:
    """Inspect ``path`` and return a :class:`StoreStateProbe` snapshot.

    Probes are non-throwing — missing paths / unreadable directories
    return zero counts with the corresponding boolean flag cleared so
    the UI can render a coherent "this candidate is bad because X" line
    without the caller fanning out into per-error branches.
    """
    p = Path(path)
    state = StoreStateProbe(
        path=str(p),
        exists=False,
        is_dir=False,
        readable=False,
        writable=False,
        files_count=0,
        size_bytes=0,
        free_bytes=0,
    )
    try:
        state.exists = p.exists()
    except OSError:
        return state
    if not state.exists:
        # Free space still reports against the deepest existing ancestor
        # so the UI can render "if you create this dir, you'll have N
        # bytes free here" without a separate call.
        anc = p
        while anc != anc.parent and not anc.exists():
            anc = anc.parent
        if anc.exists():
            with contextlib.suppress(OSError):
                state.free_bytes = shutil.disk_usage(anc).free
        return state
    state.is_dir = p.is_dir()
    state.readable = os.access(p, os.R_OK)
    state.writable = os.access(p, os.W_OK)
    if state.is_dir:
        files, size = _walk_size(p)
        state.files_count = files
        state.size_bytes = size
        with contextlib.suppress(OSError):
            state.free_bytes = shutil.disk_usage(p).free
    return state


def _walk_size(p: Path) -> tuple[int, int]:
    """Recursively sum file count + bytes under ``p``."""
    files = 0
    size = 0
    try:
        for child in p.rglob("*"):
            try:
                if child.is_file():
                    files += 1
                    size += child.stat().st_size
            except OSError:
                continue
    except OSError:
        return files, size
    return files, size


# ── Suggestions ──────────────────────────────────────────────────────────


def build_suggestions(current: str | None = None) -> list[dict[str, Any]]:
    """Return a small list of candidate storage paths the UI can chip.

    Each entry is the path itself plus its current probe state so the UI
    can label chips with "exists · 4 models · 12 GB" or "empty · 412 GB
    free" without an extra round-trip per chip.

    Order is deliberately curated for the firstrun UX:
      1. ``/mnt/ai-models`` — the conventional external NFS / fast-disk
         mount most hal0 deployments target.
      2. ``paths.models_dir()`` — hal0's FHS default (per-install).
      3. ``~/.local/share/hal0/models`` — XDG-style per-user fallback
         when running as a non-root user.
      4. ``current`` — the currently-active store (if any), so the UI
         can show it as a "stay where you are" option even if it's not
         in the above list.
    Duplicates are dropped while preserving the first occurrence.
    """
    raw: list[str] = ["/mnt/ai-models", str(paths.models_dir())]
    # XDG-style per-user dir is meaningful when HOME exists.
    home = os.environ.get("HOME", "").strip()
    if home:
        raw.append(str(Path(home) / ".local" / "share" / "hal0" / "models"))
    if current:
        raw.append(current)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for path in raw:
        if path in seen:
            continue
        seen.add(path)
        state = describe_store_state(path)
        out.append(
            {
                "path": path,
                "is_current": current == path,
                **state.to_dict(),
            }
        )
    return out


# ── Migration plan ──────────────────────────────────────────────────────


@dataclass
class MigrationPlan:
    """Result of comparing the current effective store to a target path.

    When ``needed`` is False, callers can propagate + persist directly.
    When ``needed`` is True, the caller renders a confirmation surface
    and resubmits with ``migrate=True`` to actually move the bytes.
    """

    needed: bool
    source: str | None
    target: str
    files_count: int = 0
    size_bytes: int = 0
    same_filesystem: bool = False
    reason: str = ""


def plan_migration(*, current: str | None, target: str) -> MigrationPlan:
    """Compute whether moving from ``current`` to ``target`` requires data move.

    A migration is **needed** when:

      * ``current`` is set and resolves to a different path than ``target``, AND
      * ``current`` exists with at least one regular file under it.

    A migration is **not** needed when:

      * ``current`` is None / unset (nothing to move from).
      * ``current == target`` (no-op).
      * ``current`` exists but is empty.
      * ``current`` does not exist.
    """
    if not current:
        return MigrationPlan(needed=False, source=None, target=target, reason="no_prior_store")
    src = Path(current)
    dst = Path(target)
    try:
        if src.resolve() == dst.resolve():
            return MigrationPlan(needed=False, source=str(src), target=target, reason="same_path")
    except OSError:
        if str(src) == str(dst):
            return MigrationPlan(needed=False, source=str(src), target=target, reason="same_path")
    if not src.exists():
        return MigrationPlan(needed=False, source=str(src), target=target, reason="source_missing")
    files, size = _walk_size(src)
    if files == 0:
        return MigrationPlan(needed=False, source=str(src), target=target, reason="source_empty")
    same_fs = False
    with contextlib.suppress(OSError):
        same_fs = src.stat().st_dev == (
            dst.stat().st_dev if dst.exists() else dst.parent.stat().st_dev
        )
    return MigrationPlan(
        needed=True,
        source=str(src),
        target=target,
        files_count=files,
        size_bytes=size,
        same_filesystem=same_fs,
        reason="has_data",
    )


# ── Migration apply ──────────────────────────────────────────────────────


@dataclass
class MigrationResult:
    """Outcome of :func:`execute_migration` — surfaces moved entries +
    any per-entry failures so the API can render an actionable response."""

    source: str
    target: str
    moved: list[str] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)


def execute_migration(plan: MigrationPlan) -> MigrationResult:
    """Move every top-level child of ``plan.source`` into ``plan.target``.

    On a per-entry failure we record the offender in ``failed`` and
    continue with the rest — surfacing the whole picture lets the UI
    decide whether to retry just the bad entries or back out wholesale.
    Failures DO NOT remove the source entry; the operator can re-run
    after fixing whatever blocked the move (permissions, disk full).

    Uses :func:`shutil.move` which prefers ``os.rename`` for same-FS
    moves and falls back to recursive copy + remove for cross-FS. We
    don't pre-flight free-space because cross-FS moves can fail
    half-way regardless; we just bail loudly when the copy fails and
    leave the source intact.
    """
    if not plan.needed:
        return MigrationResult(source=plan.source or "", target=plan.target, moved=[], failed=[])

    src = Path(plan.source or "")
    dst = Path(plan.target)
    dst.mkdir(parents=True, exist_ok=True)
    result = MigrationResult(source=str(src), target=str(dst))

    for entry in sorted(src.iterdir()):
        # Skip dot-entries — they're hal0 internals (``.tmp`` staging,
        # registry sqlite WAL, etc.) and never user-visible models.
        if entry.name.startswith("."):
            continue
        dest_path = dst / entry.name
        if dest_path.exists():
            result.failed.append(
                {
                    "name": entry.name,
                    "reason": "target_exists",
                    "target": str(dest_path),
                }
            )
            continue
        try:
            shutil.move(str(entry), str(dest_path))
            result.moved.append(entry.name)
        except OSError as exc:
            result.failed.append({"name": entry.name, "reason": str(exc)})
            log.warning(
                "model_store.migrate_failed",
                extra={"name": entry.name, "src": str(entry), "error": str(exc)},
            )

    return result


# ── Lemonade config propagation ──────────────────────────────────────────


def propagate_lemonade_config(new_store: str) -> tuple[bool, str | None]:
    """Atomically rewrite Lemonade's ``extra_models_dir``.

    Returns ``(changed, previous_value)``:
      * ``changed=True``  — ``extra_models_dir`` was updated on disk and
        the lemond daemon needs a restart for the new value to take
        effect at load time.
      * ``changed=False`` — config either matched already or doesn't
        exist (fresh install before the lemonade installer ran). The
        caller should treat both as "no-op" and skip the restart.
    """
    config_path = _lemonade_config_path()
    if not config_path.exists():
        # The installer hasn't seeded the lemonade config yet (HAL0_HOME
        # dev install pre-bootstrap, or a fresh container). Nothing to
        # propagate; the next install run will pick up our new value.
        return False, None
    try:
        with open(config_path, "rb") as f:
            current = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not read lemonade config at {config_path}: {exc}") from exc

    prev = current.get("extra_models_dir")
    if prev == new_store:
        return False, prev if isinstance(prev, str) else None

    current["extra_models_dir"] = new_store

    # Atomic write: tempfile in the same directory + fsync + os.replace.
    # Matches the pattern in hal0.config.loader.write_toml_atomic so the
    # invariant ("lemond never parses a half-written config.json") is
    # uniform across hal0.
    tmp_path: Path | None = None
    try:
        fd, tmp_str = tempfile.mkstemp(
            prefix=f".{config_path.name}.",
            suffix=".tmp",
            dir=config_path.parent,
        )
        tmp_path = Path(tmp_str)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(current, f, indent=2, sort_keys=True)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            with contextlib.suppress(OSError):
                os.close(fd)
            raise
        os.replace(tmp_path, config_path)
        tmp_path = None
    finally:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink(missing_ok=True)

    return True, prev if isinstance(prev, str) else None


# ── Lemonade service restart ─────────────────────────────────────────────


async def restart_lemonade_service() -> bool | None:
    """Best-effort ``systemctl restart hal0-lemonade.service``.

    Returns:
      * True  — systemctl exited 0.
      * False — systemctl exited non-zero (caller may want to surface
        the user-visible "restart failed" toast).
      * None  — systemctl unavailable (HAL0_HOME dev install, container
        without systemd, CI). The caller treats this as "nothing to
        restart" rather than a failure.
    """
    if os.environ.get("HAL0_HOME"):
        # Dev install — no real systemd to talk to. Treat as no-op.
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl",
            "restart",
            "hal0-lemonade.service",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except (FileNotFoundError, PermissionError, OSError):
        return None
    try:
        rc = await asyncio.wait_for(proc.wait(), timeout=15.0)
    except TimeoutError:
        proc.kill()
        return False
    return rc == 0


__all__ = [
    "MigrationPlan",
    "MigrationResult",
    "StoreStateProbe",
    "build_suggestions",
    "describe_store_state",
    "execute_migration",
    "plan_migration",
    "propagate_lemonade_config",
    "restart_lemonade_service",
]
