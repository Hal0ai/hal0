"""One-shot conversion of a TOML-era ``registry.toml`` into the SQLite registry.

The model registry lives only in SQLite (``hal0.db``). Releases before the
SQLite cutover kept it in ``/var/lib/hal0/registry/registry.toml``, and until
this conversion existed every :class:`~hal0.registry.sqlite_store.SqliteModelRegistry`
re-imported that file whenever the ``model`` table was empty. ``hal0 registry
export`` and ``hal0 model import-backup`` wrote the same path, so deleting
every model brought them all back on the next registry open.

:func:`convert_registry_toml` replaces that with one converting step, run by
:func:`hal0.updater.updater.run_post_activation_migrations` (every update,
every ``install.sh`` re-run, and the boot-time safety net):

1. No ``registry.toml``: record the ``registry_toml`` marker and stop.
2. Otherwise back up ``registry.toml`` and ``hal0.db`` under
   ``/var/lib/hal0/backups/registry-toml-<UTC>/``.
3. If this database has no ``registry_toml`` marker, it was never initialised
   from the file: import every entry (``INSERT OR IGNORE``, so a row already
   in SQLite is never overwritten) and record the marker in the same
   transaction. Migration ``011`` pre-marks a database that already holds
   models, because the old first-boot import already ran against it.
4. Rename the file to ``registry.toml.imported-<UTC>`` (or
   ``registry.toml.retired-<UTC>`` when nothing was imported), so it is never
   read again, and delete the dead ``registry.toml.lock`` sidecar.

It fails loud: a failed backup, an unreadable or malformed file, or a failed
rename raises :class:`RegistryConversionError` and leaves the file in place,
so the next run retries. Single entries that fail ``Model`` validation are
skipped and named in an ERROR log line; both copies of the file keep them.
"""

from __future__ import annotations

import contextlib
import json
import logging
import shutil
import sqlite3
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hal0.config import paths
from hal0.db import repository
from hal0.db.connection import connect, tx
from hal0.db.migrate import migrate
from hal0.model_meta import derive_model_provider
from hal0.registry.model import Model
from hal0.registry.store import RegistryError

log = logging.getLogger(__name__)

#: Marker row name in the ``one_shot_conversion`` table (migration 011).
MARKER = "registry_toml"

_TOML_NAME = "registry.toml"
_LOCK_NAME = "registry.toml.lock"


class RegistryConversionError(RegistryError):
    """The one-shot ``registry.toml`` conversion could not complete."""

    code = "model.registry_conversion_failed"
    status = 500


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _free_path(base: Path) -> Path:
    """``base``, or ``base-2``, ``base-3`` … — the first name not taken."""
    candidate = base
    n = 2
    while candidate.exists():
        candidate = base.with_name(f"{base.name}-{n}")
        n += 1
    return candidate


def _backup(toml_file: Path, db_file: Path, stamp: str) -> Path:
    """Copy ``registry.toml`` and a consistent snapshot of ``hal0.db``.

    Uses SQLite's online backup API for the database, so a live writer
    cannot leave a torn copy. Raises :class:`RegistryConversionError` on
    any failure; the conversion must not touch either file without a backup.
    """
    dest = _free_path(paths.var_lib() / "backups" / f"registry-toml-{stamp}")
    try:
        dest.mkdir(parents=True)
        shutil.copy2(toml_file, dest / _TOML_NAME)
        if db_file.exists():
            with (
                connect(db_file) as src,
                contextlib.closing(sqlite3.connect(dest / "hal0.db")) as dst,
            ):
                src.backup(dst)
    except (OSError, sqlite3.Error) as exc:
        raise RegistryConversionError(
            f"could not back up {toml_file} and {db_file} before converting: {exc}",
            details={"backup_dir": str(dest), "reason": str(exc)},
        ) from exc
    return dest


def _load(toml_file: Path) -> tuple[dict[str, Model], list[str]]:
    """Parse ``registry.toml`` into valid models plus the ids that failed.

    Raises :class:`RegistryConversionError` when the file itself cannot be
    read or parsed; a file the conversion cannot understand is never
    silently retired.
    """
    try:
        with open(toml_file, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RegistryConversionError(
            f"cannot read {toml_file}: {exc}",
            details={"path": str(toml_file), "reason": str(exc)},
        ) from exc

    raw = data.get("models", {})
    if isinstance(raw, list):
        # The oldest files used an array of tables carrying their own ``id``.
        raw = {str(e.get("id")): e for e in raw if isinstance(e, dict) and e.get("id")}
    if not isinstance(raw, dict):
        raise RegistryConversionError(
            f"{toml_file}: [models] is neither a table nor an array of tables",
            details={"path": str(toml_file)},
        )

    models: dict[str, Model] = {}
    invalid: list[str] = []
    for mid, entry in raw.items():
        if not isinstance(entry, dict):
            invalid.append(str(mid))
            continue
        payload: dict[str, Any] = {**entry, "id": mid}
        # Entries written before ``provider`` existed carry only ``backends``.
        payload.setdefault("provider", derive_model_provider(payload.get("backends")))
        try:
            models[str(mid)] = Model.model_validate(payload)
        except Exception as exc:
            log.warning("registry.toml_conversion.entry_invalid id=%s err=%s", mid, exc)
            invalid.append(str(mid))
    return models, invalid


def _marker(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT detail FROM one_shot_conversion WHERE name = ?", (MARKER,)
    ).fetchone()
    return None if row is None else (row[0] or "{}")


def _write_marker(conn: sqlite3.Connection, detail: dict[str, Any]) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO one_shot_conversion (name, completed_at, detail) VALUES (?, ?, ?)",
        (MARKER, datetime.now(UTC).isoformat(), json.dumps(detail, sort_keys=True)),
    )


def _import(conn: sqlite3.Connection, models: dict[str, Model]) -> tuple[int, int]:
    """INSERT OR IGNORE every model; return ``(imported, already_present)``."""
    imported = present = 0
    for model_id, model in models.items():
        row = repository.model_to_row(model)
        columns = ", ".join(row)
        placeholders = ", ".join("?" for _ in row)
        cur = conn.execute(
            f"INSERT OR IGNORE INTO model ({columns}) VALUES ({placeholders})",
            list(row.values()),
        )
        if cur.rowcount == 0:
            present += 1
            continue
        imported += 1
        conn.executemany(
            "INSERT OR IGNORE INTO model_backend (model_id, backend) VALUES (?, ?)",
            [(model_id, backend) for backend in model.backends],
        )
    return imported, present


def convert_registry_toml(*, job_id: str | None = None) -> dict[str, Any]:
    """Run the one-shot ``registry.toml`` conversion. Idempotent.

    Returns a summary dict: ``outcome`` is ``"absent"`` (no file),
    ``"imported"`` or ``"retired"`` (file archived without importing,
    because this database was already initialised from it), plus the
    counts, the ``backup`` directory and the ``archived_to`` path.

    Raises:
        RegistryConversionError: the backup, the parse or the rename failed.
            The file stays where it is, so the next run retries.
    """
    toml_file = paths.registry_dir() / _TOML_NAME
    lock_file = paths.registry_dir() / _LOCK_NAME
    db_file = paths.db_path()

    if not toml_file.exists():
        with connect(db_file) as conn:
            migrate(conn)
            if _marker(conn) is None:
                with tx(conn):
                    _write_marker(conn, {"outcome": "absent"})
        lock_file.unlink(missing_ok=True)
        return {"outcome": "absent"}

    stamp = _stamp()
    backup_dir = _backup(toml_file, db_file, stamp)
    models, invalid = _load(toml_file)

    imported = present = 0
    with connect(db_file) as conn:
        migrate(conn)
        with tx(conn):
            do_import = _marker(conn) is None
            if do_import:
                imported, present = _import(conn, models)
                _write_marker(
                    conn,
                    {
                        "outcome": "imported",
                        "imported": imported,
                        "already_present": present,
                        "invalid": invalid,
                        "backup": str(backup_dir),
                    },
                )

    suffix = "imported" if do_import else "retired"
    target = _free_path(toml_file.with_name(f"{_TOML_NAME}.{suffix}-{stamp}"))
    archived: Path | None = target
    try:
        toml_file.rename(target)
    except FileNotFoundError:
        # A concurrent run archived it first; its import (if any) committed
        # under the same marker, so there is nothing left to do.
        archived = None
    except OSError as exc:
        raise RegistryConversionError(
            f"converted {toml_file} but could not rename it: {exc}",
            details={"path": str(toml_file), "reason": str(exc), "backup_dir": str(backup_dir)},
        ) from exc
    lock_file.unlink(missing_ok=True)

    if invalid:
        log.error(
            "registry.toml_conversion.entries_skipped job_id=%s ids=%s backup=%s",
            job_id,
            ",".join(invalid),
            backup_dir,
        )
    result: dict[str, Any] = {
        "outcome": suffix,
        "imported": imported,
        "already_present": present,
        "invalid": invalid,
        "backup": str(backup_dir),
        "archived_to": str(archived) if archived else None,
    }
    log.info("registry.toml_conversion.done job_id=%s result=%s", job_id, result)
    return result


__all__ = ["MARKER", "RegistryConversionError", "convert_registry_toml"]
