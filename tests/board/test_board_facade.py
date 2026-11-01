"""Tests for the BoardClient facade — src/hal0/board/client.py.

Every facade method is a one-line forward through
``HermesKanbanClient.request_json``; these tests assert the method, path and
query/body on the request recorded by an ``httpx.MockTransport`` behind the
real client. The method lists are the contract the other board lanes build on.

Run targeted:
    PYTHONPATH=src .venv/bin/python -m pytest tests/board/test_board_facade.py -q
"""

from __future__ import annotations

import json
import types
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from hal0.board import KANBAN_BASE_PATH, BoardNotInstalled, HermesKanbanClient
from hal0.board.client import BoardClient, resolve_board_client


class _Recorder:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(
            {
                "method": request.method,
                "path": request.url.path,
                "params": dict(request.url.params),
                "body": json.loads(request.content) if request.content else None,
                "agent": request.headers["x-hal0-agent"],
            }
        )
        return httpx.Response(200, json={"ok": True})


def _kanban(recorder: _Recorder) -> HermesKanbanClient:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(recorder.handler), base_url="http://127.0.0.1:9119"
    )
    return HermesKanbanClient(http_client=http, token_resolver=lambda: "TOK")


@pytest.fixture
def recorder() -> _Recorder:
    return _Recorder()


@pytest.fixture
def client(recorder: _Recorder) -> BoardClient:
    return BoardClient(_kanban(recorder))


P = KANBAN_BASE_PATH

# (method name, args, kwargs, HTTP method, upstream path, extra query params)
_READS: list[tuple[str, tuple[Any, ...], dict[str, Any], str, str, dict[str, str]]] = [
    ("get_board", (), {}, "GET", "/board", {"include_archived": "false"}),
    (
        "get_board",
        (),
        {"tenant": "t1", "include_archived": True, "workflow_template_id": "wf"},
        "GET",
        "/board",
        {"tenant": "t1", "include_archived": "true", "workflow_template_id": "wf"},
    ),
    ("get_task", ("t1",), {}, "GET", "/tasks/t1", {}),
    ("get_task_log", ("t1",), {}, "GET", "/tasks/t1/log", {}),
    ("get_task_log", ("t1",), {"tail": 50}, "GET", "/tasks/t1/log", {"tail": "50"}),
    ("list_boards", (), {}, "GET", "/boards", {"include_archived": "false"}),
    (
        "list_boards",
        (),
        {"include_archived": False},
        "GET",
        "/boards",
        {"include_archived": "false"},
    ),
    ("list_profiles", (), {}, "GET", "/profiles", {}),
    ("list_assignees", (), {}, "GET", "/assignees", {}),
    ("get_stats", (), {}, "GET", "/stats", {}),
    ("get_diagnostics", (), {}, "GET", "/diagnostics", {}),
    ("list_active_workers", (), {}, "GET", "/workers/active", {}),
    ("get_run", ("r1",), {}, "GET", "/runs/r1", {}),
    ("get_config", (), {}, "GET", "/config", {}),
    ("get_orchestration", (), {}, "GET", "/orchestration", {}),
]


@pytest.mark.parametrize(("name", "args", "kwargs", "verb", "path", "extra"), _READS)
async def test_read_rows_forward_to_hermes_path(
    client: BoardClient,
    recorder: _Recorder,
    name: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    verb: str,
    path: str,
    extra: dict[str, str],
) -> None:
    method = getattr(client, name)
    # Without ``board`` / ``agent_id`` neither is sent.
    assert await method(*args, **kwargs) == {"ok": True}
    sent = recorder.requests[-1]
    assert (sent["method"], sent["path"], sent["params"]) == (verb, f"{P}{path}", extra)
    assert sent["body"] is None
    assert sent["agent"] == "hermes"

    # With ``board`` + ``agent_id`` both are threaded through.
    await method(*args, **kwargs, board="alpha", agent_id="claude-dev")
    sent = recorder.requests[-1]
    assert sent["path"] == f"{P}{path}"
    assert sent["params"] == {**extra, "board": "alpha"}
    assert sent["agent"] == "claude-dev"


_BODY = {"k": "v"}

# (method name, args, kwargs, expected HTTP method, upstream path, expected body)
_MUTATIONS: list[tuple[str, tuple[Any, ...], dict[str, Any], str, str, Any]] = [
    ("create_task", (_BODY,), {}, "POST", "/tasks", _BODY),
    ("update_task", ("t1", _BODY), {}, "PATCH", "/tasks/t1", _BODY),
    ("delete_task", ("t1",), {}, "DELETE", "/tasks/t1", None),
    ("comment_task", ("t1", _BODY), {}, "POST", "/tasks/t1/comments", _BODY),
    ("bulk_tasks", (_BODY,), {}, "POST", "/tasks/bulk", _BODY),
    ("reassign_task", ("t1", _BODY), {}, "POST", "/tasks/t1/reassign", _BODY),
    ("specify_task", ("t1", _BODY), {}, "POST", "/tasks/t1/specify", _BODY),
    ("decompose_task", ("t1", _BODY), {}, "POST", "/tasks/t1/decompose", _BODY),
    ("reclaim_task", ("t1", _BODY), {}, "POST", "/tasks/t1/reclaim", _BODY),
    ("add_link", (_BODY,), {}, "POST", "/links", _BODY),
    ("remove_link", (_BODY,), {}, "DELETE", "/links", _BODY),
    ("dispatch_nudge", (), {}, "POST", "/dispatch", None),
    ("create_board", (_BODY,), {}, "POST", "/boards", _BODY),
    ("update_board", ("alpha", _BODY), {}, "PATCH", "/boards/alpha", _BODY),
    ("switch_board", ("alpha",), {}, "POST", "/boards/alpha/switch", None),
    ("update_profile", ("dev", _BODY), {}, "PATCH", "/profiles/dev", _BODY),
    ("update_orchestration", (_BODY,), {}, "PUT", "/orchestration", _BODY),
]


@pytest.mark.parametrize(("name", "args", "kwargs", "verb", "path", "body"), _MUTATIONS)
async def test_mutations_forward_body(
    client: BoardClient,
    recorder: _Recorder,
    name: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    verb: str,
    path: str,
    body: Any,
) -> None:
    method = getattr(client, name)
    await method(*args, **kwargs)
    sent = recorder.requests[-1]
    assert (sent["method"], sent["path"], sent["params"], sent["body"]) == (
        verb,
        f"{P}{path}",
        {},
        body,
    )
    assert sent["agent"] == "hermes"

    await method(*args, **kwargs, board="alpha", agent_id="claude-dev")
    sent = recorder.requests[-1]
    assert sent["params"] == {"board": "alpha"}
    assert sent["body"] == body
    assert sent["agent"] == "claude-dev"


async def test_dispatch_nudge_sends_max(client: BoardClient, recorder: _Recorder) -> None:
    await client.dispatch_nudge(max_dispatch=3, board="alpha")
    sent = recorder.requests[-1]
    assert (sent["method"], sent["path"]) == ("POST", f"{P}/dispatch")
    assert sent["params"] == {"max": "3", "board": "alpha"}


@pytest.mark.parametrize(("flag", "wire"), [(True, "true"), (False, "false")])
async def test_delete_board_sends_delete_flag(
    client: BoardClient, recorder: _Recorder, flag: bool, wire: str
) -> None:
    await client.delete_board("alpha", delete=flag)
    sent = recorder.requests[-1]
    assert (sent["method"], sent["path"]) == ("DELETE", f"{P}/boards/alpha")
    assert sent["params"] == {"delete": wire}
    assert sent["body"] is None


# ── resolve_board_client ─────────────────────────────────────────────────────


def _app() -> Any:
    return types.SimpleNamespace(state=types.SimpleNamespace())


def _check(value: bool) -> Callable[[], bool]:
    return lambda: value


def test_resolve_raises_not_installed_when_no_hermes() -> None:
    app = _app()
    with pytest.raises(BoardNotInstalled):
        resolve_board_client(app, installed_check=_check(False))
    assert app.state.board_installed is False
    assert not hasattr(app.state, "board_client")


def test_resolve_caches_client_on_app_state() -> None:
    app = _app()
    kanban = HermesKanbanClient(token_resolver=lambda: "TOK")
    app.state.hermes_kanban = kanban
    calls: list[int] = []

    def check() -> bool:
        calls.append(1)
        return True

    first = resolve_board_client(app, installed_check=check)
    assert isinstance(first, BoardClient)
    assert first._kanban is kanban
    assert resolve_board_client(app, installed_check=check) is first
    assert app.state.board_client is first
    assert app.state.board_installed is True
    assert calls == [1]


def test_resolve_builds_kanban_client_when_missing() -> None:
    app = _app()
    client = resolve_board_client(app, installed_check=_check(True))
    assert isinstance(app.state.hermes_kanban, HermesKanbanClient)
    assert client._kanban is app.state.hermes_kanban


def test_resolve_checks_installed_once_per_process() -> None:
    app = _app()
    calls: list[int] = []

    def check() -> bool:
        calls.append(1)
        return False

    for _ in range(2):
        with pytest.raises(BoardNotInstalled):
            resolve_board_client(app, installed_check=check)
    assert calls == [1]
