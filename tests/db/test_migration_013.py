"""Migration 013: ``draft`` / ``draft_kind`` columns on ``model`` (ADR-0031)."""

from __future__ import annotations

from pathlib import Path

from hal0.db.connection import connect
from hal0.db.migrate import migrate


def test_013_adds_the_draft_columns(tmp_path: Path) -> None:
    with connect(tmp_path / "t.db") as conn:
        applied = migrate(conn)
        assert 13 in applied
        cols = {row[1] for row in conn.execute("PRAGMA table_info(model)")}
        assert {"draft", "draft_kind"} <= cols
        assert migrate(conn) == []
