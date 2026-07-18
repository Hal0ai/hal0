"""Async, batched, off-hot-path SQLite writer for the metrics tables.

One bounded ``asyncio.Queue`` feeds one background drain task that batches
up to ``write_batch_size`` rows into a single ``BEGIN IMMEDIATE`` write
(:func:`hal0.db.connection.tx`). Every metrics producer (the T1 request
seam, the T2 sampler) shares this single writer -- never opens its own
connection -- so writes never contend with each other beyond SQLite's own
WAL serialization.

Hot-path cost (plan §13.5): enqueuing a row is a dict build + a
``queue.put_nowait`` -- no I/O, no lock acquisition, sub-50µs. On overflow
(sustained backpressure) the oldest queued row is dropped and a warning is
logged once per drain cycle -- the request handler is NEVER blocked or
made to wait on a full queue.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import structlog

from hal0.db.connection import connect, tx
from hal0.db.migrate import migrate

log = structlog.get_logger("hal0.metrics.writer")

#: (table_name, row_dict) -- the only shape ever placed on the queue.
_QueueItem = tuple[str, dict[str, Any]]


def _insert_sql(table: str, row: dict[str, Any]) -> tuple[str, tuple[Any, ...]]:
    columns = tuple(row.keys())
    placeholders = ",".join("?" for _ in columns)
    sql = f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})"
    return sql, tuple(row[c] for c in columns)


class MetricsWriter:
    """Bounded-queue async writer shared by every metrics producer.

    Construction never touches the filesystem; call :meth:`start` from an
    async context (app lifespan) to open the background drain task, and
    :meth:`ensure_schema` once (idempotent) before the first write so a
    process that never constructs :class:`~hal0.registry.store.SqliteModelRegistry`
    still gets the 001+002 tables.
    """

    def __init__(
        self,
        *,
        db_path: Path | str | None = None,
        queue_maxsize: int = 1024,
        batch_size: int = 64,
    ) -> None:
        self._db_path = db_path
        self._batch_size = max(1, batch_size)
        self._queue: asyncio.Queue[_QueueItem] = asyncio.Queue(maxsize=max(1, queue_maxsize))
        self._task: asyncio.Task[None] | None = None
        self._dropped = 0
        self._written = 0
        self._stopping = False

    @property
    def db_path(self) -> Path | str | None:
        """The SQLite path this writer was constructed with (``None`` = default)."""
        return self._db_path

    # ── schema ────────────────────────────────────────────────────────────

    def ensure_schema(self) -> None:
        """Apply any pending migrations (001, 002, ...). Idempotent, sync.

        Safe to call from a non-async context (e.g. a CLI verb) -- opens
        and closes its own short-lived connection.
        """
        with connect(self._db_path) as conn:
            migrate(conn, migrations_dir=None)

    # ── producer side (hot path) ────────────────────────────────────────

    def enqueue(self, table: str, row: dict[str, Any]) -> None:
        """Non-blocking enqueue. Drops the OLDEST queued row on overflow.

        Never raises, never awaits -- this is the only surface the T1
        seam / T2 sampler touch from a live request or sampler tick.
        """
        try:
            self._queue.put_nowait((table, row))
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
                self._dropped += 1
            with contextlib.suppress(asyncio.QueueFull):
                self._queue.put_nowait((table, row))
            log.warning("metrics.writer_queue_overflow", dropped_total=self._dropped)

    # ── background drain ─────────────────────────────────────────────────

    def start(self) -> None:
        if self._task is not None:
            return
        self.ensure_schema()
        self._stopping = False
        self._task = asyncio.create_task(self._drain_loop(), name="metrics-writer-drain")

    async def stop(self, *, flush_timeout_s: float = 2.0) -> None:
        if self._task is None:
            return
        self._stopping = True
        with contextlib.suppress(TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(self._queue.join(), timeout=flush_timeout_s)
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _drain_loop(self) -> None:
        while True:
            item = await self._queue.get()
            batch: list[_QueueItem] = [item]
            self._queue.task_done()
            while len(batch) < self._batch_size:
                try:
                    nxt = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                batch.append(nxt)
                self._queue.task_done()
            self._write_batch(batch)
            if self._stopping and self._queue.empty():
                # Let stop() observe an empty queue promptly; loop
                # continues in case more rows land before cancellation.
                await asyncio.sleep(0)

    def _write_batch(self, batch: Iterable[_QueueItem]) -> None:
        try:
            with connect(self._db_path) as conn, tx(conn):
                for table, row in batch:
                    sql, params = _insert_sql(table, row)
                    conn.execute(sql, params)
                    self._written += 1
        except Exception as exc:  # pragma: no cover -- defensive, must never crash the loop
            log.warning(
                "metrics.writer_batch_failed",
                error=str(exc),
                error_type=type(exc).__name__,
                batch_size=len(list(batch)) if not isinstance(batch, list) else len(batch),
            )

    @property
    def stats(self) -> dict[str, int]:
        """Diagnostic counters -- surfaced by ``hal0 metrics status``."""
        return {
            "written": self._written,
            "dropped": self._dropped,
            "queued": self._queue.qsize(),
        }


__all__ = ["MetricsWriter"]
