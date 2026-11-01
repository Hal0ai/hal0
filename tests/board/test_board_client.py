"""Tests for HermesKanbanClient — src/hal0/board/__init__.py.

Stubs the Hermes kanban dashboard with an httpx.MockTransport behind the real
client (mirrors tests/api/test_memory_admin_routes.py).

Run targeted:
    PYTHONPATH=src .venv/bin/python -m pytest tests/board/test_board_client.py -q
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from hal0.board import (
    DEFAULT_BASE_URL,
    KANBAN_BASE_PATH,
    BoardNoToken,
    BoardUnreachable,
    BoardUpstreamError,
    HermesKanbanClient,
)


class _Recorder:
    """Captures upstream requests (incl. headers); serves canned responses."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.responses: dict[tuple[str, str], httpx.Response] = {}
        self.fail_connect = False

    def respond(self, method: str, path: str, status: int, body: Any) -> None:
        self.responses[(method, path)] = httpx.Response(status, json=body)

    def respond_empty(self, method: str, path: str, status: int = 200) -> None:
        self.responses[(method, path)] = httpx.Response(status)

    def respond_text(self, method: str, path: str, status: int, text: str) -> None:
        self.responses[(method, path)] = httpx.Response(status, text=text)

    async def handler(self, request: httpx.Request) -> httpx.Response:
        if self.fail_connect:
            raise httpx.ConnectError("connection refused", request=request)
        self.requests.append(
            {
                "method": request.method,
                "path": request.url.path,
                "params": dict(request.url.params),
                "body": request.content.decode() if request.content else "",
                "headers": dict(request.headers),
            }
        )
        key = (request.method, request.url.path)
        if key in self.responses:
            return self.responses[key]
        return httpx.Response(200, json={"echo": request.url.path})


def _make_client(recorder: _Recorder, **kw: Any) -> HermesKanbanClient:
    transport = httpx.MockTransport(recorder.handler)
    http = httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9119")
    return HermesKanbanClient(http_client=http, **kw)


@pytest.fixture(autouse=True)
def _token_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Default resolver reads a tmp token file, never the real install's."""
    token_file = tmp_path / "default-token"
    token_file.write_text("TOK", encoding="utf-8")
    monkeypatch.setattr("hal0.board.auth.dashboard_session_token_path", lambda _a: token_file)


@pytest.fixture
def recorder() -> _Recorder:
    return _Recorder()


# ── from_env ─────────────────────────────────────────────────────────────────


def test_from_env_default_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HERMES_DASHBOARD_BASE_URL", raising=False)
    client = HermesKanbanClient.from_env()
    assert client._base_url == DEFAULT_BASE_URL


def test_from_env_reads_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_DASHBOARD_BASE_URL", "http://192.0.2.142:9119")
    client = HermesKanbanClient.from_env()
    assert client._base_url == "http://192.0.2.142:9119"


# ── prefix + forwarding ────────────────────────────────────────────────────


async def test_request_json_prepends_kanban_prefix(recorder: _Recorder) -> None:
    client = _make_client(recorder)
    await client.request_json("GET", "/board")
    assert recorder.requests[-1]["path"] == f"{KANBAN_BASE_PATH}/board"


async def test_request_json_forwards_params_and_body(recorder: _Recorder) -> None:
    client = _make_client(recorder)
    await client.request_json("POST", "/tasks", params={"board": "alpha"}, json_body={"title": "t"})
    fwd = recorder.requests[-1]
    assert fwd["path"] == f"{KANBAN_BASE_PATH}/tasks"
    assert fwd["params"] == {"board": "alpha"}
    assert '"title"' in fwd["body"]


async def test_empty_200_body_returns_empty_dict(recorder: _Recorder) -> None:
    recorder.respond_empty("DELETE", f"{KANBAN_BASE_PATH}/tasks/t1", 200)
    client = _make_client(recorder)
    result = await client.request_json("DELETE", "/tasks/t1")
    assert result == {}


# ── headers (auth) ─────────────────────────────────────────────────────────


async def test_headers_inject_token_both_forms(recorder: _Recorder) -> None:
    client = _make_client(recorder, token_resolver=lambda: "TOK")
    await client.request_json("GET", "/board")
    h = recorder.requests[-1]["headers"]
    assert h["x-hermes-session-token"] == "TOK"
    assert h["authorization"] == "Bearer TOK"
    assert h["x-hal0-agent"] == "hermes"


async def test_agent_id_override_outbound(recorder: _Recorder) -> None:
    client = _make_client(recorder, token_resolver=lambda: "TOK")
    await client.request_json("GET", "/board", agent_id="claude-dev")
    assert recorder.requests[-1]["headers"]["x-hal0-agent"] == "claude-dev"


async def test_default_agent_id_from_env(
    recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAL0_AGENT_ID", "ops")
    client = _make_client(recorder)
    await client.request_json("GET", "/board")
    assert recorder.requests[-1]["headers"]["x-hal0-agent"] == "ops"


# ── error mapping ──────────────────────────────────────────────────────────


async def test_upstream_4xx_passes_status_through(recorder: _Recorder) -> None:
    recorder.respond("GET", f"{KANBAN_BASE_PATH}/tasks/ghost", 404, {"detail": "no task"})
    client = _make_client(recorder)
    with pytest.raises(BoardUpstreamError) as ei:
        await client.request_json("GET", "/tasks/ghost")
    assert ei.value.status == 404
    assert ei.value.code == "board.upstream_error"


async def test_upstream_5xx_maps_to_502(recorder: _Recorder) -> None:
    recorder.respond("GET", f"{KANBAN_BASE_PATH}/board", 500, {"detail": "boom"})
    client = _make_client(recorder)
    with pytest.raises(BoardUpstreamError) as ei:
        await client.request_json("GET", "/board")
    assert ei.value.status == 502


async def test_transport_failure_maps_to_503(recorder: _Recorder) -> None:
    recorder.fail_connect = True
    client = _make_client(recorder)
    with pytest.raises(BoardUnreachable) as ei:
        await client.request_json("GET", "/board")
    assert ei.value.status == 503
    assert ei.value.code == "board.unreachable"


# ── token file resolution ────────────────────────────────────────────────────


async def test_no_token_raises_before_request(recorder: _Recorder) -> None:
    def _missing() -> str:
        raise BoardNoToken("no token file")

    client = _make_client(recorder, token_resolver=_missing)
    with pytest.raises(BoardNoToken):
        await client.request_json("GET", "/board")
    assert recorder.requests == []


async def test_blank_token_never_sent(recorder: _Recorder) -> None:
    client = _make_client(recorder, token_resolver=lambda: "  ")
    with pytest.raises(BoardNoToken):
        await client.request_json("GET", "/board")
    assert recorder.requests == []


async def test_default_resolver_reads_token_file(
    recorder: _Recorder, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    token_file = tmp_path / "dashboard-token"
    token_file.write_text("FROMFILE\n", encoding="utf-8")
    monkeypatch.setattr("hal0.board.auth.dashboard_session_token_path", lambda _agent: token_file)
    client = _make_client(recorder)
    await client.request_json("GET", "/board")
    assert recorder.requests[-1]["headers"]["authorization"] == "Bearer FROMFILE"


async def test_401_rereads_token_once_then_succeeds() -> None:
    tokens = iter(["old", "new"])
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        sent = request.headers["authorization"]
        seen.append(sent)
        if sent == "Bearer old":
            return httpx.Response(401, json={"detail": "Unauthorized"})
        return httpx.Response(200, json={"columns": []})

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:9119"
    )
    client = HermesKanbanClient(http_client=http, token_resolver=lambda: next(tokens))
    assert await client.request_json("GET", "/board") == {"columns": []}
    assert seen == ["Bearer old", "Bearer new"]


async def test_second_401_surfaces_upstream_error() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"detail": "Unauthorized"})

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:9119"
    )
    client = HermesKanbanClient(http_client=http, token_resolver=lambda: "tok")
    with pytest.raises(BoardUpstreamError) as ei:
        await client.request_json("GET", "/board")
    assert ei.value.status == 401
    assert calls == 2  # initial + exactly one retry, never a loop
