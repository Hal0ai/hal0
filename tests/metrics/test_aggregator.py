"""aggregate_hour() -- idempotent hourly rollup of request_metric + slot_sample."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from hal0.db.connection import connect
from hal0.db.migrate import migrate
from hal0.metrics.aggregator import aggregate_hour


def _seed_request_metric(conn, ts: str, **overrides) -> None:
    row = {
        "ts": ts,
        "request_id": "r",
        "slot_id": "primary",
        "model_id": "qwen3-4b",
        "runner": "rocm",
        "device": "gpu-rocm",
        "modality": "chat",
        "ok": 1,
        "ttft_ms": 100.0,
        "prefill_tps": 1000.0,
        "decode_tps": 40.0,
        "spec_accept_rate": 0.5,
    }
    row.update(overrides)
    cols = ",".join(row.keys())
    placeholders = ",".join("?" for _ in row)
    conn.execute(
        f"INSERT INTO request_metric ({cols}) VALUES ({placeholders})", tuple(row.values())
    )


class TestAggregateHour:
    def test_groups_by_model_runner_device_modality(self, tmp_path: Path) -> None:
        db = tmp_path / "t.db"
        bucket = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
        with connect(db) as conn:
            migrate(conn)
            _seed_request_metric(conn, "2026-01-01T10:05:00+00:00", decode_tps=40.0)
            _seed_request_metric(conn, "2026-01-01T10:10:00+00:00", decode_tps=60.0)
            written = aggregate_hour(conn, bucket)
            assert written == 1  # one (model,runner,device,modality) group

            row = conn.execute(
                "SELECT * FROM metric_rollup WHERE dim_kind='request_hourly'"
            ).fetchone()
            assert row["count"] == 2
            assert row["ok_count"] == 2
            assert row["tps_decode_avg"] == 50.0

    def test_out_of_window_rows_excluded(self, tmp_path: Path) -> None:
        db = tmp_path / "t.db"
        bucket = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
        with connect(db) as conn:
            migrate(conn)
            _seed_request_metric(conn, "2026-01-01T09:59:59+00:00")  # before window
            _seed_request_metric(conn, "2026-01-01T11:00:00+00:00")  # at/after window end
            _seed_request_metric(conn, "2026-01-01T10:30:00+00:00")  # in window
            aggregate_hour(conn, bucket)
            row = conn.execute(
                "SELECT * FROM metric_rollup WHERE dim_kind='request_hourly'"
            ).fetchone()
            assert row["count"] == 1

    def test_idempotent_rerun_same_hour(self, tmp_path: Path) -> None:
        db = tmp_path / "t.db"
        bucket = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
        with connect(db) as conn:
            migrate(conn)
            _seed_request_metric(conn, "2026-01-01T10:05:00+00:00")
            aggregate_hour(conn, bucket)
            aggregate_hour(conn, bucket)
            count = conn.execute(
                "SELECT COUNT(*) FROM metric_rollup WHERE dim_kind='request_hourly'"
            ).fetchone()[0]
            assert count == 1  # INSERT OR REPLACE keyed on (bucket, dim_kind, dim_key)

    def test_slot_sample_hourly_rollup(self, tmp_path: Path) -> None:
        db = tmp_path / "t.db"
        bucket = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
        with connect(db) as conn:
            migrate(conn)
            conn.execute(
                "INSERT INTO slot_sample (ts, slot_id, state, vram_bytes, power_w) "
                "VALUES ('2026-01-01T10:01:00+00:00', 'primary', 'serving', 1000, 40.0)"
            )
            conn.execute(
                "INSERT INTO slot_sample (ts, slot_id, state, vram_bytes, power_w) "
                "VALUES ('2026-01-01T10:02:00+00:00', 'primary', 'serving', 2000, 60.0)"
            )
            aggregate_hour(conn, bucket)
            row = conn.execute(
                "SELECT * FROM metric_rollup WHERE dim_kind='slot_sample_hourly'"
            ).fetchone()
            assert row["count"] == 2
            assert row["vram_bytes_avg"] == 1500
            assert row["power_w_avg"] == 50.0
