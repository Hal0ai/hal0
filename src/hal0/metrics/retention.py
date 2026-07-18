"""Bounded storage -- background auto-prune (plan §13.5: "never fill a user's disk").

Three independent retention windows, each backed by a rollup that
survives the prune (spec-obs-metrics.md Part 4.4):

  * ``request_metric``  -- raw per-request rows, pruned after
    ``retention_request_days`` (default 7); ``metric_rollup`` keeps the
    hourly/daily aggregate.
  * ``slot_sample``      -- raw per-slot timeseries, pruned after
    ``retention_slot_sample_days`` (default 3); same rollup relationship.
  * ``metric_rollup``    -- long-retention aggregate itself, pruned after
    ``retention_rollup_days`` (default 90) -- this is the true floor;
    nothing downsamples past it.

``bench_run`` and ``slot_event`` are NOT pruned here: bench rows are rare
(the table grows slowly by design, kept for baseline/regression history)
and lifecycle events are a low-volume audit trail, not a timeseries.
"""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog

from hal0.db.connection import connect, tx

if TYPE_CHECKING:
    from pathlib import Path

log = structlog.get_logger("hal0.metrics.retention")


def _cutoff_iso(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


def prune(
    conn: sqlite3.Connection,
    *,
    request_days: int = 7,
    slot_sample_days: int = 3,
    rollup_days: int = 90,
) -> dict[str, int]:
    """Delete rows older than each table's retention window. Returns counts deleted."""
    deleted: dict[str, int] = {}
    with tx(conn):
        cur = conn.execute("DELETE FROM request_metric WHERE ts < ?", (_cutoff_iso(request_days),))
        deleted["request_metric"] = cur.rowcount if cur.rowcount is not None else 0

        cur = conn.execute("DELETE FROM slot_sample WHERE ts < ?", (_cutoff_iso(slot_sample_days),))
        deleted["slot_sample"] = cur.rowcount if cur.rowcount is not None else 0

        cur = conn.execute(
            "DELETE FROM metric_rollup WHERE bucket < ?", (_cutoff_iso(rollup_days),)
        )
        deleted["metric_rollup"] = cur.rowcount if cur.rowcount is not None else 0
    return deleted


class MetricsRetention:
    """Background task: prune on an interval (default every 6h)."""

    def __init__(
        self,
        *,
        db_path: Path | str | None = None,
        interval_s: float = 6 * 3600.0,
        request_days: int = 7,
        slot_sample_days: int = 3,
        rollup_days: int = 90,
    ) -> None:
        self._db_path = db_path
        self._interval_s = max(60.0, interval_s)
        self._request_days = request_days
        self._slot_sample_days = slot_sample_days
        self._rollup_days = rollup_days
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop(), name="metrics-retention")

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
                    "metrics.retention_run_failed", error=str(exc), error_type=type(exc).__name__
                )
            await asyncio.sleep(self._interval_s)

    async def run_once(self) -> dict[str, int]:
        def _run() -> dict[str, int]:
            with connect(self._db_path) as conn:
                return prune(
                    conn,
                    request_days=self._request_days,
                    slot_sample_days=self._slot_sample_days,
                    rollup_days=self._rollup_days,
                )

        result = await asyncio.to_thread(_run)
        if any(result.values()):
            log.info("metrics.retention_pruned", **result)
        return result


__all__ = ["MetricsRetention", "prune"]
