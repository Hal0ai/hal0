"""One-shot retirement of a TOML-era ``registry.toml``.

The model registry lives only in SQLite (``hal0.db``). Until this step
existed, every :class:`~hal0.registry.sqlite_store.SqliteModelRegistry`
re-imported ``/var/lib/hal0/registry/registry.toml`` whenever the ``model``
table was empty, and ``hal0 registry export`` / ``hal0 model import-backup``
wrote that same path, so deleting every model brought them all back.

The upgrade floor is 1.3.0, and every 1.3.0 box already runs the SQLite
registry and already did its first-boot import of the file. So the file
holds nothing the database needs, and this step never imports it.
:func:`retire_registry_toml` runs from
:func:`hal0.updater.updater.run_post_activation_migrations` (every update,
every ``install.sh`` re-run, and the boot-time safety net):

1. If ``registry.toml`` exists, copy it to
   ``/var/lib/hal0/backups/registry-toml-<UTC>/`` and rename it to
   ``registry.toml.retired-<UTC>``, so nothing can ever read it again.
2. Delete the dead ``registry.toml.lock`` sidecar (best-effort: a failure
   only logs a warning).

It fails loud on the TOML itself: a failed backup or rename raises
:class:`RegistryConversionError` and leaves the file in place, so the next
run retries. The database is never touched.
"""

from __future__ import annotations

import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hal0.config import paths
from hal0.registry.store import RegistryError

log = logging.getLogger(__name__)

_TOML_NAME = "registry.toml"
_LOCK_NAME = "registry.toml.lock"


class RegistryConversionError(RegistryError):
    """The ``registry.toml`` retirement could not complete."""

    code = "model.registry_conversion_failed"
    status = 500


def _free_path(base: Path) -> Path:
    """``base``, or ``base-2``, ``base-3`` … — the first name not taken."""
    candidate = base
    n = 2
    while candidate.exists():
        candidate = base.with_name(f"{base.name}-{n}")
        n += 1
    return candidate


def _drop_stale_lock(lock_file: Path, job_id: str | None) -> None:
    """Delete the dead ``registry.toml.lock`` sidecar. Best-effort.

    Nothing takes this lock any more, so a leftover file is harmless clutter:
    failing to remove it logs a warning and never fails the migrations.
    """
    try:
        lock_file.unlink(missing_ok=True)
    except OSError as exc:
        log.warning(
            "registry.toml_lock_unlink_failed job_id=%s path=%s err=%s", job_id, lock_file, exc
        )


def retire_registry_toml(*, job_id: str | None = None) -> dict[str, Any]:
    """Back up and rename a leftover ``registry.toml``. Idempotent.

    Returns ``{"outcome": "absent"}`` when there is no file, otherwise
    ``{"outcome": "retired", "backup": ..., "archived_to": ...}``.

    Raises:
        RegistryConversionError: the backup or the rename failed. The file
            stays where it is, so the next run retries.
    """
    toml_file = paths.registry_dir() / _TOML_NAME
    lock_file = paths.registry_dir() / _LOCK_NAME

    if not toml_file.exists():
        _drop_stale_lock(lock_file, job_id)
        return {"outcome": "absent"}

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = _free_path(paths.var_lib() / "backups" / f"registry-toml-{stamp}")
    try:
        backup_dir.mkdir(parents=True)
        shutil.copy2(toml_file, backup_dir / _TOML_NAME)
    except OSError as exc:
        raise RegistryConversionError(
            f"could not back up {toml_file} before retiring it: {exc}",
            details={"path": str(toml_file), "backup_dir": str(backup_dir), "reason": str(exc)},
        ) from exc

    target = _free_path(toml_file.with_name(f"{_TOML_NAME}.retired-{stamp}"))
    archived: Path | None = target
    try:
        toml_file.rename(target)
    except FileNotFoundError:
        # A concurrent run retired it first.
        archived = None
    except OSError as exc:
        raise RegistryConversionError(
            f"backed up {toml_file} but could not rename it: {exc}",
            details={"path": str(toml_file), "backup_dir": str(backup_dir), "reason": str(exc)},
        ) from exc
    _drop_stale_lock(lock_file, job_id)

    result: dict[str, Any] = {
        "outcome": "retired",
        "backup": str(backup_dir),
        "archived_to": str(archived) if archived else None,
    }
    log.info("registry.toml_retired job_id=%s result=%s", job_id, result)
    return result


__all__ = ["RegistryConversionError", "retire_registry_toml"]
