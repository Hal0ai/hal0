"""hal0.metrics.read -- system_stats / stats_summary / models_health contracts.

Every function must degrade to an empty/zeroed shape on a fresh
(unmigrated) DB -- the "works with the stack off" requirement (plan
§13.1) -- and return the documented shape once seeded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import pairwise
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


class TestStatsSummaryBuckets:
    """``buckets`` time series on ``stats_summary`` -- zero-filled, oldest first."""

    @staticmethod
    def _seed(db: Path) -> None:
        now = datetime.now(UTC)
        with connect(db) as conn:
            migrate(conn)
            for i, (age, tokens) in enumerate(
                [(timedelta(minutes=5), 20), (timedelta(minutes=40), 30), (timedelta(hours=3), 7)]
            ):
                conn.execute(
                    "INSERT INTO request_metric "
                    "(ts, request_id, model_id, runner, device, modality, ok, completion_tokens) "
                    "VALUES (?, ?, 'qwen3-4b', 'rocm', 'gpu-rocm', 'chat', 1, ?)",
                    ((now - age).isoformat(), f"r{i}", tokens),
                )

    def test_shape_1h(self, tmp_path: Path) -> None:
        db = tmp_path / "t.db"
        self._seed(db)
        out = metrics_read.stats_summary(db, window="1h")
        assert len(out["buckets"]) == 24
        assert all(set(b) == {"ts", "requests", "tokens_completed"} for b in out["buckets"])

    def test_count_by_window(self, tmp_path: Path) -> None:
        db = tmp_path / "t.db"
        self._seed(db)
        assert len(metrics_read.stats_summary(db, window="24h")["buckets"]) == 24
        assert len(metrics_read.stats_summary(db, window="7d")["buckets"]) == 21

    def test_bucket_spacing_and_alignment(self, tmp_path: Path) -> None:
        db = tmp_path / "t.db"
        self._seed(db)
        out = metrics_read.stats_summary(db, window="1h")
        starts = [datetime.fromisoformat(b["ts"]) for b in out["buckets"]]
        assert starts == sorted(starts)  # oldest first
        assert all((b - a).total_seconds() == 150 for a, b in pairwise(starts))
        assert starts[0] == datetime.fromisoformat(out["window"]["from"])
        end = starts[-1] + timedelta(seconds=150)
        assert end == datetime.fromisoformat(out["window"]["to"])

    def test_sum_to_totals(self, tmp_path: Path) -> None:
        db = tmp_path / "t.db"
        self._seed(db)
        out = metrics_read.stats_summary(db, window="24h")
        assert out["totals"]["requests"] == 3
        assert sum(b["requests"] for b in out["buckets"]) == out["totals"]["requests"]
        assert (
            sum(b["tokens_completed"] for b in out["buckets"]) == out["totals"]["tokens_completed"]
        )

    def test_rows_land_in_expected_bucket(self, tmp_path: Path) -> None:
        db = tmp_path / "t.db"
        self._seed(db)
        out = metrics_read.stats_summary(db, window="1h")
        # 40 min ago sits ~bucket 8 of 24; 5 min ago sits ~bucket 22.
        assert sum(b["tokens_completed"] for b in out["buckets"][:12]) == 30
        assert sum(b["tokens_completed"] for b in out["buckets"][-3:]) == 20
        assert sum(b["requests"] for b in out["buckets"]) == 2  # the 3 h-old row is out of window

    def test_empty_window_is_zero_filled(self, tmp_path: Path) -> None:
        db = tmp_path / "t.db"
        with connect(db) as conn:
            migrate(conn)
        out = metrics_read.stats_summary(db, window="1h")
        assert len(out["buckets"]) == 24
        assert all(b["requests"] == 0 and b["tokens_completed"] == 0 for b in out["buckets"])

    def test_unmigrated_db_is_zero_filled(self, tmp_path: Path) -> None:
        out = metrics_read.stats_summary(tmp_path / "fresh.db", window="7d")
        assert len(out["buckets"]) == 21
        assert all(b["requests"] == 0 for b in out["buckets"])


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


class TestSlotActivity:
    """``slot_activity`` -- per-slot token rate / requests / TTFT and fleet duty, bucketed."""

    NOW = datetime(2026, 11, 15, 12, 0, 0, tzinfo=UTC)

    def _db(self, tmp_path: Path) -> Path:
        db = tmp_path / "t.db"
        with connect(db) as conn:
            migrate(conn)
        return db

    def _request(
        self,
        conn,
        *,
        end: datetime,
        slot: str,
        tokens: int | None,
        total_ms: float,
        ttft_ms: float | None = None,
        ok: int = 1,
    ) -> None:
        conn.execute(
            "INSERT INTO request_metric (ts, request_id, slot_id, completion_tokens, ttft_ms, "
            "total_ms, ok) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (end.isoformat(), f"r-{end.timestamp()}-{slot}", slot, tokens, ttft_ms, total_ms, ok),
        )

    def test_unmigrated_db_returns_empty_buckets(self, tmp_path: Path) -> None:
        out = metrics_read.slot_activity(tmp_path / "fresh.db", window="5m", now=self.NOW)
        assert out["window"] == "5m"
        assert out["buckets"] == 60
        assert out["bucket_s"] == 5
        assert out["slots"] == {}
        assert out["fleet"]["npu_util"] == [None] * 60
        assert out["fleet"]["gpu_util"] == [None] * 60

    def test_unknown_window_falls_back_to_15m(self, tmp_path: Path) -> None:
        out = metrics_read.slot_activity(self._db(tmp_path), window="3d", now=self.NOW)
        assert out["window"] == "15m"
        assert out["bucket_s"] == 15

    def test_tokens_spread_over_generation_time(self, tmp_path: Path) -> None:
        """100 tokens generated over the last 10 s (ttft excluded) fill the last two 5 s buckets."""
        db = self._db(tmp_path)
        with connect(db) as conn:
            self._request(
                conn, end=self.NOW, slot="flm", tokens=100, total_ms=12_000, ttft_ms=2_000
            )
        out = metrics_read.slot_activity(db, window="5m", now=self.NOW)
        tps = out["slots"]["flm"]["tps"]
        assert tps[-1] == 10.0  # 50 tokens / 5 s
        assert tps[-2] == 10.0
        assert sum(tps[:-2]) == 0
        assert out["slots"]["flm"]["peak_tps"] == 10.0
        assert out["slots"]["flm"]["tokens"] == 100

    def test_requests_errors_and_ttft_p50_per_slot(self, tmp_path: Path) -> None:
        db = self._db(tmp_path)
        with connect(db) as conn:
            for i, ttft in enumerate((100.0, 200.0, 300.0)):
                end = self.NOW - timedelta(seconds=30 * (i + 1))
                self._request(
                    conn, end=end, slot="npu-fast", tokens=10, total_ms=1_000, ttft_ms=ttft
                )
            self._request(conn, end=self.NOW, slot="npu-fast", tokens=None, total_ms=50, ok=0)
            # Outside the 5 m window: not counted.
            self._request(
                conn,
                end=self.NOW - timedelta(minutes=10),
                slot="npu-fast",
                tokens=10,
                total_ms=1_000,
            )
        lane = metrics_read.slot_activity(db, window="5m", now=self.NOW)["slots"]["npu-fast"]
        assert lane["requests"] == 4
        assert lane["errors"] == 1
        assert lane["ttft_p50_ms"] == 200.0

    def test_request_started_before_the_window_counts_only_its_overlap(
        self, tmp_path: Path
    ) -> None:
        db = self._db(tmp_path)
        start = self.NOW - timedelta(minutes=5)
        with connect(db) as conn:
            # 20 s of generation, half before the window opens.
            self._request(
                conn, end=start + timedelta(seconds=10), slot="flm", tokens=200, total_ms=20_000
            )
        lane = metrics_read.slot_activity(db, window="5m", now=self.NOW)["slots"]["flm"]
        assert lane["tokens"] == 200
        assert sum(t * 5 for t in lane["tps"]) == 100.0  # only the in-window half is charted

    def test_fleet_duty_is_the_bucket_mean_and_null_without_samples(self, tmp_path: Path) -> None:
        db = self._db(tmp_path)
        with connect(db) as conn:
            for offset, npu in ((1, 0.2), (2, 0.6)):
                ts = (self.NOW - timedelta(seconds=offset)).isoformat()
                conn.execute(
                    "INSERT INTO slot_sample (ts, slot_id, state, npu_util, gpu_util) "
                    "VALUES (?, '__fleet__', 'n/a', ?, 0.5)",
                    (ts, npu),
                )
        fleet = metrics_read.slot_activity(db, window="5m", now=self.NOW)["fleet"]
        assert fleet["npu_util"][-1] == 0.4
        assert fleet["gpu_util"][-1] == 0.5
        assert fleet["npu_util"][:-1] == [None] * 59

    def test_rows_after_now_are_ignored(self, tmp_path: Path) -> None:
        """A row written after ``now`` was captured never lands in the last bucket."""
        db = self._db(tmp_path)
        later = self.NOW + timedelta(seconds=10)
        with connect(db) as conn:
            self._request(conn, end=later, slot="flm", tokens=50, total_ms=1_000)
            conn.execute(
                "INSERT INTO slot_sample (ts, slot_id, state, npu_util) "
                "VALUES (?, '__fleet__', 'n/a', 0.9)",
                (later.isoformat(),),
            )
        out = metrics_read.slot_activity(db, window="5m", now=self.NOW)
        assert out["slots"] == {}
        assert out["fleet"]["npu_util"][-1] is None

    def test_ttft_equal_to_total_puts_the_tokens_at_the_end(self, tmp_path: Path) -> None:
        """A 30 s wait then the whole answer at once: one bucket, not six."""
        db = self._db(tmp_path)
        with connect(db) as conn:
            self._request(conn, end=self.NOW, slot="flm", tokens=1, total_ms=30_000, ttft_ms=30_000)
        tps = metrics_read.slot_activity(db, window="5m", now=self.NOW)["slots"]["flm"]["tps"]
        assert tps[-1] == 0.2  # 1 token / 5 s bucket
        assert sum(1 for v in tps if v > 0) == 1
