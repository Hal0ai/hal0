"""The steward's board tools call the Hermes kanban through ``BoardClient``.

ADR-0046: ``/api/board/*`` is a live proxy to the Hermes kanban plugin, so the
hal0-brain chat tools talk to the SAME facade (``app.state.board_client``)
instead of a local store. Every assertion here is on the request the
``MockTransport`` behind the real client recorded (method, path, query, body).

Run targeted:
    PYTHONPATH=src .venv/bin/python -m pytest tests/brain/test_brain_board_client.py -q
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from hal0.activity import AuditStore
from hal0.api.routes import board_chat as bc
from hal0.board import KANBAN_BASE_PATH, HermesKanbanClient
from hal0.board.client import BoardClient
from hal0.config.schema import BrainChatConfig, Hal0Config

P = KANBAN_BASE_PATH


class _Hermes:
    """Records every request; answers from ``responses`` or ``{"ok": true}``."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.responses: dict[tuple[str, str], httpx.Response] = {}
        self.fail: Exception | None = None

    async def handler(self, request: httpx.Request) -> httpx.Response:
        if self.fail is not None:
            raise self.fail
        self.requests.append(
            {
                "method": request.method,
                "path": request.url.path,
                "params": dict(request.url.params),
                "body": json.loads(request.content) if request.content else None,
            }
        )
        return self.responses.get(
            (request.method, request.url.path), httpx.Response(200, json={"ok": True})
        )


def _request(tmp_path: Path, hermes: _Hermes, *, read_only: bool = False) -> Any:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(hermes.handler), base_url="http://hermes.test"
    )
    kanban = HermesKanbanClient(
        base_url="http://hermes.test", token_resolver=lambda: "tok", http_client=http
    )
    audit = AuditStore(tmp_path / "audit.db")
    audit.init_schema()
    state = SimpleNamespace(
        board_client=BoardClient(kanban),
        board_installed=True,
        audit=audit,
        hal0_config=Hal0Config(brain_chat=BrainChatConfig(read_only=read_only)),
        brain_persona_root=tmp_path / "personas",
    )
    return SimpleNamespace(app=SimpleNamespace(state=state), headers={})


def _audit_rows(request: Any) -> list[dict[str, Any]]:
    return request.app.state.audit.query(action="board.chat.turn")


@pytest.mark.asyncio
async def test_get_board_compacts_hermes_columns(tmp_path: Path) -> None:
    hermes = _Hermes()
    hermes.responses[("GET", f"{P}/board")] = httpx.Response(
        200,
        json={"columns": [{"name": "todo", "tasks": [{"id": "t1", "title": "x", "body": "long"}]}]},
    )
    req = _request(tmp_path, hermes)

    result = await bc._dispatch_tool(req, "get_board", {}, board="alpha")

    assert result == {"tasks": [{"id": "t1", "title": "x"}]}
    assert hermes.requests[0]["method"] == "GET"
    assert hermes.requests[0]["path"] == f"{P}/board"
    assert hermes.requests[0]["params"] == {"board": "alpha"}
    assert _audit_rows(req) == []  # reads are unaudited


@pytest.mark.asyncio
async def test_move_task_patches_status(tmp_path: Path) -> None:
    hermes = _Hermes()
    req = _request(tmp_path, hermes)

    await bc._dispatch_tool(req, "move_task", {"task_id": "t1", "status": "done"}, board=None)

    (sent,) = hermes.requests
    assert (sent["method"], sent["path"]) == ("PATCH", f"{P}/tasks/t1")
    assert sent["body"] == {"status": "done"}
    assert len(_audit_rows(req)) == 1


@pytest.mark.asyncio
async def test_add_dependency_posts_link(tmp_path: Path) -> None:
    hermes = _Hermes()
    req = _request(tmp_path, hermes)

    await bc._dispatch_tool(req, "add_dependency", {"parent_id": "p", "child_id": "c"}, board=None)

    (sent,) = hermes.requests
    assert (sent["method"], sent["path"]) == ("POST", f"{P}/links")
    assert sent["body"] == {"parent_id": "p", "child_id": "c"}


@pytest.mark.asyncio
async def test_remove_dependency_deletes_link_with_query_ids(tmp_path: Path) -> None:
    hermes = _Hermes()
    req = _request(tmp_path, hermes)

    await bc._dispatch_tool(
        req, "remove_dependency", {"parent_id": "p", "child_id": "c"}, board=None
    )

    (sent,) = hermes.requests
    assert (sent["method"], sent["path"]) == ("DELETE", f"{P}/links")
    assert sent["params"] == {"parent_id": "p", "child_id": "c"}
    assert sent["body"] is None


@pytest.mark.asyncio
async def test_nudge_dispatcher_posts_max(tmp_path: Path) -> None:
    hermes = _Hermes()
    req = _request(tmp_path, hermes)

    await bc._dispatch_tool(req, "nudge_dispatcher", {"max": 5}, board=None)

    (sent,) = hermes.requests
    assert (sent["method"], sent["path"]) == ("POST", f"{P}/dispatch")
    assert sent["params"] == {"max": "5"}


@pytest.mark.asyncio
async def test_create_task_threads_board(tmp_path: Path) -> None:
    hermes = _Hermes()
    req = _request(tmp_path, hermes)

    await bc._dispatch_tool(req, "create_task", {"title": "x", "body": None}, board="alpha")

    (sent,) = hermes.requests
    assert (sent["method"], sent["path"]) == ("POST", f"{P}/tasks")
    assert sent["params"] == {"board": "alpha"}
    assert sent["body"] == {"title": "x"}


@pytest.mark.asyncio
async def test_specify_decompose_and_orchestration(tmp_path: Path) -> None:
    hermes = _Hermes()
    req = _request(tmp_path, hermes)

    await bc._dispatch_tool(req, "specify_task", {"task_id": "t5"}, board=None)
    await bc._dispatch_tool(req, "decompose_task", {"task_id": "t6"}, board=None)
    await bc._dispatch_tool(req, "update_orchestration", {"auto_decompose": True}, board=None)
    await bc._dispatch_tool(req, "get_orchestration", {}, board=None)
    await bc._dispatch_tool(req, "get_assignees", {}, board=None)
    await bc._dispatch_tool(req, "get_task", {"task_id": "t7"}, board=None)

    got = [(r["method"], r["path"], r["body"]) for r in hermes.requests]
    assert got == [
        ("POST", f"{P}/tasks/t5/specify", {}),
        ("POST", f"{P}/tasks/t6/decompose", {}),
        ("PUT", f"{P}/orchestration", {"auto_decompose": True}),
        ("GET", f"{P}/orchestration", None),
        ("GET", f"{P}/assignees", None),
        ("GET", f"{P}/tasks/t7", None),
    ]


@pytest.mark.asyncio
async def test_board_unreachable_is_tool_error_not_exception(tmp_path: Path) -> None:
    hermes = _Hermes()
    hermes.fail = httpx.ConnectError("refused")
    req = _request(tmp_path, hermes)

    result = await bc._dispatch_tool(
        req, "move_task", {"task_id": "t1", "status": "done"}, board=None
    )

    assert result["error"] == "board.unreachable"
    assert "detail" in result
    assert isinstance(result["message"], str) and result["message"]
    (row,) = _audit_rows(req)
    assert row["outcome"] == "error"


@pytest.mark.asyncio
async def test_board_unreachable_read_is_tool_error(tmp_path: Path) -> None:
    hermes = _Hermes()
    hermes.fail = httpx.ConnectError("refused")
    req = _request(tmp_path, hermes)

    result = await bc._dispatch_tool(req, "get_board", {}, board=None)

    assert result["error"] == "board.unreachable"
    assert isinstance(result["message"], str) and result["message"]
    assert _audit_rows(req) == []


@pytest.mark.asyncio
async def test_read_only_mode_still_blocks_mutations(tmp_path: Path) -> None:
    hermes = _Hermes()
    req = _request(tmp_path, hermes, read_only=True)

    refused = await bc._dispatch_tool(
        req, "move_task", {"task_id": "t1", "status": "done"}, board=None
    )
    read = await bc._dispatch_tool(req, "get_board", {}, board=None)

    assert "read-only mode" in refused["error"]
    assert [r["method"] for r in hermes.requests] == ["GET"]  # only the read went out
    assert "read-only mode" not in json.dumps(read)
    assert _audit_rows(req) == []


_ALL_BOARD_TOOLS: list[tuple[str, dict[str, Any]]] = [
    ("move_task", {"task_id": "t1", "status": "done"}),
    ("assign_task", {"task_id": "t1", "assignee": "bob"}),
    ("block_task", {"task_id": "t1", "block_reason": "waiting"}),
    ("create_task", {"title": "x"}),
    ("comment_task", {"task_id": "t1", "body": "hi"}),
    ("add_dependency", {"parent_id": "p", "child_id": "c"}),
    ("remove_dependency", {"parent_id": "p", "child_id": "c"}),
    ("specify_task", {"task_id": "t1"}),
    ("decompose_task", {"task_id": "t1"}),
    ("nudge_dispatcher", {"max": 3}),
    ("update_orchestration", {"auto_decompose": True}),
    ("get_board", {}),
    ("get_task", {"task_id": "t1"}),
    ("get_assignees", {}),
    ("get_orchestration", {}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "args"), _ALL_BOARD_TOOLS, ids=[n for n, _ in _ALL_BOARD_TOOLS])
async def test_every_board_tool_threads_board_scope(
    tmp_path: Path, name: str, args: dict[str, Any]
) -> None:
    hermes = _Hermes()
    req = _request(tmp_path, hermes)

    await bc._dispatch_tool(req, name, args, board="alpha")

    assert len(hermes.requests) == 1
    assert hermes.requests[0]["params"]["board"] == "alpha"


@pytest.mark.asyncio
async def test_upstream_4xx_is_tool_error_and_audited(tmp_path: Path) -> None:
    hermes = _Hermes()
    hermes.responses[("PATCH", f"{P}/tasks/t1")] = httpx.Response(
        409, json={"detail": "cannot move to running"}
    )
    req = _request(tmp_path, hermes)

    result = await bc._dispatch_tool(
        req, "move_task", {"task_id": "t1", "status": "running"}, board=None
    )

    assert result["error"] == "board.upstream_error"
    assert result["detail"]["upstream_status"] == 409
    assert isinstance(result["message"], str) and result["message"]
    (row,) = _audit_rows(req)
    assert row["outcome"] == "error"
