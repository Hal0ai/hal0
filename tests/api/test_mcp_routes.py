"""Integration tests for the ``/api/mcp/*`` REST surface (issue #206).

The orchestrator's full ``create_app()`` mounts the FastMCP sub-apps,
which we don't want to spin up for a route-shape test. Instead we mount
the router on a bare FastAPI app and either stub ``app.state.mcp_servers``
with a tiny fake (for the introspection happy path) or leave it absent
(to exercise the empty branch).

We also stub the journald audit reader via ``monkeypatch.setattr`` so
the tests don't depend on ``journalctl`` being present on the host.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api.middleware import error_codes
from hal0.api.routes import mcp as mcp_routes


class _FakeMcpServer:
    """Minimal FastMCP stand-in — list_tools/resources/prompts return lists."""

    def __init__(
        self,
        *,
        tools: int = 3,
        resources: int = 1,
        prompts: int = 0,
    ) -> None:
        self._tools = [object()] * tools
        self._resources = [object()] * resources
        self._prompts = [object()] * prompts

    async def list_tools(self) -> list[Any]:
        return list(self._tools)

    async def list_resources(self) -> list[Any]:
        return list(self._resources)

    async def list_prompts(self) -> list[Any]:
        return list(self._prompts)


def _build_app(*, with_servers: bool = True) -> FastAPI:
    app = FastAPI()
    error_codes.install(app)
    app.include_router(mcp_routes.router, prefix="/api/mcp", tags=["mcp"])
    if with_servers:
        app.state.mcp_servers = {
            "hal0-admin": _FakeMcpServer(tools=11, resources=4, prompts=2),
            "hal0-memory": _FakeMcpServer(tools=4, resources=0, prompts=1),
        }
    return app


@pytest.fixture
def app() -> FastAPI:
    return _build_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _stub_audit(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Default: empty audit log. Individual tests override per-call."""
    rows: list[dict[str, Any]] = []

    async def _fake(**kwargs: Any) -> list[dict[str, Any]]:
        if "server_filter" in kwargs and kwargs["server_filter"] is not None:
            return [r for r in rows if r.get("server") == kwargs["server_filter"]]
        return rows

    monkeypatch.setattr(mcp_routes, "_read_audit_events", _fake)
    return rows


# ── GET /api/mcp/servers ─────────────────────────────────────────────────────


def test_servers_returns_introspected_counts(client: TestClient) -> None:
    response = client.get("/api/mcp/servers")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 2
    by_id = {s["id"]: s for s in body["servers"]}
    assert by_id["hal0-admin"]["tools"] == 11
    assert by_id["hal0-admin"]["resources"] == 4
    assert by_id["hal0-admin"]["prompts"] == 2
    assert by_id["hal0-admin"]["bundled"] is True
    assert by_id["hal0-admin"]["state"] == "running"
    assert by_id["hal0-admin"]["transport"] == "streamable-http"
    assert by_id["hal0-admin"]["connect_url"].endswith("/mcp/admin")
    assert by_id["hal0-memory"]["connect_url"].endswith("/mcp/memory")
    assert by_id["hal0-memory"]["tools"] == 4


def test_servers_empty_when_state_absent() -> None:
    app = _build_app(with_servers=False)
    with TestClient(app) as client:
        response = client.get("/api/mcp/servers")
    assert response.status_code == 200
    assert response.json() == {"servers": [], "count": 0}


def test_servers_surfaces_recent_rpm(
    client: TestClient,
    _stub_audit: list[dict[str, Any]],
) -> None:
    now = time.time()
    _stub_audit.extend(
        [
            {
                "event": "mcp.tool.invoked",
                "server": "hal0-admin",
                "tool": "slot_list",
                "client_id": "claude-code",
                "timestamp": now - 5,
                "args": {},
                "outcome": "invoked",
                "gated": False,
            }
        ]
        * 3
    )
    response = client.get("/api/mcp/servers")
    by_id = {s["id"]: s for s in response.json()["servers"]}
    assert by_id["hal0-admin"]["activity"]["rpm"] == 3
    assert "claude-code" in by_id["hal0-admin"]["connected"]


# ── GET /api/mcp/clients ─────────────────────────────────────────────────────


def test_clients_derives_from_audit_log(
    client: TestClient,
    _stub_audit: list[dict[str, Any]],
) -> None:
    now = time.time()
    _stub_audit.extend(
        [
            {
                "event": "mcp.tool.invoked",
                "server": "hal0-admin",
                "tool": "slot_list",
                "client_id": "claude-code",
                "timestamp": now - 30,
                "args": {},
                "outcome": "invoked",
                "gated": False,
            },
            {
                "event": "mcp.tool.invoked",
                "server": "hal0-memory",
                "tool": "memory_search",
                "client_id": "claude-code",
                "timestamp": now - 10,
                "args": {},
                "outcome": "invoked",
                "gated": False,
            },
            {
                "event": "mcp.tool.invoked",
                "server": "hal0-memory",
                "tool": "memory_search",
                "client_id": "cursor",
                "timestamp": now - 5,
                "args": {},
                "outcome": "invoked",
                "gated": False,
            },
            # anonymous gets dropped
            {
                "event": "mcp.tool.invoked",
                "server": "hal0-admin",
                "tool": "version_info",
                "client_id": "anonymous",
                "timestamp": now - 1,
                "args": {},
                "outcome": "invoked",
                "gated": False,
            },
        ]
    )
    response = client.get("/api/mcp/clients")
    body = response.json()
    assert body["count"] == 2
    by_id = {c["id"]: c for c in body["clients"]}
    assert by_id["claude-code"]["name"] == "Claude Code"
    assert by_id["claude-code"]["role"] == "CLI"
    assert sorted(by_id["claude-code"]["connected_to"]) == ["hal0-admin", "hal0-memory"]
    assert by_id["cursor"]["name"] == "Cursor"
    assert by_id["cursor"]["role"] == "IDE"


def test_clients_empty_when_no_audit(client: TestClient) -> None:
    response = client.get("/api/mcp/clients")
    assert response.status_code == 200
    assert response.json() == {"clients": [], "count": 0}


# ── GET /api/mcp/catalog ─────────────────────────────────────────────────────


def test_catalog_returns_static_items(client: TestClient) -> None:
    response = client.get("/api/mcp/catalog")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["items"], list)
    assert len(body["items"]) >= 8
    # Shape check — first item must carry the keys the dashboard reads.
    first = body["items"][0]
    for key in ("name", "author", "verified", "description", "tools", "category"):
        assert key in first
    assert isinstance(body["categories"], list)
    assert "Files" in body["categories"]


# ── GET /api/mcp/{id}/logs ───────────────────────────────────────────────────


def test_server_logs_filters_by_server(
    client: TestClient,
    _stub_audit: list[dict[str, Any]],
) -> None:
    now = time.time()
    _stub_audit.extend(
        [
            {
                "event": "mcp.tool.invoked",
                "server": "hal0-admin",
                "tool": "slot_list",
                "client_id": "claude-code",
                "timestamp": now,
                "args": {},
                "outcome": "invoked",
                "gated": False,
            },
            {
                "event": "mcp.tool.invoked",
                "server": "hal0-memory",
                "tool": "memory_search",
                "client_id": "cursor",
                "timestamp": now,
                "args": {},
                "outcome": "invoked",
                "gated": False,
            },
        ]
    )
    response = client.get("/api/mcp/hal0-admin/logs")
    body = response.json()
    assert body["server"] == "hal0-admin"
    assert body["count"] == 1
    assert body["events"][0]["tool"] == "slot_list"


# ── 501 stubs ────────────────────────────────────────────────────────────────


def test_install_returns_501(client: TestClient) -> None:
    response = client.post("/api/mcp/install", json={"name": "filesystem"})
    assert response.status_code == 501
    body = response.json()
    assert body["error"]["code"] == "mcp.not_implemented"
    assert "ADR-0013" in body["error"]["message"]


def test_uninstall_returns_501(client: TestClient) -> None:
    response = client.delete("/api/mcp/filesystem")
    assert response.status_code == 501
    assert response.json()["error"]["code"] == "mcp.not_implemented"


def test_action_returns_501(client: TestClient) -> None:
    response = client.post("/api/mcp/hal0-admin/restart")
    assert response.status_code == 501
    assert response.json()["error"]["code"] == "mcp.not_implemented"


def test_config_patch_returns_501(client: TestClient) -> None:
    response = client.patch(
        "/api/mcp/hal0-admin/config",
        json={"env": {"FOO": "bar"}},
    )
    assert response.status_code == 501
    body = response.json()
    assert body["error"]["code"] == "mcp.not_implemented"
    assert body["error"]["details"]["patch"] == {"env": {"FOO": "bar"}}
