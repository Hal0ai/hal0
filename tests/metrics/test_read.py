"""hal0.metrics.read -- system_stats / stats_summary / models_health contracts.

Every function must degrade to an empty/zeroed shape on a fresh
(unmigrated) DB -- the "works with the stack off" requirement (plan
§13.1) -- and return the documented shape once seeded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hal0.db.connection import connect
from hal0.db.migrate import migrate
from hal0.metrics import read as metrics_read


class TestSystemStatsDegradesGracefully:
    def test_unmigrated_db_returns_empty_shape(self, tmp_path: Path) -> None:
        # No migrate() call at all -- the file doesn't even exist yet.
        out = metrics_read.system_stats(tmp_path / "fresh.db")
        assert out == {"ts": None, "fleet": {}, "slots": []}

    def test_migrated_but_empty_db_returns_empty_shape(self, tmp_path: Path) -> None:
        db = tmp_path / "t.db"
        with connect(db) as conn:
            migrate(conn)
        out = metrics_read.system_stats(db)
        assert out["fleet"] == {}
        assert out["slots"] == []


class TestSystemStatsSeeded:
    def test_latest_fleet_and_per_slot_rows(self, tmp_path: Path) -> None:
        db = tmp_path / "t.db"
        with connect(db) as conn:
            migrate(conn)
            conn.execute(
                "INSERT INTO slot_sample (ts, slot_id, state, gpu_util, power_w) "
                "VALUES ('2026-01-01T00:00:00Z', '__fleet__', 'n/a', 0.5, 40.0)"
            )
            conn.execute(
                "INSERT INTO slot_sample (ts, slot_id, state, vram_bytes) "
                "VALUES ('2026-01-01T00:00:00Z', 'primary', 'ready', 1000)"
            )
            conn.execute(
                "INSERT INTO slot_sample (ts, slot_id, state, vram_bytes) "
                "VALUES ('2026-01-01T00:05:00Z', 'primary', 'serving', 2000)"
            )
        out = metrics_read.system_stats(db)
        assert out["fleet"]["gpu_util"] == 0.5
        assert len(out["slots"]) == 1
        assert out["slots"][0]["vram_bytes"] == 2000  # latest, not the older row
        assert out["slots"][0]["state"] == "serving"


class TestStatsSummary:
    def test_empty_db_returns_zeroed_totals(self, tmp_path: Path) -> None:
        db = tmp_path / "t.db"
        with connect(db) as conn:
            migrate(conn)
        out = metrics_read.stats_summary(db)
        assert out["totals"] == {"requests": 0, "ok": 0, "errors": 0, "tokens_completed": 0}
        assert out["by_model"] == []

    def test_aggregates_within_window(self, tmp_path: Path) -> None:
        db = tmp_path / "t.db"
        now = datetime.now(UTC)
        with connect(db) as conn:
            migrate(conn)
            recent = (now - timedelta(minutes=10)).isoformat()
            stale = (now - timedelta(hours=5)).isoformat()
            conn.execute(
                "INSERT INTO request_metric "
                "(ts, request_id, model_id, runner, device, modality, ok, ttft_ms, decode_tps, "
                "completion_tokens) VALUES (?, 'r1', 'qwen3-4b', 'rocm', 'gpu-rocm', 'chat', 1, "
                "100.0, 40.0, 20)",
                (recent,),
            )
            conn.execute(
                "INSERT INTO request_metric "
                "(ts, request_id, model_id, runner, device, modality, ok) "
                "VALUES (?, 'r2', 'qwen3-4b', 'rocm', 'gpu-rocm', 'chat', 0)",
                (stale,),
            )
        out = metrics_read.stats_summary(db, window="1h")
        assert out["totals"]["requests"] == 1  # only the recent row is in-window
        assert out["totals"]["tokens_completed"] == 20
        assert out["by_model"][0]["model_id"] == "qwen3-4b"
        assert out["by_model"][0]["tps_decode"]["avg"] == 40.0

    def test_bench_baseline_surfaced(self, tmp_path: Path) -> None:
        db = tmp_path / "t.db"
        with connect(db) as conn:
            migrate(conn)
            conn.execute(
                "INSERT INTO bench_run (ts, run_id, cell_key, model_id, runner, hw_hash, "
                "tps_decode, ttft_ms, baseline, outcome) VALUES "
                "('2026-01-01T00:00:00Z', 'run1', 'key1', 'qwen3-4b', 'rocm', 'hw123', "
                "49.1, 138.0, 1, 'ok')"
            )
        out = metrics_read.stats_summary(db)
        assert "qwen3-4b x rocm x hw:hw123" in out["bench_baseline"]


class TestRequestsRollup:
    """GET /api/stats/requests payload -- see hal0.metrics.read.requests_rollup."""

    def test_unmigrated_db_returns_zeroed_shape(self, tmp_path: Path) -> None:
        out = metrics_read.requests_rollup(tmp_path / "fresh.db")
        assert out == {
            "window_s": 60,
            "req_per_min": 0.0,
            "p50_ms": None,
            "p95_ms": None,
            "endpoints": [],
            "errors": 0,
            "dedupe": False,
        }

    def test_migrated_but_empty_db_returns_zeroed_shape(self, tmp_path: Path) -> None:
        db = tmp_path / "t.db"
        with connect(db) as conn:
            migrate(conn)
        out = metrics_read.requests_rollup(db)
        assert out["req_per_min"] == 0.0
        assert out["p50_ms"] is None
        assert out["endpoints"] == []
        assert out["errors"] == 0

    def test_only_in_window_rows_counted(self, tmp_path: Path) -> None:
        db = tmp_path / "t.db"
        now = datetime.now(UTC)
        with connect(db) as conn:
            migrate(conn)
            recent = (now - timedelta(seconds=10)).isoformat()
            stale = (now - timedelta(minutes=5)).isoformat()
            conn.execute(
                "INSERT INTO request_metric (ts, request_id, model_id, ok, total_ms) "
                "VALUES (?, 'r1', 'qwen3-4b', 1, 120.0)",
                (recent,),
            )
            conn.execute(
                "INSERT INTO request_metric (ts, request_id, model_id, ok, total_ms) "
                "VALUES (?, 'r2', 'qwen3-4b', 1, 999.0)",
                (stale,),
            )
        out = metrics_read.requests_rollup(db, window_s=60)
        assert out["endpoints"] == [{"path": "qwen3-4b", "count": 1}]
        assert out["p50_ms"] == 120.0
        assert out["req_per_min"] == 1.0  # 1 request / (60s / 60) minutes

    def test_endpoints_grouped_by_model_id_and_errors_counted(self, tmp_path: Path) -> None:
        db = tmp_path / "t.db"
        now = datetime.now(UTC)
        recent = (now - timedelta(seconds=5)).isoformat()
        with connect(db) as conn:
            migrate(conn)
            conn.execute(
                "INSERT INTO request_metric (ts, request_id, model_id, ok, total_ms) "
                "VALUES (?, 'r1', 'model-a', 1, 100.0)",
                (recent,),
            )
            conn.execute(
                "INSERT INTO request_metric (ts, request_id, model_id, ok, total_ms) "
                "VALUES (?, 'r2', 'model-a', 0, 200.0)",
                (recent,),
            )
            conn.execute(
                "INSERT INTO request_metric (ts, request_id, model_id, ok, total_ms) "
                "VALUES (?, 'r3', 'model-b', 1, 300.0)",
                (recent,),
            )
            conn.execute(
                # No model_id -- must fall back to "unknown", never crash.
                "INSERT INTO request_metric (ts, request_id, model_id, ok, total_ms) "
                "VALUES (?, 'r4', NULL, 1, 400.0)",
                (recent,),
            )
        out = metrics_read.requests_rollup(db, window_s=60)
        assert out["errors"] == 1
        by_path = {e["path"]: e["count"] for e in out["endpoints"]}
        assert by_path == {"model-a": 2, "model-b": 1, "unknown": 1}
        # Sorted by count descending -- model-a (2) must come first.
        assert out["endpoints"][0]["path"] == "model-a"
        assert out["p95_ms"] == 400.0


@dataclass
class _FakeSlot:
    name: str
    state: str = "ready"
    port: int = 8081
    model_id: str | None = "qwen3-4b"
    last_used_at: float | None = None
    metadata: dict = field(default_factory=dict)


class TestModelsHealth:
    def test_empty_slot_list_returns_empty_models(self, tmp_path: Path) -> None:
        db = tmp_path / "t.db"
        with connect(db) as conn:
            migrate(conn)
        out = metrics_read.models_health([], db_path=db)
        assert out == {"models": []}

    def test_slot_row_shape(self, tmp_path: Path) -> None:
        db = tmp_path / "t.db"
        now = datetime.now(UTC)
        with connect(db) as conn:
            migrate(conn)
            conn.execute(
                "INSERT INTO request_metric (ts, request_id, slot_id, ttft_ms, decode_tps, ok) "
                "VALUES (?, 'r1', 'primary', 120.0, 45.0, 1)",
                ((now - timedelta(hours=1)).isoformat(),),
            )
        slot = _FakeSlot(name="primary", state="serving", metadata={"pinned": True})
        out = metrics_read.models_health([slot], db_path=db)
        row = out["models"][0]
        assert row["checkpoint"] == "qwen3-4b"
        assert row["health_ok"] is True
        assert row["pinned"] is True
        assert row["backend_url"] == "http://127.0.0.1:8081"
        assert row["ttft_ms_p50_24h"] == 120.0
        assert row["tps_decode_p50_24h"] == 45.0
