"""One-shot, idempotent import: ``registry.toml`` → the SQLite ``model`` table.

Two entry points share :func:`import_toml_to_sqlite`:

* :meth:`hal0.registry.sqlite_store.SqliteModelRegistry._maybe_first_boot_import`
  — runs automatically the first time any process opens an empty
  registry database that still has a ``registry.toml`` sitting next to
  it (the ML-1 cutover path for existing installs).
* ``hal0 registry import-sqlite`` (``hal0.cli.registry_commands``) — the
  same operation, explicit and re-runnable at any time.

Both use ``INSERT OR IGNORE``, never ``REPLACE`` — a model id already
present in SQLite (including one edited directly after cutover) is left
untouched, so re-running this import can never clobber a live edit
(plan §8.3 step 3).
"""

from __future__ import annotations

import logging
import sqlite3
import tomllib
from dataclasses import dataclass
from pathlib import Path

from hal0.config import paths
from hal0.db import repository
from hal0.db.connection import connect, tx
from hal0.db.migrate import migrate
from hal0.registry.model import Model

log = logging.getLogger(__name__)


@dataclass
class ImportReport:
    """Outcome counters for one :func:`import_toml_to_sqlite` run."""

    imported: int = 0
    skipped_existing: int = 0
    skipped_invalid: int = 0


def _load_toml_models(registry_file: Path) -> tuple[dict[str, Model], int]:
    """Parse ``registry.toml`` the same way the TOML store's read path does.

    Malformed entries are logged and skipped rather than raised — mirrors
    ``TomlModelRegistry._read_locked``'s "never blank a working registry
    over one bad entry" behaviour, so an import inherits the same
    tolerance for partially-corrupt files.

    Returns the valid entries plus a count of entries skipped for
    failing ``Model`` validation (a missing/corrupt file yields
    ``({}, 0)``, not an exception).
    """
    try:
        with open(registry_file, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        log.warning("import_toml: cannot read %s: %s", registry_file, exc)
        return {}, 0

    raw = data.get("models", {}) if isinstance(data, dict) else {}
    out: dict[str, Model] = {}
    skipped_invalid = 0
    if isinstance(raw, dict):
        for mid, entry in raw.items():
            if not isinstance(entry, dict):
                skipped_invalid += 1
                continue
            try:
                out[mid] = Model.model_validate({**entry, "id": mid})
            except Exception as exc:
                log.warning("import_toml: entry %r failed validation: %s", mid, exc)
                skipped_invalid += 1
    return out, skipped_invalid


def _import_into(conn: sqlite3.Connection, models: dict[str, Model], report: ImportReport) -> None:
    migrate(conn)
    with tx(conn):
        for model_id, model in models.items():
            row = repository.model_to_row(model)
            columns = ", ".join(row)
            placeholders = ", ".join("?" for _ in row)
            cur = conn.execute(
                f"INSERT OR IGNORE INTO model ({columns}) VALUES ({placeholders})",
                list(row.values()),
            )
            if cur.rowcount == 0:
                report.skipped_existing += 1
                continue
            report.imported += 1
            conn.executemany(
                "INSERT OR IGNORE INTO model_backend (model_id, backend) VALUES (?, ?)",
                [(model_id, backend) for backend in model.backends],
            )


def import_toml_to_sqlite(
    *,
    registry_file: Path | None = None,
    db_path: Path | None = None,
    conn: sqlite3.Connection | None = None,
) -> ImportReport:
    """Idempotently copy every valid ``registry.toml`` entry into SQLite.

    Args:
        registry_file: Source TOML file. Defaults to
            ``paths.registry_dir() / "registry.toml"``.
        db_path: Target database path, used to open a fresh connection
            when ``conn`` is not supplied. Defaults to
            :func:`hal0.db.connection.db_path`.
        conn: Reuse an already-open connection (the first-boot path does
            this to avoid a second connection to the same database mid-
            startup) instead of opening a new one.

    Returns:
        Counts of imported / already-present / invalid-and-skipped
        entries. A missing source file returns all-zero counts rather
        than raising — there is nothing to import.
    """
    rfile = registry_file if registry_file is not None else (paths.registry_dir() / "registry.toml")
    report = ImportReport()
    if not rfile.exists():
        return report

    models, skipped_invalid = _load_toml_models(rfile)
    report.skipped_invalid = skipped_invalid
    if not models:
        return report

    if conn is not None:
        _import_into(conn, models, report)
    else:
        with connect(db_path) as owned_conn:
            _import_into(owned_conn, models, report)
    return report


__all__ = ["ImportReport", "import_toml_to_sqlite"]
