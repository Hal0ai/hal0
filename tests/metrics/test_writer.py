"""MetricsWriter: bounded queue, batched writes, drop-oldest overflow."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from hal0.db.connection import connect
from hal0.metrics.writer import MetricsWriter


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "metrics.db"


class TestMetricsWriter:
    def test_ensure_schema_creates_tables(self, db_path: Path) -> None:
        writer = MetricsWriter(db_path=db_path)
        writer.ensure_schema()
        with connect(db_path) as conn:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "request_metric" in tables

    @pytest.mark.asyncio
    async def test_enqueued_row_is_written(self, db_path: Path) -> None:
        writer = MetricsWriter(db_path=db_path, batch_size=8)
        writer.start()
        writer.enqueue(
            "request_metric",
            {
                "ts": "2026-01-01T00:00:00Z",
                "request_id": "r1",
                "slot_id": "primary",
                "model_id": "qwen3-4b",
                "ok": 1,
            },
        )
        # Give the background drain task a tick to run.
        for _ in range(50):
            await asyncio.sleep(0.01)
            if writer.stats["written"] >= 1:
                break
        await writer.stop()
        with connect(db_path) as conn:
            rows = conn.execute("SELECT * FROM request_metric").fetchall()
            assert len(rows) == 1
            assert rows[0]["request_id"] == "r1"

    @pytest.mark.asyncio
    async def test_batches_multiple_rows_in_one_transaction(self, db_path: Path) -> None:
        writer = MetricsWriter(db_path=db_path, batch_size=64)
        writer.start()
        for i in range(10):
            writer.enqueue(
                "request_metric",
                {
                    "ts": "2026-01-01T00:00:00Z",
                    "request_id": f"r{i}",
                    "ok": 1,
                },
            )
        for _ in range(50):
            await asyncio.sleep(0.01)
            if writer.stats["written"] >= 10:
                break
        await writer.stop()
        with connect(db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM request_metric").fetchone()[0]
            assert count == 10

    def test_overflow_drops_oldest_not_raises(self, db_path: Path) -> None:
        """enqueue() never raises or blocks, even on a full queue.

        No background drain task running -- the queue simply fills, and
        the Nth+1 row must evict the oldest rather than raise QueueFull or
        block the caller.
        """
        writer = MetricsWriter(db_path=db_path, queue_maxsize=4, batch_size=64)
        for i in range(10):
            writer.enqueue("request_metric", {"request_id": f"r{i}"})
        assert writer.stats["queued"] == 4
        assert writer.stats["dropped"] >= 1

    @pytest.mark.asyncio
    async def test_stop_is_safe_when_never_started(self, db_path: Path) -> None:
        writer = MetricsWriter(db_path=db_path)
        await writer.stop()  # must not raise
