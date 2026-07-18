"""ETag / If-Match optimistic concurrency on board task routes (KB-6).

hal0 uses 409 (not 412) for a stale write — the error-envelope stack ships a
Conflict(409) class and no 412 machinery, and a stale board write is the
edit-vs-edit race that class documents. If-Match is OPTIONAL and purely
additive: omit it and the write behaves exactly as before.

Run targeted:
    PYTHONPATH=src .venv/bin/python -m pytest tests/board/test_board_etag.py -q
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.activity import AuditStore
from hal0.api.middleware import error_codes
from hal0.api.routes import board
from hal0.board.store import BoardStore


@pytest.fixture
def store(tmp_path) -> BoardStore:
    s = BoardStore(tmp_path / "board.db")
    asyncio.run(s.ensure_initialized(None))
    return s


@pytest.fixture
def client(store: BoardStore, tmp_path) -> Iterator[TestClient]:
    audit = AuditStore(tmp_path / "audit.db")
    audit.init_schema()
    app = FastAPI()
    error_codes.install(app)
    app.include_router(board.router, prefix="/api/board")
    app.state.board_store = store
    app.state.hermes_kanban = None
    app.state.audit = audit
    with TestClient(app) as c:
        yield c


def _make(store: BoardStore) -> str:
    return store.create_task({"title": "a", "status": "todo"})["task"]["id"]


def test_get_task_emits_etag(client: TestClient, store: BoardStore) -> None:
    tid = _make(store)
    r = client.get(f"/api/board/tasks/{tid}")
    assert r.status_code == 200
    assert r.headers["ETag"] == '"1"'


def test_patch_emits_new_etag(client: TestClient, store: BoardStore) -> None:
    tid = _make(store)
    r = client.patch(f"/api/board/tasks/{tid}", json={"status": "ready"})
    assert r.status_code == 200
    assert r.headers["ETag"] == '"2"'


def test_patch_without_if_match_always_applies(client: TestClient, store: BoardStore) -> None:
    tid = _make(store)
    store.update_task(tid, {"title": "b"})  # revision now 2, no header sent
    r = client.patch(f"/api/board/tasks/{tid}", json={"title": "c"})
    assert r.status_code == 200  # unconditional write still works


def test_patch_matching_if_match_ok(client: TestClient, store: BoardStore) -> None:
    tid = _make(store)
    r = client.patch(
        f"/api/board/tasks/{tid}", json={"status": "ready"}, headers={"If-Match": '"1"'}
    )
    assert r.status_code == 200
    assert r.headers["ETag"] == '"2"'


def test_patch_stale_if_match_conflicts(client: TestClient, store: BoardStore) -> None:
    tid = _make(store)
    store.update_task(tid, {"title": "b"})  # revision now 2
    r = client.patch(f"/api/board/tasks/{tid}", json={"title": "c"}, headers={"If-Match": '"1"'})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "board.stale_write"


def test_delete_stale_if_match_conflicts(client: TestClient, store: BoardStore) -> None:
    tid = _make(store)
    store.update_task(tid, {"title": "b"})  # revision now 2
    r = client.request("DELETE", f"/api/board/tasks/{tid}", headers={"If-Match": '"1"'})
    assert r.status_code == 409


def test_delete_matching_if_match_ok(client: TestClient, store: BoardStore) -> None:
    tid = _make(store)
    r = client.request("DELETE", f"/api/board/tasks/{tid}", headers={"If-Match": '"1"'})
    assert r.status_code == 200


def test_if_match_weak_and_star_tolerated(client: TestClient, store: BoardStore) -> None:
    tid = _make(store)
    # weak validator form
    r = client.patch(f"/api/board/tasks/{tid}", json={"title": "b"}, headers={"If-Match": 'W/"1"'})
    assert r.status_code == 200
    # star = unconditional
    r = client.patch(f"/api/board/tasks/{tid}", json={"title": "c"}, headers={"If-Match": "*"})
    assert r.status_code == 200
