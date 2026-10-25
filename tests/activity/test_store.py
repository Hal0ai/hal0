"""Unit tests for the durable AuditStore (SQLite source of truth).

Covers the contract the rest of the Activity subsystem depends on:
  * record() persists a row that survives a fresh connection (durability).
  * query() filters by since-cursor, category, severity, outcome, actor,
    kind, action glob, and free-text search.
  * audit_action() records outcome=ok on success and outcome=error (with
    the exception message) on failure — the "confirmation it actually
    took place" guarantee.
  * record_event() adapts an EventBus event into a durable kind="event" row.
  * prune() enforces retention without dropping recent history.
  * export() renders CSV and JSON honoring filters.
  * Provider secrets are never persisted (redaction).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from hal0.activity import AuditStore, audit_action


@pytest.fixture()
def store(tmp_path: Path) -> AuditStore:
    s = AuditStore(tmp_path / "activity.db", retention_days=30, max_rows=None)
    s.init_schema()
    return s


# ── record + durability ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_persists_and_survives_new_connection(tmp_path: Path) -> None:
    db = tmp_path / "activity.db"
    s1 = AuditStore(db, retention_days=30, max_rows=None)
    s1.init_schema()
    rid = await s1.record(
        kind="action",
        category="slot",
        action="slot.delete",
        target="chat",
        actor="dashboard",
        severity="ok",
        outcome="ok",
        message="deleted slot chat",
    )
    assert rid == 1
    # Fresh store object → fresh connection → proves it hit disk, not RAM.
    s2 = AuditStore(db, retention_days=30, max_rows=None)
    rows = s2.query()
    assert len(rows) == 1
    assert rows[0]["action"] == "slot.delete"
    assert rows[0]["outcome"] == "ok"
    assert rows[0]["target"] == "chat"


@pytest.mark.asyncio
async def test_record_assigns_monotonic_ids(store: AuditStore) -> None:
    a = await store.record(
        kind="action",
        category="model",
        action="model.pull",
        target="qwen",
        actor="cli",
        severity="info",
        outcome="pending",
        message="pull start",
    )
    b = await store.record(
        kind="action",
        category="model",
        action="model.delete",
        target="qwen",
        actor="cli",
        severity="ok",
        outcome="ok",
        message="deleted",
    )
    assert (a, b) == (1, 2)


@pytest.mark.asyncio
async def test_before_after_roundtrip_as_json(store: AuditStore) -> None:
    await store.record(
        kind="action",
        category="slot",
        action="slot.edit_config",
        target="chat",
        actor="dashboard",
        severity="ok",
        outcome="ok",
        message="ctx 4096→8192",
        before={"context_size": 4096},
        after={"context_size": 8192},
    )
    row = store.query()[0]
    assert json.loads(row["before"]) == {"context_size": 4096}
    assert json.loads(row["after"]) == {"context_size": 8192}


# ── query filters ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_query_since_cursor_excludes_seen(store: AuditStore) -> None:
    i1 = await store.record(
        kind="event",
        category="system",
        action="system.restart",
        target="api",
        actor="system",
        severity="info",
        outcome=None,
        message="boot",
    )
    i2 = await store.record(
        kind="action",
        category="profile",
        action="profile.create",
        target="p1",
        actor="dashboard",
        severity="ok",
        outcome="ok",
        message="created p1",
    )
    rows = store.query(since=i1)
    assert [r["id"] for r in rows] == [i2]


@pytest.mark.asyncio
async def test_query_filters_by_severity_and_outcome(store: AuditStore) -> None:
    await store.record(
        kind="action",
        category="slot",
        action="slot.load",
        target="chat",
        actor="dashboard",
        severity="ok",
        outcome="ok",
        message="loaded",
    )
    await store.record(
        kind="action",
        category="slot",
        action="slot.load",
        target="npu",
        actor="dashboard",
        severity="error",
        outcome="error",
        message="failed",
        error="OOM",
    )
    errs = store.query(severity="error")
    assert len(errs) == 1 and errs[0]["target"] == "npu"
    oks = store.query(outcome="ok")
    assert len(oks) == 1 and oks[0]["target"] == "chat"


@pytest.mark.asyncio
async def test_query_filters_by_category_actor_kind(store: AuditStore) -> None:
    await store.record(
        kind="action",
        category="capability",
        action="capability.apply",
        target="stt",
        actor="mcp:claude-dev",
        severity="ok",
        outcome="ok",
        message="stt→npu",
    )
    await store.record(
        kind="event",
        category="system",
        action="slot.state",
        target="chat",
        actor="system",
        severity="info",
        outcome=None,
        message="ready",
    )
    assert len(store.query(category="capability")) == 1
    assert len(store.query(actor="mcp:claude-dev")) == 1
    assert len(store.query(kind="event")) == 1


@pytest.mark.asyncio
async def test_query_action_glob_and_search(store: AuditStore) -> None:
    await store.record(
        kind="action",
        category="slot",
        action="slot.restart",
        target="chat",
        actor="dashboard",
        severity="ok",
        outcome="ok",
        message="restarted chat",
    )
    await store.record(
        kind="action",
        category="model",
        action="model.pull",
        target="qwen3",
        actor="cli",
        severity="ok",
        outcome="ok",
        message="pulled qwen3",
    )
    assert {r["target"] for r in store.query(action="slot.*")} == {"chat"}
    assert {r["target"] for r in store.query(search="qwen")} == {"qwen3"}


@pytest.mark.asyncio
async def test_query_limit_returns_newest_first(store: AuditStore) -> None:
    for i in range(5):
        await store.record(
            kind="event",
            category="system",
            action="slot.state",
            target=f"s{i}",
            actor="system",
            severity="info",
            outcome=None,
            message=str(i),
        )
    rows = store.query(limit=2)
    assert [r["target"] for r in rows] == ["s4", "s3"]


# ── audit_action confirmation guarantee ──────────────────────────────────────


@pytest.mark.asyncio
async def test_audit_action_records_ok_on_success(store: AuditStore) -> None:
    async with audit_action(
        store,
        category="slot",
        action="slot.edit_config",
        target="chat",
        actor="dashboard",
        before={"context_size": 4096},
    ) as rec:
        rec.after = {"context_size": 8192}
    row = store.query()[0]
    assert row["outcome"] == "ok"
    assert row["severity"] == "ok"
    assert json.loads(row["after"]) == {"context_size": 8192}
    assert row["duration_ms"] is not None


@pytest.mark.asyncio
async def test_audit_action_records_error_and_reraises(store: AuditStore) -> None:
    with pytest.raises(ValueError, match="boom"):
        async with audit_action(
            store, category="slot", action="slot.delete", target="chat", actor="dashboard"
        ):
            raise ValueError("boom")
    row = store.query()[0]
    assert row["outcome"] == "error"
    assert row["severity"] == "error"
    assert "boom" in row["error"]


# ── EventBus mirror ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_event_adapts_bus_event(store: AuditStore) -> None:
    event = {
        "id": 7,
        "ts": "2026-06-14T00:00:00+00:00",
        "type": "slot.state",
        "severity": "warn",
        "source": "slot:chat",
        "message": "chat: ready → error",
        "data": {"slot": "chat", "from": "ready", "to": "error"},
    }
    await store.record_event(event)
    row = store.query()[0]
    assert row["kind"] == "event"
    assert row["action"] == "slot.state"
    assert row["severity"] == "warn"
    assert row["target"] == "chat"


# ── retention ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_prune_drops_rows_older_than_retention(tmp_path: Path) -> None:
    s = AuditStore(tmp_path / "activity.db", retention_days=7, max_rows=None)
    s.init_schema()
    # Hand-insert an ancient row + a fresh one.
    await s.record(
        kind="event",
        category="system",
        action="slot.state",
        target="old",
        actor="system",
        severity="info",
        outcome=None,
        message="old",
    )
    with sqlite3.connect(s.db_path) as conn:
        conn.execute("UPDATE audit SET ts = '2000-01-01T00:00:00+00:00' WHERE target='old'")
        conn.commit()
    await s.record(
        kind="event",
        category="system",
        action="slot.state",
        target="new",
        actor="system",
        severity="info",
        outcome=None,
        message="new",
    )
    dropped = await s.prune()
    assert dropped == 1
    assert {r["target"] for r in s.query()} == {"new"}


@pytest.mark.asyncio
async def test_prune_enforces_max_rows_keeping_newest(tmp_path: Path) -> None:
    s = AuditStore(tmp_path / "activity.db", retention_days=3650, max_rows=3)
    s.init_schema()
    for i in range(6):
        await s.record(
            kind="event",
            category="system",
            action="slot.state",
            target=f"s{i}",
            actor="system",
            severity="info",
            outcome=None,
            message=str(i),
        )
    await s.prune()
    rows = s.query(limit=100)
    assert [r["target"] for r in rows] == ["s5", "s4", "s3"]


# ── export ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_export_json_and_csv(store: AuditStore) -> None:
    await store.record(
        kind="action",
        category="slot",
        action="slot.load",
        target="chat",
        actor="dashboard",
        severity="ok",
        outcome="ok",
        message="loaded chat",
    )
    blob = store.export(fmt="json")
    assert json.loads(blob)[0]["action"] == "slot.load"
    csv_blob = store.export(fmt="csv")
    assert "slot.load" in csv_blob
    assert csv_blob.splitlines()[0].startswith("id,ts,kind,category,action")


# ── redaction ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_secret_values_are_redacted_in_before_after(store: AuditStore) -> None:
    await store.record(
        kind="action",
        category="provider",
        action="provider.credential_write",
        target="openrouter",
        actor="dashboard",
        severity="ok",
        outcome="ok",
        message="set key",
        after={"api_key": "sk-supersecret-123"},
    )
    row = store.query()[0]
    assert "supersecret" not in (row["after"] or "")


# ── connection lifetime ──────────────────────────────────────────────────────


class _TrackedConnection(sqlite3.Connection):
    """A real connection that remembers whether ``close()`` was called.

    Probing a closed connection from the test thread would trip SQLite's
    same-thread check first (the store runs its writes in ``asyncio.to_thread``).
    """

    closed = False

    def close(self) -> None:
        self.closed = True
        super().close()


@pytest.fixture()
def opened_connections(monkeypatch: pytest.MonkeyPatch) -> list[_TrackedConnection]:
    """Every connection ``hal0.activity`` opens, in order."""
    opened: list[_TrackedConnection] = []
    real_connect = sqlite3.connect

    def spy(*args: object, **kwargs: object) -> _TrackedConnection:
        conn = real_connect(*args, factory=_TrackedConnection, **kwargs)
        opened.append(conn)
        return conn

    monkeypatch.setattr("hal0.activity.sqlite3.connect", spy)
    return opened


def _assert_all_closed(conns: list[_TrackedConnection]) -> None:
    assert conns, "the operation never opened a connection: the spy is not wired"
    assert [c.closed for c in conns] == [True] * len(conns)


@pytest.mark.asyncio
async def test_every_store_operation_closes_its_connection(
    tmp_path: Path, opened_connections: list[_TrackedConnection]
) -> None:
    """``with sqlite3.connect(...)`` commits but does not close, and an unclosed
    connection is only reclaimed by the cyclic GC (it logs ``ResourceWarning: unclosed
    database``), so each operation must close the connection it opened."""
    s = AuditStore(tmp_path / "activity.db", retention_days=30, max_rows=1)

    s.init_schema()
    await s.record(
        kind="action",
        category="slot",
        action="slot.load",
        target="chat",
        actor="dashboard",
        severity="ok",
        outcome="ok",
        message="loaded chat",
    )
    await s.record(
        kind="action",
        category="slot",
        action="slot.load",
        target="coder",
        actor="dashboard",
        severity="ok",
        outcome="ok",
        message="loaded coder",
    )
    assert len(s.query()) == 2
    assert s.export(fmt="json")
    assert await s.prune() == 1  # max_rows=1 trims the older row

    # init_schema + 2 inserts + query + export + prune
    assert len(opened_connections) == 6
    _assert_all_closed(opened_connections)


def test_connection_closes_when_the_body_raises(
    tmp_path: Path, opened_connections: list[_TrackedConnection]
) -> None:
    s = AuditStore(tmp_path / "activity.db")
    s.init_schema()
    with pytest.raises(RuntimeError, match="boom"), s._connect():
        raise RuntimeError("boom")
    _assert_all_closed(opened_connections)


def test_uncommitted_write_rolls_back_when_the_body_raises(tmp_path: Path) -> None:
    """Closing must not weaken the commit/rollback contract ``with conn:`` gave."""
    s = AuditStore(tmp_path / "activity.db")
    s.init_schema()
    insert = "INSERT INTO audit (ts, kind, category, action, severity) VALUES ('t', 'action', 'c', 'a', 'ok')"

    with pytest.raises(RuntimeError, match="boom"), s._connect() as conn:
        conn.execute(insert)
        raise RuntimeError("boom")
    assert s.query() == []

    with s._connect() as conn:
        conn.execute(insert)
    assert len(s.query()) == 1  # a clean exit commits
