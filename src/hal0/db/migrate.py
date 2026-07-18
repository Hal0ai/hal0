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
"""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

from hal0.db.connection import tx

_MIGRATIONS_PACKAGE = "hal0.db.migrations"
_MIGRATION_RE = re.compile(r"^(\d+)_.+\.sql$")
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")


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

    Returns the list of newly applied version numbers (empty if the
    database was already current).
    """
    _ensure_migrations_table(conn)
    newly_applied: list[int] = []
    for version, _filename, sql_text in _discover_migrations(migrations_dir):
        if version in applied_versions(conn):
            continue
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
