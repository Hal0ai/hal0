"""The steward's board tools read/write the hal0-owned store — #1829.

KB-4 (``e18c25ab``) moved ``/api/board/*`` off the Hermes-kanban proxy onto the
local :class:`~hal0.board.store.BoardStore`, but ``hal0.brain.chat``'s board
tools kept forwarding to ``app.state.hermes_kanban``. On a fresh rc.5 box that
forward is unauthenticated and 401s (the pinned hermes wheel ships no
``web_dist``, so the HTML session-token scrape returns nothing); with a
credential it would be worse — the steward would answer from a DIFFERENT board
than the UI, ``hal0 board list`` and ``GET /api/board/board`` show.

These pin the architectural half of the fix: the brain and the REST router
answer from THE SAME store. Every assertion is cross-surface (write on one
surface, read on the other) — a "returns 200" test would pass on the broken
code too, because the Hermes forward also returns 200 whenever a token happens
to be harvestable.

Run targeted:
    HAL0_HOME=$(mktemp -d) uv run pytest tests/brain/test_brain_board_store.py -q
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.activity import AuditStore
from hal0.api.middleware import error_codes
from hal0.api.routes import board
from hal0.api.routes import board_chat as bc
from hal0.board.store import BoardStore
from hal0.config.schema import BrainChatConfig, Hal0Config


class _HostileKanban:
    """A ``hermes_kanban`` stand-in that must NEVER be consulted.

    Two failure modes at once, mirroring the field: a read that succeeds but
    answers from a DIFFERENT board (silent divergence), and — for the tests
    that ask for it — the 401 a fresh box actually gets.
    """

    def __init__(self, *, raises: bool = False) -> None:
        self.calls: list[tuple[str, str]] = []
        self._raises = raises

    async def request_json(self, method: str, path: str, **kw: Any) -> Any:
        self.calls.append((method, path))
        if self._raises:
            raise RuntimeError("hermes kanban returned an error (401 Unauthorized)")
        return {"columns": [{"tasks": [{"id": "t_hermes", "title": "a DIFFERENT board"}]}]}


async def _make_app(tmp_path: Path, *, kanban: Any) -> FastAPI:
    """One app whose REST router and brain tools share app.state.

    The store is initialised UP FRONT with no client, so the only thing that
    could reach ``hermes_kanban`` afterwards is a board tool still forwarding
    (the first-boot import in ``ensure_initialized`` is a separate lane, out of
    scope here).
    """
    app = FastAPI()
    error_codes.install(app)
    app.include_router(board.router, prefix="/api/board")
    store = BoardStore(tmp_path / "board.db")
    await store.ensure_initialized(None)
    app.state.board_store = store
    app.state.hermes_kanban = kanban
    audit = AuditStore(tmp_path / "audit.db")
    audit.init_schema()
    app.state.audit = audit
    # Mutation tests need the mutating tools; the shipped default is read-only.
    app.state.hal0_config = Hal0Config(brain_chat=BrainChatConfig(read_only=False))
    app.state.brain_persona_root = tmp_path / "personas"
    return app


def _brain_request(app: FastAPI) -> Any:
    """A Request stand-in over the SAME app the REST client drives."""
    return SimpleNamespace(app=app, headers={})


@pytest.fixture
async def app_and_client(tmp_path: Path) -> AsyncIterator[tuple[FastAPI, TestClient]]:
    app = await _make_app(tmp_path, kanban=_HostileKanban())
    with TestClient(app) as client:
        yield app, client


# ── reads: the brain sees what the REST surface sees ─────────────────────────


@pytest.mark.asyncio
async def test_get_board_reads_the_same_store_as_rest(
    app_and_client: tuple[FastAPI, TestClient],
) -> None:
    """A card created through ``POST /api/board/tasks`` is visible to the
    steward's ``get_board`` — and the Hermes client is never touched.

    Against the unfixed code this fails on both counts: get_board returns the
    kanban stub's ``t_hermes`` row instead of the store's card.
    """
    app, client = app_and_client
    created = client.post("/api/board/tasks", json={"title": "written via REST"})
    assert created.status_code == 200
    task_id = created.json()["task"]["id"]

    result = await bc._dispatch_tool(_brain_request(app), "get_board", {}, board=None)

    ids = [t["id"] for t in result["tasks"]]
    assert task_id in ids, f"steward answered from a different board: {result}"
    assert "t_hermes" not in ids
    assert app.state.hermes_kanban.calls == []


@pytest.mark.asyncio
async def test_get_task_reads_the_same_store_as_rest(
    app_and_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_and_client
    task_id = client.post("/api/board/tasks", json={"title": "one card"}).json()["task"]["id"]

    rest = client.get(f"/api/board/tasks/{task_id}").json()
    tool = await bc._dispatch_tool(
        _brain_request(app), "get_task", {"task_id": task_id}, board=None
    )

    assert tool["id"] == rest["id"] == task_id
    assert tool["title"] == rest["title"] == "one card"
    assert app.state.hermes_kanban.calls == []


@pytest.mark.asyncio
async def test_get_assignees_and_orchestration_match_the_rest_surface(
    app_and_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_and_client
    client.post("/api/board/tasks", json={"title": "assigned", "assignee": "hal0-brain"})

    assignees = await bc._dispatch_tool(_brain_request(app), "get_assignees", {}, board=None)
    orchestration = await bc._dispatch_tool(
        _brain_request(app), "get_orchestration", {}, board=None
    )

    assert assignees == client.get("/api/board/assignees").json()
    assert [a["id"] for a in assignees] == ["hal0-brain"]
    assert orchestration == client.get("/api/board/orchestration").json()
    assert app.state.hermes_kanban.calls == []


# ── the fresh-box failure mode: Hermes absent / 401 ──────────────────────────


@pytest.mark.asyncio
async def test_board_reads_work_with_hermes_absent(tmp_path: Path) -> None:
    """No ``hermes_kanban`` wired at all — the steward still reads the board.

    This is the fresh-install case the pinned wheel creates (no ``web_dist`` ⇒
    no harvestable token ⇒ 401 on every forward).
    """
    app = await _make_app(tmp_path, kanban=None)
    with TestClient(app) as client:
        task_id = client.post("/api/board/tasks", json={"title": "hermes-free"}).json()["task"][
            "id"
        ]
        result = await bc._dispatch_tool(_brain_request(app), "get_board", {}, board=None)
    assert [t["id"] for t in result["tasks"]] == [task_id]


@pytest.mark.asyncio
async def test_board_reads_survive_a_401ing_hermes(tmp_path: Path) -> None:
    """A Hermes client that raises on every call cannot break a board read."""
    app = await _make_app(tmp_path, kanban=_HostileKanban(raises=True))
    with TestClient(app) as client:
        task_id = client.post("/api/board/tasks", json={"title": "still readable"}).json()["task"][
            "id"
        ]
        result = await bc._dispatch_tool(_brain_request(app), "get_board", {}, board=None)
    assert [t["id"] for t in result["tasks"]] == [task_id]
    assert app.state.hermes_kanban.calls == []


# ── mutations land on the store the operator is looking at ───────────────────


@pytest.mark.asyncio
async def test_chat_create_task_lands_on_the_rest_board(
    app_and_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_and_client
    result = await bc._dispatch_tool(
        _brain_request(app), "create_task", {"title": "written via chat"}, board=None
    )
    task_id = result["task"]["id"]

    lanes = client.get("/api/board/board").json()["lanes"]
    titles = [t["title"] for cards in lanes.values() for t in cards]
    assert "written via chat" in titles
    assert client.get(f"/api/board/tasks/{task_id}").status_code == 200
    assert app.state.hermes_kanban.calls == []
    # Still audited exactly as before the rewire.
    assert "board.chat.turn" in [r["action"] for r in app.state.audit.query(category="board")]


@pytest.mark.asyncio
async def test_chat_move_task_moves_the_rest_card(
    app_and_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_and_client
    task_id = client.post("/api/board/tasks", json={"title": "movable"}).json()["task"]["id"]

    await bc._dispatch_tool(
        _brain_request(app), "move_task", {"task_id": task_id, "status": "done"}, board=None
    )

    assert client.get(f"/api/board/tasks/{task_id}").json()["status"] == "done"
    assert app.state.hermes_kanban.calls == []


@pytest.mark.asyncio
async def test_chat_comment_and_block_land_on_the_rest_card(
    app_and_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_and_client
    task_id = client.post("/api/board/tasks", json={"title": "commentable"}).json()["task"]["id"]

    await bc._dispatch_tool(
        _brain_request(app),
        "comment_task",
        {"task_id": task_id, "body": "from the steward"},
        board=None,
    )
    await bc._dispatch_tool(
        _brain_request(app),
        "block_task",
        {"task_id": task_id, "block_reason": "waiting on the operator"},
        board=None,
    )

    card = client.get(f"/api/board/tasks/{task_id}").json()
    assert card["status"] == "blocked"
    assert card["block_reason"] == "waiting on the operator"
    assert app.state.hermes_kanban.calls == []


@pytest.mark.asyncio
async def test_chat_update_orchestration_lands_on_the_rest_surface(
    app_and_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_and_client
    await bc._dispatch_tool(
        _brain_request(app),
        "update_orchestration",
        {"orchestrator_profile": "hal0-brain", "auto_decompose": True},
        board=None,
    )
    rest = client.get("/api/board/orchestration").json()
    assert rest["orchestrator_profile"] == "hal0-brain"
    assert rest["auto_decompose"] is True
    assert app.state.hermes_kanban.calls == []


# ── the store seam itself ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_brain_and_routes_resolve_one_store_object(tmp_path: Path) -> None:
    """Both surfaces go through ``resolve_store``, so a box with no store
    pre-injected still ends up with exactly ONE store on app.state."""
    import hal0.config.paths as paths_mod

    app = FastAPI()
    error_codes.install(app)
    app.include_router(board.router, prefix="/api/board")
    app.state.hermes_kanban = None
    app.state.hal0_config = Hal0Config(brain_chat=BrainChatConfig(read_only=False))
    orig = paths_mod.db_path
    paths_mod.db_path = lambda: tmp_path / "hal0.db"  # type: ignore[assignment]
    try:
        with TestClient(app) as client:
            assert client.get("/api/board/board").status_code == 200
            from_routes = app.state.board_store
            from_brain = await bc._board_store(_brain_request(app))
    finally:
        paths_mod.db_path = orig  # type: ignore[assignment]
    assert from_brain is from_routes
