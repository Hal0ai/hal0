"""Background hourly/daily rollup -- downsamples T1/T2 raw rows into ``metric_rollup``.

Idempotent by design: every upsert is ``INSERT OR REPLACE`` keyed on
``(bucket, dim_kind, dim_key)`` (the table's primary key), so re-running
the same hour twice (a restart mid-aggregation, or a test calling
:func:`aggregate_hour` directly) produces the same row, not a duplicate.

Percentiles are computed in Python (nearest-rank) rather than through a
SQL window function -- hourly buckets on a single-box install are small
(low thousands of rows at most), so pulling them into a Python list and
sorting is simpler and cheaper to test than emulating percentile_cont in
SQLite.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog

from hal0.db.connection import connect, tx

if TYPE_CHECKING:
    from pathlib import Path

log = structlog.get_logger("hal0.metrics.aggregator")


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(pct * (len(ordered) - 1))))
    return ordered[idx]


def _avg(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _bucket_bounds(bucket_start: datetime) -> tuple[str, str]:
    bucket_end = bucket_start + timedelta(hours=1)
    return bucket_start.isoformat(), bucket_end.isoformat()


def aggregate_hour(conn: sqlite3.Connection, bucket_start: datetime) -> int:
    """Aggregate one hour of ``request_metric`` + ``slot_sample`` rows.

    ``bucket_start`` must be truncated to the hour by the caller. Returns
    the number of ``metric_rollup`` rows written.
    """
    start_iso, end_iso = _bucket_bounds(bucket_start)
    bucket_label = bucket_start.strftime("%Y-%m-%dT%H:00:00Z")
    written = 0

    with tx(conn):
        # ── request_hourly ──────────────────────────────────────────────
        rows = conn.execute(
            "SELECT model_id, runner, device, modality, ok, ttft_ms, prefill_tps, decode_tps, "
            "spec_accept_rate FROM request_metric WHERE ts >= ? AND ts < ?",
            (start_iso, end_iso),
        ).fetchall()
        grouped: dict[tuple, list[sqlite3.Row]] = defaultdict(list)
        for row in rows:
            key = (row["model_id"], row["runner"], row["device"], row["modality"])
            grouped[key].append(row)

        for (model_id, runner, device, modality), group in grouped.items():
            ok_count = sum(1 for r in group if r["ok"])
            ttft = [r["ttft_ms"] for r in group if r["ttft_ms"] is not None]
            prefill = [r["prefill_tps"] for r in group if r["prefill_tps"] is not None]
            decode = [r["decode_tps"] for r in group if r["decode_tps"] is not None]
            spec_accept = [
                r["spec_accept_rate"] for r in group if r["spec_accept_rate"] is not None
            ]
            dim_key = json.dumps(
                {
                    "model_id": model_id,
                    "runner": runner,
                    "device": device,
                    "modality": modality,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            conn.execute(
                "INSERT OR REPLACE INTO metric_rollup "
                "(bucket, dim_kind, dim_key, count, ok_count, ttft_ms_p50, ttft_ms_p95, "
                "tps_prefill_avg, tps_decode_avg, tps_decode_p50, spec_accept_avg, "
                "vram_bytes_avg, gtt_bytes_avg, power_w_avg) "
                "VALUES (?, 'request_hourly', ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)",
                (
                    bucket_label,
                    dim_key,
                    len(group),
                    ok_count,
                    _percentile(ttft, 0.50),
                    _percentile(ttft, 0.95),
                    _avg(prefill),
                    _avg(decode),
                    _percentile(decode, 0.50),
                    _avg(spec_accept),
                ),
            )
            written += 1

        # ── slot_sample_hourly ───────────────────────────────────────────
        slot_rows = conn.execute(
            "SELECT slot_id, vram_bytes, gtt_bytes, power_w FROM slot_sample "
            "WHERE ts >= ? AND ts < ?",
            (start_iso, end_iso),
        ).fetchall()
        slot_grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in slot_rows:
            slot_grouped[row["slot_id"]].append(row)

        for slot_id, group in slot_grouped.items():
            vram = [r["vram_bytes"] for r in group if r["vram_bytes"] is not None]
            gtt = [r["gtt_bytes"] for r in group if r["gtt_bytes"] is not None]
            power = [r["power_w"] for r in group if r["power_w"] is not None]
            dim_key = json.dumps({"slot_id": slot_id}, sort_keys=True, separators=(",", ":"))
            conn.execute(
                "INSERT OR REPLACE INTO metric_rollup "
                "(bucket, dim_kind, dim_key, count, ok_count, ttft_ms_p50, ttft_ms_p95, "
                "tps_prefill_avg, tps_decode_avg, tps_decode_p50, spec_accept_avg, "
                "vram_bytes_avg, gtt_bytes_avg, power_w_avg) "
                "VALUES (?, 'slot_sample_hourly', ?, ?, NULL, NULL, NULL, NULL, NULL, NULL, "
                "NULL, ?, ?, ?)",
                (
                    bucket_label,
                    dim_key,
                    len(group),
                    int(_avg(vram)) if vram else None,
                    int(_avg(gtt)) if gtt else None,
                    _avg(power),
                ),
            )
            written += 1

    return written


class MetricsAggregator:
    """Background task: aggregate the most recently completed hour, on interval."""

    def __init__(
        self,
        *,
        db_path: Path | str | None = None,
        interval_s: float = 3600.0,
    ) -> None:
        self._db_path = db_path
        self._interval_s = max(60.0, interval_s)
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop(), name="metrics-aggregator")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception as exc:  # pragma: no cover -- defensive
                log.warning(
                    "metrics.aggregator_run_failed", error=str(exc), error_type=type(exc).__name__
                )
            await asyncio.sleep(self._interval_s)

    async def run_once(self) -> int:
        """Aggregate the last fully-elapsed hour. Returns rows written."""
        now = datetime.now(UTC)
        completed_hour_start = (now - timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)

        def _run() -> int:
            with connect(self._db_path) as conn:
                return aggregate_hour(conn, completed_hour_start)

        return await asyncio.to_thread(_run)


__all__ = ["MetricsAggregator", "aggregate_hour"]
