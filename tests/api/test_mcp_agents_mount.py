"""``/mcp/agents`` end to end on the real app (ADR-0043).

``create_app()`` with its lifespan and auth on: a bridge key opens the mount, a
caller with no key does not, and the tool list matches ``GET /api/bridge/me``.
Calls are made from ``Host: 127.0.0.1``, which the transport's DNS-rebinding
allowlist always admits.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hal0.api import auth as auth_mod
from hal0.api import create_app

ADMIN_KEY = "admin-key-for-agents-mount"

MCP_HEADERS = {
    "Host": "127.0.0.1:8080",
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


def _write_hal0_toml(body: str) -> None:
    etc = Path(os.environ["HAL0_HOME"]) / "etc" / "hal0"
    etc.mkdir(parents=True, exist_ok=True)
    (etc / "hal0.toml").write_text(body)


@pytest.fixture(autouse=True)
def _auth_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("HAL0_REQUIRE_AUTH", "1")
    monkeypatch.setenv("HAL0_ADMIN_KEY", ADMIN_KEY)
    auth_mod._require_auth_cache = None
    yield
    auth_mod._require_auth_cache = None


@pytest.fixture
def client() -> Iterator[TestClient]:
    _write_hal0_toml("")
    with TestClient(create_app(), base_url="http://127.0.0.1:8080") as c:
        yield c


def _rpc(
    client: TestClient,
    key: str | None,
    method: str,
    params: dict[str, Any],
    rid: int = 1,
    session: str | None = None,
) -> Any:
    headers = dict(MCP_HEADERS)
    if key is not None:
        headers["Authorization"] = f"Bearer {key}"
    if session is not None:
        headers["mcp-session-id"] = session
    return client.post(
        "/mcp/agents/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params},
    )


def _result(resp: Any) -> dict[str, Any]:
    """The JSON-RPC ``result`` of a plain-JSON or single-event SSE response."""
    text = resp.text
    if resp.headers.get("content-type", "").startswith("text/event-stream"):
        text = next(line[6:] for line in text.splitlines() if line.startswith("data: "))
    body: dict[str, Any] = json.loads(text)
    assert "error" not in body, body
    result: dict[str, Any] = body["result"]
    return result


_INIT = {
    "protocolVersion": "2025-03-26",
    "capabilities": {},
    "clientInfo": {"name": "probe", "version": "0"},
}


def _mint(client: TestClient) -> str:
    admin = {"Authorization": f"Bearer {ADMIN_KEY}"}
    resp = client.post("/api/bridge/clients", json={"name": "cc"}, headers=admin)
    assert resp.status_code in (200, 201), resp.text
    return str(resp.json()["key"])


def test_a_bridge_key_lists_the_sixteen_tools(client: TestClient) -> None:
    key = _mint(client)
    init = _rpc(client, key, "initialize", _INIT)
    assert init.status_code == 200, init.text
    session = init.headers["mcp-session-id"]
    listed = _rpc(client, key, "tools/list", {}, rid=2, session=session)
    assert listed.status_code == 200, listed.text
    names = sorted(t["name"] for t in _result(listed)["tools"])
    me = client.get("/api/bridge/me", headers={"Authorization": f"Bearer {key}"}).json()
    assert names == sorted(me["tools"]) and len(names) == 16  # 9 run tools + 7 teach tools


class _Service:
    """A BridgeService stand-in that names who was asking."""

    def __init__(self) -> None:
        self.callers: list[Any] = []

    async def list_runs(self, caller: Any, **_kw: Any) -> list[dict[str, Any]]:
        self.callers.append((caller.tier, caller.name))
        return []


def _call(client: TestClient, key: str, tool: str) -> Any:
    session = _rpc(client, key, "initialize", _INIT).headers["mcp-session-id"]
    return _rpc(
        client,
        key,
        "tools/call",
        {"name": tool, "arguments": {}},
        rid=3,
        session=session,
    )


def test_a_tool_call_acts_as_the_key_holder(client: TestClient) -> None:
    """The principal comes off the live request: the bridge client, or the admin key."""
    key = _mint(client)
    service = _Service()
    client.app.state.bridge_service = service  # type: ignore[attr-defined]
    for who in (key, ADMIN_KEY):
        resp = _call(client, who, "agent_runs_list")
        assert resp.status_code == 200, resp.text
        assert _result(resp)["isError"] is False
    assert service.callers == [("bridge", "cc"), ("admin", "admin")]


def test_a_tool_call_before_the_service_exists_is_bridge_unavailable(client: TestClient) -> None:
    """Mounted, so the bridge is on: no service yet is 503 ``bridge.unavailable``, as REST says."""
    key = _mint(client)
    client.app.state.bridge_service = None  # type: ignore[attr-defined]
    result = _result(_call(client, key, "agent_runs_list"))
    assert result["isError"] is True
    assert "bridge.unavailable" in result["content"][0]["text"]
    assert "bridge.disabled" not in result["content"][0]["text"]


def test_no_key_is_refused_before_the_mount(client: TestClient) -> None:
    assert _rpc(client, None, "initialize", _INIT).status_code == 401


def test_the_mount_is_absent_when_the_bridge_is_disabled() -> None:
    _write_hal0_toml("[bridge]\nenabled = false\n")
    with TestClient(create_app(), base_url="http://127.0.0.1:8080") as c:
        assert "hal0-agents" not in c.app.state.mcp_servers
        resp = _rpc(c, ADMIN_KEY, "initialize", _INIT)
        assert resp.status_code in (401, 404, 405)
