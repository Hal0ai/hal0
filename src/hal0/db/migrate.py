"""Forward-only SQLite schema migration runner.

This is the concrete implementation of the job the (no-op) config-migration
framework was standing in for (tracker P1-migfw): a real, idempotent,
forward-only schema runner for the ``db/`` foundation's single SQLite file.

Migrations are plain ``.sql`` files shipped *inside the package* at
``hal0/db/migrations/NNN_name.sql`` — never read from a runtime
``/var/lib`` path, so the set of migrations a given build applies always
matches the installed code. Each file is applied inside its own
transaction (:func:`hal0.db.connection.tx`) and recorded in
``schema_migrations``, so calling :func:`migrate` on every boot is a cheap
no-op once a given version has landed. There are no down-migrations —
schema changes only move forward.

One migration is irreversible in a way a rollback cannot paper over: 015 drops
the retired ADR-0029 model tuning. :func:`migrate` is the one place every path
into it goes through (the registry store, the slot identity table and the port
authority all call it on first use, so the updater would be too late), so the
guard that snapshots the database first lives here.
"""

from __future__ import annotations

import contextlib
import os
import re
import sqlite3
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

import structlog

from hal0.db.connection import tx

log = structlog.get_logger(__name__)

_MIGRATIONS_PACKAGE = "hal0.db.migrations"
_MIGRATION_RE = re.compile(r"^(\d+)_.+\.sql$")
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")

#: The file :func:`_backup_before_tuning_drop` guards. Matched by name, not by
#: version: the bridge store runs its own numbered migrations through
#: :func:`migrate` against a different database.
_TUNING_DROP_MIGRATION = "015_drop_retired_model_tuning.sql"
_TUNING_HOLDING_TABLE = "legacy_model_tuning"
_RETIRED_TUNING_COLUMNS = (
    "profile",
    "extra_args",
    "n_gpu_layers",
    "context_size",
    "rope_freq_base",
    "preferred_runner",
)
_RETIRED_TUNING_EXTRA_KEYS = ("$._enable_thinking", "$._vision")


class MigrationBackupError(RuntimeError):
    """A migration that destroys data was not applied: its backup failed."""


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    """Create the bookkeeping table if absent. Idempotent."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )


def applied_versions(conn: sqlite3.Connection) -> set[int]:
    """Return the set of migration version numbers already applied."""
    _ensure_migrations_table(conn)
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {int(row[0]) for row in rows}


def _split_statements(sql_text: str) -> list[str]:
    """Split a migration file into individual statements on ``;``.

    ``sqlite3.Cursor.executescript`` is deliberately NOT used here: it
    issues an implicit ``COMMIT`` before running, which would tear down
    the ``BEGIN IMMEDIATE`` transaction :func:`hal0.db.connection.tx`
    just opened. Line comments (``-- ...``) are stripped first so a
    semicolon inside a comment (e.g. "-- Model.path; entry point") can't
    masquerade as a statement boundary — the naive split is otherwise
    safe for migrations we author ourselves (plain DDL, no string
    literals containing semicolons).
    """
    without_comments = _LINE_COMMENT_RE.sub("", sql_text)
    return [stmt.strip() for stmt in without_comments.split(";") if stmt.strip()]


def _discover_migrations(migrations_dir: Path | None) -> list[tuple[int, str, str]]:
    """Return ``(version, filename, sql_text)`` triples, sorted by version.

    Reads from ``migrations_dir`` when given (tests point this at a tmp
    fixture directory); otherwise reads the package data shipped inside
    ``hal0.db.migrations`` via :mod:`importlib.resources`.
    """
    found: list[tuple[int, str, str]] = []
    if migrations_dir is not None:
        for path in sorted(Path(migrations_dir).glob("*.sql")):
            match = _MIGRATION_RE.match(path.name)
            if not match:
                continue
            found.append((int(match.group(1)), path.name, path.read_text()))
    else:
        base = resources.files(_MIGRATIONS_PACKAGE)
        for entry in base.iterdir():
            if not entry.name.endswith(".sql"):
                continue
            match = _MIGRATION_RE.match(entry.name)
            if not match:
                continue
            found.append((int(match.group(1)), entry.name, entry.read_text()))
    found.sort(key=lambda triple: triple[0])
    return found


def available_versions(migrations_dir: Path | None = None) -> list[int]:
    """Return the migration versions a build ships, ascending.

    The same discovery :func:`migrate` applies (``migrations_dir`` when given,
    else the package's own), so a caller that asks "is this database current?"
    (``hal0 doctor all``'s ``bridge-store`` row) cannot disagree with the runner
    about what "current" is.
    """
    return [version for version, _name, _sql in _discover_migrations(migrations_dir)]


def _has_retired_tuning(conn: sqlite3.Connection) -> bool:
    """True when migration 015 would destroy a value, not just empty structure.

    An empty holding table holds nothing, which is also what a brand-new
    database looks like when 012 and 015 run in one pass. Reads the live schema
    rather than assuming it, because a second process can finish 015 between the
    caller's "still pending?" check and this query.
    """
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "model" not in tables:
        return False
    if (
        _TUNING_HOLDING_TABLE in tables
        and conn.execute(f"SELECT 1 FROM {_TUNING_HOLDING_TABLE} LIMIT 1").fetchone()
    ):
        return True
    columns = {row[1] for row in conn.execute("PRAGMA table_info(model)")}
    conditions = [f"{c} IS NOT NULL" for c in _RETIRED_TUNING_COLUMNS if c in columns]
    if "extra" in columns:
        conditions += [
            f"(json_valid(extra) AND json_type(extra, '{key}') IS NOT NULL)"
            for key in _RETIRED_TUNING_EXTRA_KEYS
        ]
    if not conditions:
        return False
    return (
        conn.execute(f"SELECT 1 FROM model WHERE {' OR '.join(conditions)} LIMIT 1").fetchone()
        is not None
    )


def _reserve_backup_file(backup_dir: Path) -> Path:
    """Create an empty ``0600`` backup file with a name nothing else holds.

    Reserving first means the snapshot is never world-readable, even briefly
    (``VACUUM INTO`` writes into an existing empty file and keeps its mode), and
    two processes racing the same migration cannot collide on one name.
    """
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    n = 0
    while True:
        suffix = f"-{n}" if n else ""
        target = backup_dir / f"hal0-pre-015-{stamp}{suffix}.db"
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            n += 1
            continue
        os.close(fd)
        return target


def _vacuum_into(conn: sqlite3.Connection, target: Path) -> None:
    """Write a consistent snapshot of ``conn``'s database to ``target``."""
    conn.execute("VACUUM INTO ?", (str(target),))


def _backup_before_tuning_drop(conn: sqlite3.Connection) -> Path | None:
    """Snapshot the database when migration 015 would destroy tuning values.

    On 1.7.x a slot the converter blocked keeps its tune only on the model row
    and in the holding table, so 015 can delete the last copy. The converter
    never cleared the model columns of a converted model either, so this also
    fires once on a converted box whose models once carried a tune. It is a
    no-op when there is nothing to lose (a new database, a clean model table).

    Raises :class:`MigrationBackupError` when the snapshot cannot be written,
    before the migration has touched anything. Returns the snapshot's path, or
    ``None`` when none was needed.
    """
    main = next((row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main"), "")
    if not main or not _has_retired_tuning(conn):  # an in-memory database has no file to lose
        return None

    from hal0.config import paths
    from hal0.config.migrations.runner_system import _backups_dir

    target: Path | None = None
    try:
        target = _reserve_backup_file(_backups_dir())
        _vacuum_into(conn, target)
        with target.open("rb") as snapshot:
            os.fsync(snapshot.fileno())
        if os.geteuid() == 0:
            # A root-run tool got here first. The snapshot belongs to whoever
            # owns the database, so hal0 can restore from it.
            db_stat = os.stat(main)
            os.chown(target, db_stat.st_uid, db_stat.st_gid)
    except (OSError, sqlite3.Error) as exc:
        if target is not None:
            with contextlib.suppress(OSError):
                target.unlink(missing_ok=True)
        where = target if target is not None else paths.var_lib() / "backups"
        raise MigrationBackupError(
            f"migration 015 was not applied: it permanently drops retired model tuning still "
            f"in {main}, and the backup at {where} could not be written ({exc}). The database "
            "is unchanged. Fix: free disk space there, or run `sudo hal0 doctor perms --fix`, "
            "then restart hal0-api"
        ) from exc
    log.warning("db.pre_migration_backup_written", migration=15, path=str(target))
    return target


def migrate(conn: sqlite3.Connection, migrations_dir: Path | None = None) -> list[int]:
    """Apply every not-yet-applied migration, in ascending version order.

    Safe to call on every process boot — already-applied versions are
    skipped via ``schema_migrations``. Each migration file runs inside its
    own :func:`hal0.db.connection.tx`, so a mid-file failure leaves the
    prior schema state intact and nothing partially recorded as applied.

    Concurrency note: the "already applied?" check happens twice — once
    before acquiring the write lock (cheap short-circuit for the common
    case) and once more *after* ``BEGIN IMMEDIATE`` succeeds, since a
    second connection racing to migrate the same fresh database (e.g.
    several ``SqliteModelRegistry`` instances constructed concurrently)
    could otherwise both decide "not applied yet" before either takes the
    lock, and the second would then re-run ``CREATE TABLE`` and fail.

    Migration 015 is preceded by :func:`_backup_before_tuning_drop`, outside
    the transaction because ``VACUUM INTO`` cannot run inside one. A failed
    backup raises :class:`MigrationBackupError` and applies nothing further.

    Returns the list of newly applied version numbers (empty if the
    database was already current).
    """
    _ensure_migrations_table(conn)
    newly_applied: list[int] = []
    for version, filename, sql_text in _discover_migrations(migrations_dir):
        if version in applied_versions(conn):
            continue
        if filename == _TUNING_DROP_MIGRATION:
            _backup_before_tuning_drop(conn)
        with tx(conn):
            # Re-check under the write lock: another connection may have
            # applied this exact version while we were waiting for it.
            if version in applied_versions(conn):
                continue
            for statement in _split_statements(sql_text):
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, datetime.now(UTC).isoformat()),
            )
        newly_applied.append(version)
    return newly_applied
