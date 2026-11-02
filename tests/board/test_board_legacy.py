"""Legacy hal0 board → Hermes kanban one-shot converter (ADR-0048)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import structlog
import structlog.testing

from hal0.board import BoardNotInstalled, BoardNoToken, BoardUnreachable, BoardUpstreamError
from hal0.board.legacy import (
    LEGACY_DEFER_DEADLINE_S,
    LEGACY_RETRY_COOLDOWN_S,
    convert_legacy_board,
    run_once,
)

# Carried copy of the 005 board schema (the migration file is deleted from the tree).
LEGACY_SCHEMA = """
CREATE TABLE board (
    slug TEXT PRIMARY KEY, name TEXT NOT NULL, icon TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '', is_current INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL DEFAULT (strftime('%s','now')),
    updated_at REAL NOT NULL DEFAULT (strftime('%s','now')),
    revision INTEGER NOT NULL DEFAULT 1);
CREATE TABLE board_column (
    status TEXT PRIMARY KEY, label TEXT NOT NULL, position INTEGER NOT NULL,
    visible INTEGER NOT NULL DEFAULT 1);
CREATE TABLE card (
    id TEXT PRIMARY KEY,
    board_slug TEXT NOT NULL REFERENCES board(slug) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT '', status TEXT NOT NULL REFERENCES board_column(status),
    assignee TEXT, tenant TEXT, priority INTEGER NOT NULL DEFAULT 0, workspace TEXT,
    created_by TEXT, body TEXT, block_reason TEXT, schedule TEXT, summary TEXT,
    created_at REAL NOT NULL DEFAULT (strftime('%s','now')),
    updated_at REAL NOT NULL DEFAULT (strftime('%s','now')),
    revision INTEGER NOT NULL DEFAULT 1);
CREATE TABLE card_comment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id TEXT NOT NULL REFERENCES card(id) ON DELETE CASCADE,
    author TEXT, body TEXT NOT NULL DEFAULT '', at REAL NOT NULL DEFAULT 0);
CREATE TABLE card_link (
    parent_id TEXT NOT NULL REFERENCES card(id) ON DELETE CASCADE,
    child_id TEXT NOT NULL REFERENCES card(id) ON DELETE CASCADE,
    PRIMARY KEY (parent_id, child_id));
CREATE TABLE card_run (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id TEXT NOT NULL REFERENCES card(id) ON DELETE CASCADE,
    state TEXT NOT NULL, profile TEXT, dur TEXT, msg TEXT, at REAL NOT NULL DEFAULT 0);
CREATE TABLE card_event (
    cursor INTEGER PRIMARY KEY AUTOINCREMENT, board_slug TEXT, card_id TEXT,
    kind TEXT NOT NULL, json TEXT, at REAL NOT NULL DEFAULT 0);
CREATE TABLE board_orchestration (id INTEGER PRIMARY KEY CHECK (id = 1), default_assignee TEXT);
CREATE TABLE board_profile (name TEXT PRIMARY KEY, label TEXT NOT NULL DEFAULT '');
INSERT INTO board_column (status, label, position) VALUES
 ('triage','triage',0),('todo','todo',1),('scheduled','scheduled',2),('ready','ready',3),
 ('running','in-progress',4),('blocked','blocked',5),('review','review',6),
 ('done','done',7),('archived','archived',8);
"""

NOW = 1_762_000_000.0  # 2025-11-01


def _db(tmp_path: Path, cards: list[tuple[str, str, str]] | None) -> Path:
    path = tmp_path / "hal0.db"
    conn = sqlite3.connect(path)
    if cards is not None:
        conn.executescript(LEGACY_SCHEMA)
        conn.execute("INSERT INTO board (slug, name, description) VALUES ('default','Default','d')")
        conn.execute("INSERT INTO board (slug, name) VALUES ('ops','Ops')")
        for cid, status, slug in cards:
            conn.execute(
                "INSERT INTO card (id, board_slug, title, status, assignee, tenant, priority, body)"
                " VALUES (?, ?, ?, ?, 'worker', 'acme', 3, 'body text')",
                (cid, slug, f"title {cid}", status),
            )
    conn.commit()
    conn.close()
    return path


def _tables(path: Path) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


class FakeClient:
    """Records Hermes requests; dedups creates on idempotency_key like Hermes."""

    def __init__(self) -> None:
        self.boards: list[str] = ["default"]
        self.known: set[str] = set()  # task ids present in Hermes
        self.by_key: dict[str, str] = {}
        self.created: list[dict[str, Any]] = []
        self.created_boards: list[dict[str, Any]] = []
        self.updates: list[tuple[str, dict[str, Any]]] = []
        self.fail_create_at: int | None = None
        self.fail_exc: Exception | None = None
        self.archive_rejected = False
        self.reject_create: set[str] = set()  # idempotency keys answered with a 400
        self.reject_patch: set[str] = set()  # task ids whose status PATCH gets a 400
        self._n = 0

    async def list_boards(self, **_: Any) -> Any:
        return {"boards": [{"slug": s, "name": s} for s in self.boards], "current": "default"}

    async def create_board(self, body: Any, **_: Any) -> Any:
        self.created_boards.append(body)
        self.boards.append(body["slug"])
        return {"board": body}

    async def get_task(self, task_id: str, **_: Any) -> Any:
        if task_id in self.known:
            return {"task": {"id": task_id}}
        err = BoardUpstreamError("nope")
        err.status = 404
        raise err

    async def create_task(self, body: Any, *, board: str | None = None, **_: Any) -> Any:
        if self.fail_create_at is not None and len(self.created) + 1 == self.fail_create_at:
            self.fail_create_at = None
            raise self.fail_exc or BoardUpstreamError("boom")
        key = body["idempotency_key"]
        if not body["title"].strip() or key in self.reject_create:
            err = BoardUpstreamError("title required" if key not in self.reject_create else "bad")
            err.status = 400
            raise err
        if key not in self.by_key:
            self._n += 1
            self.by_key[key] = f"h_{self._n}"
            self.known.add(f"h_{self._n}")
            self.created.append({**body, "_board": board})
        return {"task": {"id": self.by_key[key]}}

    async def update_task(self, task_id: str, body: Any, **_: Any) -> Any:
        if task_id in self.reject_patch:
            err = BoardUpstreamError("bad patch")
            err.status = 400
            raise err
        if body["status"] == "archived" and self.archive_rejected:
            err = BoardUpstreamError("bad status")
            err.status = 400
            raise err
        self.updates.append((task_id, body))
        return {"task": {"id": task_id}}


async def _run(
    client: Any, db: Path, tmp_path: Path, *, now: float = NOW, **kw: Any
) -> dict[str, Any]:
    return await convert_legacy_board(
        client, db_path=db, export_dir=tmp_path / "out", now=lambda: now, **kw
    )


async def test_no_tables_is_noop(tmp_path: Path) -> None:
    db = _db(tmp_path, None)
    client = FakeClient()
    res = await _run(client, db, tmp_path)
    assert res == {"converted": 0, "skipped": 0, "exported": None, "dropped": False}
    assert client.created == []


async def test_empty_store_drops_tables(tmp_path: Path) -> None:
    db = _db(tmp_path, [])
    res = await _run(FakeClient(), db, tmp_path)
    assert res["dropped"] is True and res["converted"] == 0
    assert not {"board", "card", "card_event", "card_link", "board_column"} & _tables(db)


async def test_cards_pushed_with_idempotency_key_and_status(tmp_path: Path) -> None:
    db = _db(tmp_path, [("t_a", "done", "default"), ("t_b", "blocked", "ops")])
    client = FakeClient()
    res = await _run(client, db, tmp_path)
    assert res == {"converted": 2, "skipped": 0, "exported": None, "dropped": True}
    assert [c["idempotency_key"] for c in client.created] == ["t_a", "t_b"]
    first = client.created[0]
    assert first["title"] == "title t_a"
    assert first["body"] == "body text\n\nimported from hal0 board 2025-11-01"
    assert (first["assignee"], first["tenant"], first["priority"]) == ("worker", "acme", 3)
    assert client.created[1]["_board"] == "ops"
    assert client.created_boards == [{"slug": "ops", "name": "Ops", "description": ""}]
    assert client.updates == [("h_1", {"status": "done"}), ("h_2", {"status": "blocked"})]
    assert "card" not in _tables(db)


async def test_existing_hermes_id_skipped(tmp_path: Path) -> None:
    db = _db(tmp_path, [("t_a", "todo", "default"), ("t_b", "todo", "default")])
    client = FakeClient()
    client.known.add("t_a")
    res = await _run(client, db, tmp_path)
    assert (res["converted"], res["skipped"], res["dropped"]) == (1, 1, True)
    assert [c["idempotency_key"] for c in client.created] == ["t_b"]


async def test_running_maps_to_ready(tmp_path: Path) -> None:
    db = _db(
        tmp_path,
        [("t_r", "running", "default"), ("t_x", "archived", "default")],
    )
    client = FakeClient()
    await _run(client, db, tmp_path)
    assert client.updates == [("h_1", {"status": "ready"}), ("h_2", {"status": "archived"})]


async def test_archive_rejection_is_logged_not_fatal(tmp_path: Path) -> None:
    db = _db(tmp_path, [("t_x", "archived", "default")])
    client = FakeClient()
    client.archive_rejected = True
    res = await _run(client, db, tmp_path)
    assert (res["converted"], res["dropped"]) == (1, True)


async def test_unreachable_exports_json_and_drops(tmp_path: Path) -> None:
    db = _db(tmp_path, [("t_a", "todo", "default")])
    res = await _run(None, db, tmp_path)
    assert res["dropped"] is True and res["exported"] is not None
    data = json.loads(Path(res["exported"]).read_text())
    assert Path(res["exported"]).name == f"board-legacy-export-{int(NOW)}.json"
    assert [c["id"] for c in data["cards"]] == ["t_a"]
    assert {b["slug"] for b in data["boards"]} == {"default", "ops"}
    assert "card" not in _tables(db)


async def test_midrun_unreachable_defers(tmp_path: Path) -> None:
    db = _db(tmp_path, [("t_a", "todo", "default"), ("t_b", "todo", "default")])
    client = FakeClient()
    client.fail_create_at, client.fail_exc = 2, BoardUnreachable("down")
    res = await _run(client, db, tmp_path)
    assert res["deferred"] == "board.unreachable"
    assert res["exported"] is None and res["dropped"] is False
    assert "card" in _tables(db)


async def test_every_create_is_triage(tmp_path: Path) -> None:
    db = _db(tmp_path, [("t_a", "ready", "default"), ("t_b", "todo", "ops")])
    client = FakeClient()
    await _run(client, db, tmp_path)
    assert len(client.created) == 2
    assert all(c["triage"] is True for c in client.created)


async def test_blank_title_gets_fallback(tmp_path: Path) -> None:
    db = _db(tmp_path, [("t_a", "todo", "default")])
    conn = sqlite3.connect(db)
    conn.execute("UPDATE card SET title = '  '")
    conn.commit()
    conn.close()
    client = FakeClient()
    res = await _run(client, db, tmp_path)
    assert res["dropped"] is True and res["converted"] == 1
    assert client.created[0]["title"] == "hal0 card t_a"


async def test_create_400_exports_that_card_and_continues(tmp_path: Path) -> None:
    db = _db(tmp_path, [("t_a", "todo", "default"), ("t_b", "todo", "default")])
    client = FakeClient()
    client.reject_create.add("t_a")
    res = await _run(client, db, tmp_path)
    assert (res["converted"], res["dropped"]) == (1, True)
    assert [c["idempotency_key"] for c in client.created] == ["t_b"]
    data = json.loads(Path(res["exported"]).read_text())
    assert [c["id"] for c in data["cards"]] == ["t_a"]
    assert data["cards"][0]["error"] == {"status": 400, "detail": "bad"}
    assert "card" not in _tables(db)


async def test_patch_400_exports_card_with_hermes_id(tmp_path: Path) -> None:
    db = _db(tmp_path, [("t_a", "todo", "default"), ("t_b", "todo", "default")])
    client = FakeClient()
    client.reject_patch.add("h_1")
    res = await _run(client, db, tmp_path)
    assert (res["converted"], res["dropped"]) == (1, True)
    data = json.loads(Path(res["exported"]).read_text())
    assert [c["id"] for c in data["cards"]] == ["t_a"]
    assert data["cards"][0]["error"]["status"] == 400
    assert data["cards"][0]["hermes_id"] == "h_1"
    assert [c["idempotency_key"] for c in client.created] == ["t_a", "t_b"]


async def test_unreachable_at_start_defers(tmp_path: Path) -> None:
    db = _db(tmp_path, [("t_a", "todo", "default")])
    with structlog.testing.capture_logs() as logs:
        res = await _run(None, db, tmp_path, defer="board.no_token")
    assert res["deferred"] == "board.no_token"
    assert res["dropped"] is False and res["exported"] is None
    assert {"card", "board_legacy_state"} <= _tables(db)
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT value FROM board_legacy_state WHERE key='first_deferred_at'")
    assert float(row.fetchone()[0]) == NOW
    conn.close()
    ev = [e for e in logs if e["event"] == "board.legacy_deferred"]
    assert ev and ev[0]["log_level"] == "warning" and ev[0]["code"] == "board.no_token"
    # a second deferral keeps the first timestamp
    await _run(None, db, tmp_path, now=NOW + 10, defer="board.no_token")
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT value FROM board_legacy_state").fetchall() == [(str(int(NOW)),)]
    conn.close()


async def test_unreachable_past_deadline_exports_with_hermes_id(tmp_path: Path) -> None:
    db = _db(tmp_path, [("t_a", "todo", "default"), ("t_b", "todo", "default")])
    client = FakeClient()
    client.fail_create_at, client.fail_exc = 2, BoardNoToken("no token")
    first = await _run(client, db, tmp_path)
    assert first["deferred"] == "board.no_token"
    later = NOW + LEGACY_DEFER_DEADLINE_S
    with structlog.testing.capture_logs() as logs:
        res = await _run(None, db, tmp_path, now=later, defer="board.unreachable")
    assert res["dropped"] is True and res["exported"] is not None
    data = json.loads(Path(res["exported"]).read_text())
    by_id = {c["id"]: c for c in data["cards"]}
    assert by_id["t_a"]["hermes_id"] == "h_1"
    assert "hermes_id" not in by_id["t_b"]
    assert any(e["event"] == "board.legacy_exported" and e["reason"] == "deadline" for e in logs)
    assert not {"card", "board_legacy_state", "board_legacy_map"} & _tables(db)


async def test_unreachable_before_deadline_still_defers(tmp_path: Path) -> None:
    db = _db(tmp_path, [("t_a", "todo", "default")])
    await _run(None, db, tmp_path, defer="board.unreachable")
    res = await _run(
        None, db, tmp_path, now=NOW + LEGACY_DEFER_DEADLINE_S - 1, defer="board.unreachable"
    )
    assert res["deferred"] == "board.unreachable" and res["dropped"] is False


async def test_run_once_cooldown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import hal0.board.legacy as legacy

    calls: list[Any] = []

    async def fake_convert(client: Any, **kw: Any) -> dict[str, Any]:
        calls.append(kw)
        return {"deferred": "board.unreachable", "dropped": False}

    clock = {"t": 1000.0}
    monkeypatch.setattr(legacy, "convert_legacy_board", fake_convert)
    monkeypatch.setattr(legacy, "resolve_board_client", lambda _app: object())
    monkeypatch.setattr(legacy.time, "time", lambda: clock["t"])
    app = SimpleNamespace(state=SimpleNamespace())
    await run_once(app)
    await run_once(app)
    assert len(calls) == 1
    assert app.state.board_legacy_next_try == 1000.0 + LEGACY_RETRY_COOLDOWN_S
    clock["t"] += LEGACY_RETRY_COOLDOWN_S
    await run_once(app)
    assert len(calls) == 2


async def test_rerun_after_partial_is_idempotent(tmp_path: Path) -> None:
    db = _db(tmp_path, [(f"t_{i}", "todo", "default") for i in range(5)])
    client = FakeClient()
    client.fail_create_at = 3  # upstream 502 on the third create
    first = await _run(client, db, tmp_path)
    assert first["dropped"] is False and first["exported"] is None
    assert "card" in _tables(db)
    assert len(client.created) == 2
    second = await _run(client, db, tmp_path)
    assert (second["dropped"], len(client.created)) == (True, 5)
    keys = [c["idempotency_key"] for c in client.created]
    assert keys == [f"t_{i}" for i in range(5)]
    assert all(c["idempotency_key"] for c in client.created)


async def test_run_once_guard_and_not_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Any] = []

    async def fake_convert(client: Any, **kw: Any) -> dict[str, Any]:
        seen.append(client)
        return {"converted": 0, "skipped": 0, "exported": None, "dropped": True}

    def raise_not_installed(_app: Any) -> Any:
        raise BoardNotInstalled("no hermes")

    monkeypatch.setattr("hal0.board.legacy.convert_legacy_board", fake_convert)
    monkeypatch.setattr("hal0.board.legacy.resolve_board_client", raise_not_installed)
    app = SimpleNamespace(state=SimpleNamespace())
    await run_once(app)
    await run_once(app)
    assert seen == [None]
    assert app.state.board_legacy_done is True


async def test_rerun_after_crash_before_drop_uses_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hal0.board.legacy as legacy

    db = _db(tmp_path, [("t_x", "archived", "default"), ("t_y", "todo", "default")])
    client = FakeClient()
    real_drop = legacy._drop_tables
    calls = {"n": 0}

    def flaky(conn: sqlite3.Connection) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("crash before drop")
        real_drop(conn)

    monkeypatch.setattr(legacy, "_drop_tables", flaky)
    with pytest.raises(RuntimeError):
        await _run(client, db, tmp_path)
    assert len(client.created) == 2
    assert "board_legacy_map" in _tables(db)
    # Hermes' idempotency lookup ignores archived tasks: forget the key to prove
    # the map alone prevents a duplicate.
    client.by_key.clear()
    res = await _run(client, db, tmp_path)
    assert len(client.created) == 2
    assert (res["converted"], res["skipped"], res["dropped"]) == (0, 2, True)
    assert "board_legacy_map" not in _tables(db)


async def test_export_is_atomic(tmp_path: Path) -> None:
    db = _db(tmp_path, [("t_a", "todo", "default")])
    res = await _run(None, db, tmp_path)
    out = Path(res["exported"])
    assert out.exists()
    assert list(out.parent.glob("*.tmp")) == []


async def test_run_once_retries_after_abort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _db(tmp_path, [("t_a", "todo", "default")])
    client = FakeClient()
    client.fail_create_at = 1
    import hal0.board.legacy as legacy

    real = legacy.convert_legacy_board

    async def with_paths(c: Any, **kw: Any) -> dict[str, Any]:
        return await real(c, db_path=db, export_dir=tmp_path / "out", now=lambda: NOW)

    monkeypatch.setattr(legacy, "convert_legacy_board", with_paths)
    monkeypatch.setattr(legacy, "resolve_board_client", lambda _app: client)
    app = SimpleNamespace(state=SimpleNamespace())
    with structlog.testing.capture_logs() as logs:
        await run_once(app)
    assert getattr(app.state, "board_legacy_done", False) is False
    assert app.state.board_legacy_next_try > 0
    app.state.board_legacy_next_try = 0.0  # cooldown elapsed
    warn = [e for e in logs if e["event"] == "board.legacy_aborted"]
    assert warn and warn[0]["log_level"] == "warning" and warn[0]["code"] == "board.upstream_error"
    assert not any(e["event"] == "board.legacy_converted" for e in logs)
    await run_once(app)
    assert app.state.board_legacy_done is True
    assert [c["idempotency_key"] for c in client.created] == ["t_a"]
    assert "card" not in _tables(db)
