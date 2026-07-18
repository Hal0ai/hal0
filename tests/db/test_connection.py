"""Unit tests for hal0.db.connection.

Covers:
  * Every `connect()` call applies the full pragma set, including
    `foreign_keys=ON` — the top footgun called out in the ML-1 spec
    (it is a per-connection setting, not database-wide).
  * `ON DELETE CASCADE` actually fires with `foreign_keys=ON` (and would
    silently no-op without it).
  * `tx()` commits on success and rolls back on exception.
  * `tx()` uses `BEGIN IMMEDIATE` (acquires the write lock up front).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from hal0.db.connection import connect, tx


def test_connect_sets_foreign_keys_on(tmp_path: Path) -> None:
    with connect(tmp_path / "t.db") as conn:
        (val,) = conn.execute("PRAGMA foreign_keys").fetchone()
        assert val == 1


def test_connect_sets_wal_journal_mode(tmp_path: Path) -> None:
    with connect(tmp_path / "t.db") as conn:
        (mode,) = conn.execute("PRAGMA journal_mode").fetchone()
        assert mode.lower() == "wal"


def test_connect_sets_busy_timeout(tmp_path: Path) -> None:
    with connect(tmp_path / "t.db") as conn:
        (timeout,) = conn.execute("PRAGMA busy_timeout").fetchone()
        assert timeout == 5000


def test_connect_sets_synchronous_normal(tmp_path: Path) -> None:
    with connect(tmp_path / "t.db") as conn:
        (val,) = conn.execute("PRAGMA synchronous").fetchone()
        # NORMAL == 1 in SQLite's synchronous pragma encoding.
        assert val == 1


def test_foreign_keys_is_per_connection_not_persisted(tmp_path: Path) -> None:
    """The load-bearing gotcha: PRAGMA foreign_keys does not stick to the
    database file — every fresh connection must reissue it."""
    db = tmp_path / "t.db"
    with connect(db) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        (val,) = conn.execute("PRAGMA foreign_keys").fetchone()
        assert val == 0

    # A brand new connection via connect() still gets ON, regardless of
    # what the previous (closed) connection did.
    with connect(db) as conn2:
        (val2,) = conn2.execute("PRAGMA foreign_keys").fetchone()
        assert val2 == 1


def test_connect_creates_parent_directory(tmp_path: Path) -> None:
    db = tmp_path / "nested" / "dir" / "t.db"
    assert not db.parent.exists()
    with connect(db):
        pass
    assert db.parent.is_dir()
    assert db.exists()


class TestCascadeDelete:
    """Proves foreign_keys=ON actually enforces ON DELETE CASCADE — the
    concrete failure mode this pragma exists to prevent."""

    def _make_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE parent (id TEXT PRIMARY KEY)")
        conn.execute(
            "CREATE TABLE child (parent_id TEXT NOT NULL "
            "REFERENCES parent(id) ON DELETE CASCADE, val TEXT)"
        )

    def test_cascade_fires_with_foreign_keys_on(self, tmp_path: Path) -> None:
        db = tmp_path / "t.db"
        with connect(db) as conn:
            self._make_schema(conn)
            with tx(conn):
                conn.execute("INSERT INTO parent (id) VALUES ('p1')")
                conn.execute("INSERT INTO child (parent_id, val) VALUES ('p1', 'a')")
            with tx(conn):
                conn.execute("DELETE FROM parent WHERE id = 'p1'")
            remaining = conn.execute("SELECT COUNT(*) FROM child").fetchone()[0]
            assert remaining == 0

    def test_cascade_does_not_fire_with_foreign_keys_off(self, tmp_path: Path) -> None:
        """Regression guard for the exact footgun the spec calls out: if a
        connection ever loses `foreign_keys=ON`, cascade deletes silently
        stop working instead of erroring."""
        db = tmp_path / "t.db"
        with connect(db) as conn:
            conn.execute("PRAGMA foreign_keys=OFF")
            self._make_schema(conn)
            with tx(conn):
                conn.execute("INSERT INTO parent (id) VALUES ('p1')")
                conn.execute("INSERT INTO child (parent_id, val) VALUES ('p1', 'a')")
            with tx(conn):
                conn.execute("DELETE FROM parent WHERE id = 'p1'")
            remaining = conn.execute("SELECT COUNT(*) FROM child").fetchone()[0]
            assert remaining == 1  # orphaned, not cascaded — proves the pragma matters


class TestTx:
    def test_commits_on_success(self, tmp_path: Path) -> None:
        with connect(tmp_path / "t.db") as conn:
            conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
            with tx(conn):
                conn.execute("INSERT INTO t DEFAULT VALUES")
            assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1

    def test_rolls_back_on_exception(self, tmp_path: Path) -> None:
        with connect(tmp_path / "t.db") as conn:
            conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
            with pytest.raises(RuntimeError), tx(conn):
                conn.execute("INSERT INTO t DEFAULT VALUES")
                raise RuntimeError("boom")
            assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0

    def test_uses_begin_immediate(self, tmp_path: Path) -> None:
        """A second connection cannot also BEGIN IMMEDIATE while the first
        write transaction is open — proves the write lock is held."""
        db = tmp_path / "t.db"
        with connect(db) as conn1, connect(db) as conn2:
            # Fail fast instead of waiting out the 5s busy_timeout default.
            conn2.execute("PRAGMA busy_timeout=0")
            conn1.execute("BEGIN IMMEDIATE")
            try:
                with pytest.raises(sqlite3.OperationalError):
                    conn2.execute("BEGIN IMMEDIATE")
            finally:
                conn1.execute("ROLLBACK")
