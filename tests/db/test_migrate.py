"""Unit tests for hal0.db.migrate.

Covers:
  * Idempotency — calling migrate() twice applies nothing the second time.
  * Forward-only ordering — migrations apply in ascending version order.
  * Partial-apply resilience — a database that already has some versions
    applied only picks up the remaining ones.
  * The real package migration (001_registry.sql) actually creates the
    tables the ML-1 registry depends on.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from hal0.db.connection import connect
from hal0.db.migrate import applied_versions, migrate


def _write_migration(migrations_dir: Path, version: int, sql: str) -> None:
    migrations_dir.mkdir(parents=True, exist_ok=True)
    (migrations_dir / f"{version:03d}_test.sql").write_text(sql)


class TestMigrate:
    def test_applies_pending_migrations_in_order(self, tmp_path: Path) -> None:
        migrations_dir = tmp_path / "migrations"
        _write_migration(migrations_dir, 1, "CREATE TABLE a (id INTEGER PRIMARY KEY);")
        _write_migration(migrations_dir, 2, "CREATE TABLE b (id INTEGER PRIMARY KEY);")

        with connect(tmp_path / "t.db") as conn:
            applied = migrate(conn, migrations_dir)
            assert applied == [1, 2]
            assert applied_versions(conn) == {1, 2}
            # Both tables actually exist.
            conn.execute("INSERT INTO a DEFAULT VALUES")
            conn.execute("INSERT INTO b DEFAULT VALUES")

    def test_idempotent_second_call_applies_nothing(self, tmp_path: Path) -> None:
        migrations_dir = tmp_path / "migrations"
        _write_migration(migrations_dir, 1, "CREATE TABLE a (id INTEGER PRIMARY KEY);")

        with connect(tmp_path / "t.db") as conn:
            first = migrate(conn, migrations_dir)
            second = migrate(conn, migrations_dir)
            assert first == [1]
            assert second == []

    def test_partial_apply_only_picks_up_remaining(self, tmp_path: Path) -> None:
        migrations_dir = tmp_path / "migrations"
        _write_migration(migrations_dir, 1, "CREATE TABLE a (id INTEGER PRIMARY KEY);")

        db = tmp_path / "t.db"
        with connect(db) as conn:
            migrate(conn, migrations_dir)

        # A second version appears later (simulates a new release landing
        # a second migration file after 001 was already applied in
        # production).
        _write_migration(migrations_dir, 2, "CREATE TABLE b (id INTEGER PRIMARY KEY);")

        with connect(db) as conn:
            applied = migrate(conn, migrations_dir)
            assert applied == [2]
            assert applied_versions(conn) == {1, 2}

    def test_migration_runs_inside_one_transaction(self, tmp_path: Path) -> None:
        """A failing statement mid-file leaves nothing committed — no
        partial schema, no row recorded in schema_migrations."""
        migrations_dir = tmp_path / "migrations"
        _write_migration(
            migrations_dir,
            1,
            "CREATE TABLE a (id INTEGER PRIMARY KEY);\nNOT VALID SQL HERE;",
        )

        with connect(tmp_path / "t.db") as conn:
            with contextlib.suppress(Exception):
                migrate(conn, migrations_dir)
            assert applied_versions(conn) == set()
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='a'"
            ).fetchall()
            assert tables == []

    def test_forward_only_never_reapplies_lower_version(self, tmp_path: Path) -> None:
        migrations_dir = tmp_path / "migrations"
        _write_migration(migrations_dir, 5, "CREATE TABLE five (id INTEGER PRIMARY KEY);")

        with connect(tmp_path / "t.db") as conn:
            migrate(conn, migrations_dir)
            # Re-running migrate with the same file set is a no-op even
            # though nothing here ever "moves backward" — there is no
            # down-migration concept at all.
            assert migrate(conn, migrations_dir) == []


class TestPackagedRegistryMigration:
    """The real 001_registry.sql shipped in the package, applied via the
    default (non-test) migrations_dir=None path."""

    def test_001_registry_creates_expected_tables(self, tmp_path: Path) -> None:
        with connect(tmp_path / "t.db") as conn:
            applied = migrate(conn)
            assert 1 in applied
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert {"model", "model_file", "model_backend", "schema_migrations"} <= tables

    def test_001_registry_is_idempotent_against_the_real_package(self, tmp_path: Path) -> None:
        with connect(tmp_path / "t.db") as conn:
            migrate(conn)
            assert migrate(conn) == []
