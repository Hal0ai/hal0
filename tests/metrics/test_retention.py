"""prune() -- bounded storage: raw tables age out, rollup survives longer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from hal0.db.connection import connect
from hal0.db.migrate import migrate
from hal0.metrics.retention import prune


def _iso(days_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()


class TestPrune:
    def test_prunes_old_request_metric_rows(self, tmp_path: Path) -> None:
        db = tmp_path / "t.db"
        with connect(db) as conn:
            migrate(conn)
            conn.execute(
                "INSERT INTO request_metric (ts, request_id, ok) VALUES (?, 'old', 1)",
                (_iso(10),),
            )
            conn.execute(
                "INSERT INTO request_metric (ts, request_id, ok) VALUES (?, 'new', 1)",
                (_iso(1),),
            )
            deleted = prune(conn, request_days=7)
            assert deleted["request_metric"] == 1
            remaining = conn.execute("SELECT request_id FROM request_metric").fetchall()
            assert [r[0] for r in remaining] == ["new"]

    def test_prunes_old_slot_sample_rows_on_a_shorter_window(self, tmp_path: Path) -> None:
        db = tmp_path / "t.db"
        with connect(db) as conn:
            migrate(conn)
            conn.execute(
                "INSERT INTO slot_sample (ts, slot_id, state) VALUES (?, 'primary', 'ready')",
                (_iso(5),),
            )
            conn.execute(
                "INSERT INTO slot_sample (ts, slot_id, state) VALUES (?, 'primary', 'ready')",
                (_iso(1),),
            )
            deleted = prune(conn, slot_sample_days=3)
            assert deleted["slot_sample"] == 1

    def test_metric_rollup_survives_past_raw_retention(self, tmp_path: Path) -> None:
        db = tmp_path / "t.db"
        with connect(db) as conn:
            migrate(conn)
            # A rollup bucket well within the raw-table retention window,
            # but the raw table itself has already been pruned -- the
            # rollup is what's meant to survive long retention.
            conn.execute(
                "INSERT INTO metric_rollup (bucket, dim_kind, dim_key, count) "
                "VALUES (?, 'request_hourly', '{}', 5)",
                (_iso(30),),
            )
            deleted = prune(conn, request_days=7, rollup_days=90)
            assert deleted["metric_rollup"] == 0
            count = conn.execute("SELECT COUNT(*) FROM metric_rollup").fetchone()[0]
            assert count == 1

    def test_rollup_pruned_past_its_own_retention(self, tmp_path: Path) -> None:
        db = tmp_path / "t.db"
        with connect(db) as conn:
            migrate(conn)
            conn.execute(
                "INSERT INTO metric_rollup (bucket, dim_kind, dim_key, count) "
                "VALUES (?, 'request_hourly', '{}', 5)",
                (_iso(120),),
            )
            deleted = prune(conn, rollup_days=90)
            assert deleted["metric_rollup"] == 1

    def test_prune_is_idempotent(self, tmp_path: Path) -> None:
        db = tmp_path / "t.db"
        with connect(db) as conn:
            migrate(conn)
            conn.execute(
                "INSERT INTO request_metric (ts, request_id, ok) VALUES (?, 'old', 1)",
                (_iso(10),),
            )
            first = prune(conn, request_days=7)
            second = prune(conn, request_days=7)
            assert first["request_metric"] == 1
            assert second["request_metric"] == 0
