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
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from hal0.db.connection import connect
from hal0.db.migrate import _has_retired_tuning, applied_versions, migrate


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


class TestPackagedStoreMigration:
    """003_store.sql (ML-3) — deliberately "003" not "002" (OBS-1 owns 002,
    in flight on a sibling branch); must apply cleanly on top of 001 and
    add the refcounted ``store_blob`` table without touching ``model_file``.
    """

    def test_003_store_applies_on_top_of_001(self, tmp_path: Path) -> None:
        with connect(tmp_path / "t.db") as conn:
            applied = migrate(conn)
            assert 1 in applied
            assert 3 in applied
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "store_blob" in tables
            # model_file (ML-1, empty) is untouched by the store migration.
            assert conn.execute("SELECT COUNT(*) FROM model_file").fetchone()[0] == 0

    def test_003_store_blob_refcount_roundtrip(self, tmp_path: Path) -> None:
        with connect(tmp_path / "t.db") as conn:
            migrate(conn)
            conn.execute(
                "INSERT INTO store_blob (sha256, size_bytes, blob_path, refcount, created_at) "
                "VALUES ('abc123', 100, '/store/blobs/abc123', 1, '2026-01-01T00:00:00')"
            )
            row = conn.execute("SELECT refcount FROM store_blob WHERE sha256='abc123'").fetchone()
            assert row["refcount"] == 1

    def test_migration_is_idempotent_against_the_real_package(self, tmp_path: Path) -> None:
        with connect(tmp_path / "t.db") as conn:
            migrate(conn)
            assert migrate(conn) == []


class _PeerFinishes015(sqlite3.Connection):
    """Runs ``peer_drop`` right after the first read whose SQL holds ``trigger``.

    The read is fetched first: a statement only opens its snapshot when stepped.
    """

    trigger = ""
    peer_drop: Callable[[], None] | None = None

    def execute(self, sql: str, *args: Any) -> Any:
        if self.peer_drop is None or self.trigger not in sql:
            return super().execute(sql, *args)
        rows = super().execute(sql, *args).fetchall()
        peer_drop, self.peer_drop = self.peer_drop, None
        peer_drop()
        return iter(rows)


class TestRetiredTuningCheckRace:
    @pytest.mark.parametrize(
        ("trigger", "drop", "expected"),
        [
            ("sqlite_master", "DROP TABLE legacy_model_tuning", False),
            ("table_info", "ALTER TABLE model DROP COLUMN preferred_runner", True),
        ],
    )
    def test_a_peer_finishing_015_mid_check_cannot_crash_it(
        self, tmp_path: Path, trigger: str, drop: str, expected: bool
    ) -> None:
        db = tmp_path / "hal0.db"
        with connect(db) as setup:
            setup.execute("CREATE TABLE model (id TEXT, preferred_runner TEXT)")
            setup.execute("CREATE TABLE legacy_model_tuning (id TEXT)")
            if expected:
                setup.execute("INSERT INTO model VALUES ('m', 'standard')")

        def peer_drop() -> None:
            with connect(db) as peer:
                peer.execute(drop)

        conn = sqlite3.connect(db, isolation_level=None, factory=_PeerFinishes015)
        conn.trigger = trigger
        conn.peer_drop = peer_drop
        try:
            assert _has_retired_tuning(conn) is expected
            assert conn.peer_drop is None
            assert not conn.in_transaction
        finally:
            conn.close()

    def test_the_check_leaves_a_callers_transaction_open(self, tmp_path: Path) -> None:
        with connect(tmp_path / "hal0.db") as conn:
            conn.execute("BEGIN")
            assert _has_retired_tuning(conn) is False
            assert conn.in_transaction
            conn.execute("ROLLBACK")

    def test_a_read_error_after_an_automatic_rollback_is_not_masked(self, tmp_path: Path) -> None:
        class _IoErrorMidCheck(sqlite3.Connection):
            def execute(self, sql: str, *args: Any) -> Any:
                if "table_info" in sql:
                    super().execute("ROLLBACK")  # what SQLite does on an I/O error
                    raise sqlite3.OperationalError("disk I/O error")
                return super().execute(sql, *args)

        db = tmp_path / "hal0.db"
        with connect(db) as setup:
            setup.execute("CREATE TABLE model (id TEXT)")

        conn = sqlite3.connect(db, isolation_level=None, factory=_IoErrorMidCheck)
        try:
            with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
                _has_retired_tuning(conn)
        finally:
            conn.close()
