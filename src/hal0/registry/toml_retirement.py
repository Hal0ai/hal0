"""Retirement of a TOML-era ``registry.toml``.

The model registry lives only in SQLite (``hal0.db``). Until this step
existed, every :class:`~hal0.registry.sqlite_store.SqliteModelRegistry`
re-imported ``/var/lib/hal0/registry/registry.toml`` whenever the ``model``
table was empty, and ``hal0 registry export`` / ``hal0 model import-backup``
wrote that same path, so deleting every model brought them all back.

The upgrade floor is 1.3.0, and every 1.3.0 box already runs the SQLite
registry and already did its first-boot import of the file. So the file
holds nothing the database needs, nothing reads it any more, and this step
never imports it. :func:`retire_registry_toml` runs from
:func:`hal0.updater.updater.run_post_activation_migrations` (every update,
every ``install.sh`` re-run, and the boot-time safety net):

1. If ``registry.toml`` exists, copy it to
   ``/var/lib/hal0/backups/registry-toml-<UTC>/`` and rename it to
   ``registry.toml.retired-<UTC>``.
2. Delete the dead ``registry.toml.lock`` sidecar.

Because the file is inert, nothing here is ever fatal. A file the daemon
cannot back up or rename (for example a root-owned ``0600`` file left by a
``sudo hal0 registry export``) is left in place with a WARNING, and
``hal0 doctor all`` reports it (:func:`retirement_blocker`) so the operator
can delete it. API boot and ``hal0 update`` are never blocked on it. The
database is never touched.
"""

from __future__ import annotations

import contextlib
import filecmp
import logging
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hal0.config import paths

log = logging.getLogger(__name__)

_TOML_NAME = "registry.toml"
_LOCK_NAME = "registry.toml.lock"


def registry_toml_path() -> Path:
    """Where a leftover TOML-era registry would sit."""
    return paths.registry_dir() / _TOML_NAME


def _free_path(base: Path) -> Path:
    """``base``, or ``base-2``, ``base-3`` … — the first name not taken."""
    candidate = base
    n = 2
    while candidate.exists():
        candidate = base.with_name(f"{base.name}-{n}")
        n += 1
    return candidate


def _drop_stale_lock(lock_file: Path, job_id: str | None) -> None:
    """Delete the dead ``registry.toml.lock`` sidecar. Best-effort."""
    try:
        lock_file.unlink(missing_ok=True)
    except OSError as exc:
        log.warning(
            "registry.toml_lock_unlink_failed job_id=%s path=%s err=%s", job_id, lock_file, exc
        )


def _existing_backup(toml_file: Path) -> Path | None:
    """A ``registry-toml-*`` backup dir already holding ``toml_file``'s bytes."""
    try:
        size = toml_file.stat().st_size
        for candidate in sorted((paths.var_lib() / "backups").glob("registry-toml-*")):
            copy = candidate / _TOML_NAME
            with contextlib.suppress(OSError):
                if copy.stat().st_size == size and filecmp.cmp(copy, toml_file, shallow=False):
                    return candidate
    except OSError:
        # Unreadable file or backups dir: fall through to a fresh copy, which
        # reports the real error.
        return None
    return None


def _failed(toml_file: Path, job_id: str | None, reason: str) -> dict[str, Any]:
    log.warning(
        "registry.toml_retire_failed job_id=%s path=%s reason=%s — nothing reads this "
        "file any more; remove it manually (hal0 doctor all reports it until then)",
        job_id,
        toml_file,
        reason,
    )
    return {"outcome": "failed", "reason": reason}


def retire_registry_toml(*, job_id: str | None = None) -> dict[str, Any]:
    """Back up and rename a leftover ``registry.toml``. Never raises.

    Returns ``{"outcome": "absent"}`` when there is no file,
    ``{"outcome": "retired", "backup": ..., "archived_to": ...}`` on success,
    or ``{"outcome": "failed", "reason": ...}`` when the file could not be
    backed up or renamed (it stays in place; the next run tries again).
    """
    toml_file = registry_toml_path()
    lock_file = paths.registry_dir() / _LOCK_NAME

    if not toml_file.exists():
        _drop_stale_lock(lock_file, job_id)
        return {"outcome": "absent"}

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    # A rename that keeps failing (every boot) must not pile up one identical
    # copy per attempt: reuse a backup that already holds these exact bytes.
    backup_dir = _existing_backup(toml_file)
    if backup_dir is None:
        backup_dir = _free_path(paths.var_lib() / "backups" / f"registry-toml-{stamp}")
        try:
            backup_dir.mkdir(parents=True)
            shutil.copy2(toml_file, backup_dir / _TOML_NAME)
        except OSError as exc:
            # Leave no empty backup dir behind for a copy that never landed.
            with contextlib.suppress(OSError):
                backup_dir.rmdir()
            return _failed(toml_file, job_id, f"cannot back it up: {exc}")

    target = _free_path(toml_file.with_name(f"{_TOML_NAME}.retired-{stamp}"))
    archived: Path | None = target
    try:
        toml_file.rename(target)
    except FileNotFoundError:
        # A concurrent run retired it first.
        archived = None
    except OSError as exc:
        return _failed(toml_file, job_id, f"backed up to {backup_dir} but cannot rename it: {exc}")
    _drop_stale_lock(lock_file, job_id)

    result: dict[str, Any] = {
        "outcome": "retired",
        "backup": str(backup_dir),
        "archived_to": str(archived) if archived else None,
    }
    log.info("registry.toml_retired job_id=%s result=%s", job_id, result)
    return result


def retirement_blocker(toml_file: Path | None = None) -> str | None:
    """Why a leftover ``registry.toml`` is still there, or ``None`` if absent.

    Checked with the caller's own permissions, which for ``hal0 doctor`` run
    by the operator is a fair proxy for the daemon's.
    """
    toml_file = toml_file if toml_file is not None else registry_toml_path()
    try:
        st = toml_file.stat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        return f"cannot stat it: {exc}"
    owner = f"owner uid {st.st_uid}, mode {st.st_mode & 0o777:04o}"
    if not os.access(toml_file, os.R_OK):
        return f"not readable, so it cannot be backed up ({owner})"
    if not os.access(toml_file.parent, os.W_OK | os.X_OK):
        return f"{toml_file.parent} is not writable, so it cannot be renamed"
    return f"not retired yet ({owner}); hal0-api retires it at its next start"


__all__ = ["registry_toml_path", "retire_registry_toml", "retirement_blocker"]
