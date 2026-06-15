"""Tests for HermesKanbanClient — src/hal0/board/__init__.py.

Stubs the Hermes kanban dashboard with an httpx.MockTransport behind the real
client (mirrors tests/api/test_memory_admin_routes.py).

Run targeted:
    PYTHONPATH=src .venv/bin/python -m pytest tests/board/test_board_client.py -q
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from hal0.board import (
    DEFAULT_BASE_URL,
    KANBAN_BASE_PATH,
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


@pytest.fixture
def recorder() -> _Recorder:
    return _Recorder()


# ── from_env ─────────────────────────────────────────────────────────────────


def test_from_env_default_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HERMES_DASHBOARD_BASE_URL", raising=False)
    client = HermesKanbanClient.from_env()
    assert client._base_url == DEFAULT_BASE_URL


def test_from_env_reads_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_DASHBOARD_BASE_URL", "http://10.0.1.142:9119")
    client = HermesKanbanClient.from_env()
    assert client._base_url == "http://10.0.1.142:9119"


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
    client = _make_client(recorder, session_token_resolver=lambda: "TOK")
    await client.request_json("GET", "/board")
    h = recorder.requests[-1]["headers"]
    assert h["x-hermes-session-token"] == "TOK"
    assert h["authorization"] == "Bearer TOK"
    assert h["x-hal0-agent"] == "hermes"


async def test_headers_no_token_omits_auth(recorder: _Recorder) -> None:
    client = _make_client(recorder, session_token_resolver=lambda: None)
    await client.request_json("GET", "/board")
    h = recorder.requests[-1]["headers"]
    assert "x-hermes-session-token" not in h
    assert "authorization" not in h


async def test_agent_id_override_outbound(recorder: _Recorder) -> None:
    client = _make_client(recorder, session_token_resolver=lambda: "TOK")
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


# ── token harvest from dashboard HTML (the default, rotation-proof resolver) ──

_HTML = '<!doctype html><script>window.__HERMES_SESSION_TOKEN__="{tok}";</script>'


async def test_harvests_token_from_dashboard_html(
    recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HERMES_SESSION_TOKEN", raising=False)
    recorder.respond_text("GET", "/", 200, _HTML.format(tok="HARVESTED"))
    client = _make_client(recorder)  # default resolver → no env pin → harvest
    await client.request_json("GET", "/board")
    # the dashboard HTML was fetched, then /board carried the harvested bearer
    assert ("GET", "/") in [(r["method"], r["path"]) for r in recorder.requests]
    board_req = next(r for r in recorder.requests if r["path"] == f"{KANBAN_BASE_PATH}/board")
    assert board_req["headers"]["authorization"] == "Bearer HARVESTED"
    assert board_req["headers"]["x-hermes-session-token"] == "HARVESTED"


async def test_harvested_token_is_cached(
    recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HERMES_SESSION_TOKEN", raising=False)
    recorder.respond_text("GET", "/", 200, _HTML.format(tok="ONCE"))
    client = _make_client(recorder)
    await client.request_json("GET", "/board")
    await client.request_json("GET", "/stats")
    html_fetches = [r for r in recorder.requests if r["path"] == "/"]
    assert len(html_fetches) == 1  # cached across requests


async def test_env_pin_skips_html_harvest(
    recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HERMES_SESSION_TOKEN", "PINNED")
    recorder.respond_text("GET", "/", 200, _HTML.format(tok="HARVESTED"))
    client = _make_client(recorder)  # default resolver reads the env pin
    await client.request_json("GET", "/board")
    assert all(r["path"] != "/" for r in recorder.requests)  # no harvest
    board_req = next(r for r in recorder.requests if r["path"] == f"{KANBAN_BASE_PATH}/board")
    assert board_req["headers"]["authorization"] == "Bearer PINNED"


async def test_401_reharvests_token_and_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HERMES_SESSION_TOKEN", raising=False)
    # Hermes restarted: HTML now serves a rotated token; the cached one 401s once.
    state = {"board_calls": 0, "token": "ROTATED"}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(200, text=_HTML.format(tok=state["token"]))
        if request.url.path == f"{KANBAN_BASE_PATH}/board":
            state["board_calls"] += 1
            sent = request.headers.get("authorization")
            # first attempt presents the (now-stale) token → 401; retry succeeds
            if state["board_calls"] == 1:
                return httpx.Response(401, json={"detail": "Unauthorized"})
            assert sent == "Bearer ROTATED"
            return httpx.Response(200, json={"columns": []})
        return httpx.Response(200, json={})

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:9119"
    )
    client = HermesKanbanClient(http_client=http)
    result = await client.request_json("GET", "/board")
    assert result == {"columns": []}
    assert state["board_calls"] == 2  # initial + one retry
