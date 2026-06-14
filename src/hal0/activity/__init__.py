"""Durable activity / audit store — hal0's source of truth for change.

The footer :class:`hal0.events.EventBus` is a fast, in-RAM ring that is lost
on restart and carries no before/after state or success/failure outcome. This
module is its durable complement: a SQLite table recording every
config-mutating user action and system state change, with the *outcome*
captured **after** the operation runs — so the record confirms the change
actually took effect (or truthfully says it failed).

Design: single-file package mirroring ``events/__init__.py``. Uses the stdlib
``sqlite3`` (the house pattern, see ``memory/cognee_wrapper.py``) with a
per-call connection, WAL journaling, and an idempotent ``CREATE TABLE IF NOT
EXISTS`` schema. Writes are wrapped in ``asyncio.to_thread`` so the event loop
never blocks on disk.

Two write paths feed one table:
  * :func:`audit_action` — context manager around a mutation handler. Captures
    before-state, runs the body, records ``outcome="ok"`` + after-state on
    success or ``outcome="error"`` + the exception message on failure (then
    re-raises). This is the confirmation guarantee.
  * :meth:`AuditStore.record_event` — adapts an EventBus event into a durable
    ``kind="event"`` row, so structural state changes (``slot.state``,
    ``pull.*``, ``system.*``) are persisted for free.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import sqlite3
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

__all__ = ["ActionRecorder", "AuditStore", "audit_action"]

Kind = Literal["action", "event"]
Severity = Literal["info", "warn", "error", "ok"]
Outcome = Literal["ok", "error", "pending"]

# Keys whose values must never be persisted, even inside before/after blobs.
# Provider credential writes record the *fact* and *actor*, never the secret.
_SECRET_KEYS = {"api_key", "apikey", "key", "token", "secret", "password", "credential"}
_REDACTED = "***"

# Categories the type-prefix → category mapping recognises for mirrored events.
_KNOWN_CATEGORIES = {
    "slot",
    "model",
    "profile",
    "capability",
    "backend",
    "provider",
    "mcp",
    "agent",
    "approval",
    "updater",
    "comfyui",
    "pull",
    "system",
}

_COLUMNS = [
    "id",
    "ts",
    "kind",
    "category",
    "action",
    "target",
    "actor",
    "severity",
    "outcome",
    "message",
    "before",
    "after",
    "error",
    "duration_ms",
    "request_id",
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    kind        TEXT NOT NULL,
    category    TEXT NOT NULL,
    action      TEXT NOT NULL,
    target      TEXT,
    actor       TEXT,
    severity    TEXT NOT NULL,
    outcome     TEXT,
    message     TEXT,
    before      TEXT,
    after       TEXT,
    error       TEXT,
    duration_ms INTEGER,
    request_id  TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts        ON audit(ts);
CREATE INDEX IF NOT EXISTS idx_audit_category  ON audit(category);
CREATE INDEX IF NOT EXISTS idx_audit_action    ON audit(action);
CREATE INDEX IF NOT EXISTS idx_audit_severity  ON audit(severity);
CREATE INDEX IF NOT EXISTS idx_audit_outcome   ON audit(outcome);
"""

_SCHEMA_VERSION = 1


def _now_iso() -> str:
    """ISO-8601 UTC timestamp with microsecond precision (matches EventBus)."""
    return datetime.now(UTC).isoformat()


def _redact(value: Any) -> Any:
    """Recursively replace secret-looking values so they never hit disk."""
    if isinstance(value, dict):
        return {
            k: (_REDACTED if k.lower() in _SECRET_KEYS else _redact(v)) for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def _dump(value: Any) -> str | None:
    """JSON-encode a before/after blob (after redaction), or None."""
    if value is None:
        return None
    return json.dumps(_redact(value), separators=(",", ":"), default=str)


@dataclass
class ActionRecorder:
    """Mutable handle yielded by :func:`audit_action`.

    The handler fills ``after`` (and may override ``message``) to enrich the
    record written when the ``with`` block exits successfully.
    """

    after: dict[str, Any] | None = None
    message: str | None = None
    target: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class AuditStore:
    """Durable SQLite-backed audit trail. The single source of truth surfaced
    by ``/api/activity``."""

    def __init__(
        self,
        db_path: Path | str,
        *,
        retention_days: int = 30,
        max_rows: int | None = 50_000,
    ) -> None:
        self.db_path = Path(db_path)
        self.retention_days = retention_days
        self.max_rows = max_rows

    # ── connection ────────────────────────────────────────────────────────
    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def init_schema(self) -> None:
        """Create the table + indexes if absent. Idempotent; first-boot safe."""
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
            conn.commit()

    # ── write ─────────────────────────────────────────────────────────────
    def _insert(
        self,
        *,
        kind: Kind,
        category: str,
        action: str,
        target: str | None,
        actor: str | None,
        severity: Severity,
        outcome: Outcome | None,
        message: str | None,
        before: Any,
        after: Any,
        error: str | None,
        duration_ms: int | None,
        request_id: str | None,
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO audit
                    (ts, kind, category, action, target, actor, severity,
                     outcome, message, before, after, error, duration_ms, request_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    _now_iso(),
                    kind,
                    category,
                    action,
                    target,
                    actor,
                    severity,
                    outcome,
                    message,
                    _dump(before),
                    _dump(after),
                    error,
                    duration_ms,
                    request_id,
                ),
            )
            conn.commit()
            return int(cur.lastrowid or 0)

    async def record(
        self,
        *,
        kind: Kind,
        category: str,
        action: str,
        target: str | None = None,
        actor: str | None = None,
        severity: Severity = "info",
        outcome: Outcome | None = None,
        message: str | None = None,
        before: Any = None,
        after: Any = None,
        error: str | None = None,
        duration_ms: int | None = None,
        request_id: str | None = None,
    ) -> int:
        """Persist one audit row and return its monotonic id. Never blocks the
        event loop (offloaded to a worker thread)."""
        return await asyncio.to_thread(
            self._insert,
            kind=kind,
            category=category,
            action=action,
            target=target,
            actor=actor,
            severity=severity,
            outcome=outcome,
            message=message,
            before=before,
            after=after,
            error=error,
            duration_ms=duration_ms,
            request_id=request_id,
        )

    async def record_event(self, event: dict[str, Any]) -> int:
        """Adapt a :class:`hal0.events.EventBus` event into a durable row."""
        etype = str(event.get("type", "system.event"))
        data = event.get("data") or {}
        head = etype.split(".", 1)[0]
        category = head if head in _KNOWN_CATEGORIES else "system"
        target = (
            data.get("slot")
            or data.get("target")
            or data.get("model")
            or _source_target(event.get("source"))
        )
        return await self.record(
            kind="event",
            category=category,
            action=etype,
            target=target,
            actor="system",
            severity=event.get("severity", "info"),
            outcome=None,
            message=event.get("message"),
            after=data or None,
        )

    # ── read ──────────────────────────────────────────────────────────────
    def query(
        self,
        *,
        since: int = 0,
        category: str | None = None,
        action: str | None = None,
        severity: str | None = None,
        outcome: str | None = None,
        actor: str | None = None,
        kind: str | None = None,
        search: str | None = None,
        limit: int = 200,
    ) -> list[sqlite3.Row]:
        """Return rows newest-first matching the filters. ``action`` accepts a
        glob (``slot.*``); ``search`` is a free-text LIKE across
        message/target/action."""
        clauses: list[str] = ["id > ?"]
        params: list[Any] = [since]
        for col, val in (
            ("category", category),
            ("severity", severity),
            ("outcome", outcome),
            ("actor", actor),
            ("kind", kind),
        ):
            if val:
                clauses.append(f"{col} = ?")
                params.append(val)
        if action:
            clauses.append("action GLOB ?")
            params.append(action)
        if search:
            like = f"%{search}%"
            clauses.append("(message LIKE ? OR target LIKE ? OR action LIKE ?)")
            params.extend([like, like, like])
        sql = (
            f"SELECT {', '.join(_COLUMNS)} FROM audit "
            f"WHERE {' AND '.join(clauses)} "
            f"ORDER BY id DESC LIMIT ?"
        )
        params.append(max(1, min(limit, 5000)))
        with self._connect() as conn:
            return conn.execute(sql, params).fetchall()

    def export(self, *, fmt: Literal["csv", "json"], **filters: Any) -> str:
        """Render the (filtered) trail as CSV or JSON for download/archival."""
        filters.setdefault("limit", 5000)
        rows = self.query(**filters)
        dicts = [{c: r[c] for c in _COLUMNS} for r in rows]
        if fmt == "json":
            return json.dumps(dicts, indent=2, default=str)
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for d in dicts:
            writer.writerow(d)
        return buf.getvalue()

    # ── retention ─────────────────────────────────────────────────────────
    def _prune_sync(self) -> int:
        cutoff = (datetime.now(UTC) - timedelta(days=self.retention_days)).isoformat()
        dropped = 0
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM audit WHERE ts < ?", (cutoff,))
            dropped += cur.rowcount or 0
            if self.max_rows is not None:
                cur = conn.execute(
                    "DELETE FROM audit WHERE id NOT IN "
                    "(SELECT id FROM audit ORDER BY id DESC LIMIT ?)",
                    (self.max_rows,),
                )
                dropped += cur.rowcount or 0
            conn.commit()
        return dropped

    async def prune(self) -> int:
        """Drop rows older than ``retention_days`` and trim to ``max_rows``,
        keeping the newest. Returns the number deleted."""
        return await asyncio.to_thread(self._prune_sync)


def _source_target(source: Any) -> str | None:
    """Best-effort target from an event ``source`` like ``slot:chat``."""
    if isinstance(source, str) and ":" in source:
        return source.split(":", 1)[1]
    return source if isinstance(source, str) else None


@asynccontextmanager
async def audit_action(
    store: AuditStore,
    *,
    category: str,
    action: str,
    target: str | None,
    actor: str | None,
    before: dict[str, Any] | None = None,
    message: str | None = None,
    request_id: str | None = None,
) -> AsyncIterator[ActionRecorder]:
    """Record a user-initiated mutation with a truthful outcome.

    On clean exit, writes ``outcome="ok"`` (severity ``ok``) with the
    recorder's ``after`` state and elapsed time. On exception, writes
    ``outcome="error"`` (severity ``error``) carrying the exception message,
    then re-raises so the normal error envelope still fires. This is the
    "confirmation it actually took place" guarantee.
    """
    rec = ActionRecorder(target=target)
    t0 = time.monotonic()
    try:
        yield rec
    except Exception as exc:
        await store.record(
            kind="action",
            category=category,
            action=action,
            target=rec.target if rec.target is not None else target,
            actor=actor,
            severity="error",
            outcome="error",
            message=rec.message or message or f"{action} failed",
            before=before,
            after=rec.after,
            error=str(exc),
            duration_ms=int((time.monotonic() - t0) * 1000),
            request_id=request_id,
        )
        raise
    else:
        await store.record(
            kind="action",
            category=category,
            action=action,
            target=rec.target if rec.target is not None else target,
            actor=actor,
            severity="ok",
            outcome="ok",
            message=rec.message or message or action,
            before=before,
            after=rec.after,
            error=None,
            duration_ms=int((time.monotonic() - t0) * 1000),
            request_id=request_id,
        )
