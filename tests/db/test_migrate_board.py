"""005_board.sql lands cleanly on top of 001-004 and seeds the frozen lanes.

Run targeted:
    PYTHONPATH=src .venv/bin/python -m pytest tests/db/test_migrate_board.py -q
"""

from __future__ import annotations

from pathlib import Path

from hal0.db.connection import connect
from hal0.db.migrate import applied_versions, migrate


def _db(tmp_path: Path) -> Path:
    return tmp_path / "hal0.db"


def test_migrate_applies_version_5(tmp_path: Path) -> None:
    with connect(_db(tmp_path)) as conn:
        applied = migrate(conn)
        assert 5 in applied
        assert 5 in applied_versions(conn)


def test_board_tables_exist(tmp_path: Path) -> None:
    with connect(_db(tmp_path)) as conn:
        migrate(conn)
        names = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    for table in (
        "board",
        "board_column",
        "card",
        "card_comment",
        "card_link",
        "card_run",
        "card_event",
        "board_orchestration",
        "board_profile",
    ):
        assert table in names, table


def test_columns_seeded_in_frozen_order(tmp_path: Path) -> None:
    with connect(_db(tmp_path)) as conn:
        migrate(conn)
        rows = conn.execute(
            "SELECT status, label, visible FROM board_column ORDER BY position"
        ).fetchall()
    statuses = [r["status"] for r in rows]
    assert statuses == [
        "triage",
        "todo",
        "scheduled",
        "ready",
        "running",
        "blocked",
        "review",
        "done",
        "archived",
    ]
    labels = {r["status"]: r["label"] for r in rows}
    assert labels["running"] == "in-progress"
    visible = {r["status"]: bool(r["visible"]) for r in rows}
    assert visible["archived"] is False
    assert visible["todo"] is True


def test_orchestration_singleton_seeded(tmp_path: Path) -> None:
    with connect(_db(tmp_path)) as conn:
        migrate(conn)
        row = conn.execute("SELECT * FROM board_orchestration WHERE id = 1").fetchone()
    assert row["tick_interval"] == 5
    assert row["failure_limit"] == 3
    assert row["claim_ttl"] == 600
    assert row["max_in_flight"] == 4


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    db = _db(tmp_path)
    with connect(db) as conn:
        migrate(conn)
    with connect(db) as conn:
        # Second boot applies nothing new and does not re-run the seeds.
        assert migrate(conn) == []
        n = conn.execute("SELECT COUNT(*) AS n FROM board_column").fetchone()["n"]
    assert n == 9


def test_card_status_foreign_key_enforced(tmp_path: Path) -> None:
    """card.status FKs board_column — an unknown lane can't be written."""
    import sqlite3

    import pytest

    with connect(_db(tmp_path)) as conn:
        migrate(conn)
        conn.execute("INSERT INTO board (slug, name) VALUES ('default', 'd')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO card (id, board_slug, status) VALUES ('t_x', 'default', 'bogus')"
            )
