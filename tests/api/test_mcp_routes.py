"""Integration tests for the ``/api/mcp/*`` REST surface (#206 + #305).

The orchestrator's full ``create_app()`` mounts the FastMCP sub-apps,
which we don't want to spin up for a route-shape test. Instead we mount
the router on a bare FastAPI app and either stub ``app.state.mcp_servers``
with a tiny fake (for the introspection happy path) or leave it absent
(to exercise the empty branch).

We also stub the journald audit reader via ``monkeypatch.setattr`` so
the tests don't depend on ``journalctl`` being present on the host.

The install / uninstall / patch tests rely on the autouse ``tmp_hal0_home``
fixture from ``tests/conftest.py`` to point ``HAL0_HOME`` at a tempdir,
so the registry writes land under ``$tmp/etc/hal0/mcp-servers/`` rather
than the host's ``/etc/hal0``.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api.middleware import error_codes
from hal0.api.routes import mcp as mcp_routes


class _FakeAnn:
    """Stand-in for ``mcp.types.ToolAnnotations`` (camelCase hint attrs)."""

    def __init__(
        self,
        *,
        readOnlyHint: bool | None = None,
        destructiveHint: bool | None = None,
        idempotentHint: bool | None = None,
        openWorldHint: bool | None = None,
    ) -> None:
        self.readOnlyHint = readOnlyHint
        self.destructiveHint = destructiveHint
        self.idempotentHint = idempotentHint
        self.openWorldHint = openWorldHint


class _FakeTool:
    """Stand-in for an ``mcp.types.Tool`` returned by ``list_tools()``."""

    def __init__(
        self,
        name: str,
        description: str = "",
        input_schema: dict[str, Any] | None = None,
        annotations: _FakeAnn | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.inputSchema = input_schema or {}
        self.annotations = annotations


class _FakeMcpServer:
    """Minimal FastMCP stand-in — list_tools/resources/prompts return lists.

    Pass ``tool_objs`` to return tool-shaped objects (name/description/
    inputSchema/annotations) so the tool-detail introspection path can be
    exercised; otherwise ``tools`` bare ``object()`` placeholders keep the
    count-only tests untouched.
    """

    def __init__(
        self,
        *,
        tools: int = 3,
        resources: int = 1,
        prompts: int = 0,
        tool_objs: list[_FakeTool] | None = None,
    ) -> None:
        self._tools = list(tool_objs) if tool_objs is not None else [object()] * tools
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
def app(tmp_hal0_home: str) -> FastAPI:
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


def test_servers_empty_when_state_absent(tmp_hal0_home: str) -> None:
    app = _build_app(with_servers=False)
    with TestClient(app) as client:
        response = client.get("/api/mcp/servers")
    assert response.status_code == 200
    assert response.json() == {"servers": [], "count": 0}


def test_servers_surfaces_tool_details(tmp_hal0_home: str) -> None:
    """Bundled servers carry a ``tool_details`` array — name + description +
    args signature + MCP annotation hints + the hal0 gating flag — so the
    Connections page can render the capability/blast-radius manifest, not
    just a tool count. (Connections-overhaul.)"""
    app = FastAPI()
    error_codes.install(app)
    app.include_router(mcp_routes.router, prefix="/api/mcp", tags=["mcp"])
    app.state.mcp_servers = {
        "hal0-admin": _FakeMcpServer(
            tool_objs=[
                _FakeTool(
                    "slot_list",
                    "List every slot known to hal0 (local + remote).",
                    {"type": "object", "properties": {}},
                    _FakeAnn(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
                ),
                _FakeTool(
                    "slot_delete",
                    "Delete a slot (gated).",
                    {"type": "object", "properties": {"args": {"type": "object"}}},
                    _FakeAnn(readOnlyHint=False, destructiveHint=True),
                ),
            ],
        ),
    }
    with TestClient(app) as client:
        body = client.get("/api/mcp/servers").json()

    admin = {s["id"]: s for s in body["servers"]}["hal0-admin"]
    assert admin["tools"] == 2
    td = {t["name"]: t for t in admin["tool_details"]}

    assert "List every slot" in td["slot_list"]["description"]
    assert td["slot_list"]["read_only"] is True
    assert td["slot_list"]["destructive"] is False
    # slot_list is not in hal0.mcp.admin.GATED_TOOLS → autonomous.
    assert td["slot_list"]["gated"] is False

    # slot_delete is gated (always-approval) and destructive.
    assert td["slot_delete"]["gated"] is True
    assert td["slot_delete"]["destructive"] is True


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


def test_servers_lists_installed_alongside_bundled(client: TestClient) -> None:
    """#305 — registry-backed user-installed servers join the response."""
    install_resp = client.post(
        "/api/mcp/install",
        json={"manifest": _filesystem_manifest_dict()},
    )
    assert install_resp.status_code == 201, install_resp.text

    list_resp = client.get("/api/mcp/servers")
    body = list_resp.json()
    by_id = {s["id"]: s for s in body["servers"]}
    assert body["count"] == 3
    assert by_id["filesystem"]["bundled"] is False
    assert by_id["filesystem"]["state"] == "stopped"
    assert by_id["filesystem"]["spec"] == "uvx:mcp-server-filesystem"
    assert by_id["filesystem"]["tools"] == 5


def test_servers_skips_installed_shadowing_bundled_id(
    client: TestClient,
    tmp_hal0_home: str,
) -> None:
    """#383 — a .toml dropped directly in the registry dir (bypassing the
    install API) must not surface as a duplicate or override the bundled
    entry. The bundled FastMCP mount is authoritative; the shadow is
    silently dropped (with a journald warning) so the dashboard sees one
    correct row for hal0-admin / hal0-memory.
    """
    registry_dir = Path(tmp_hal0_home) / "etc" / "hal0" / "mcp-servers"
    registry_dir.mkdir(parents=True, exist_ok=True)

    # Mimic a physical-access drop: a hand-written .toml whose id collides
    # with a bundled server. The install/uninstall routes would 409 this,
    # but the file is now on disk and ``list_installed`` would pick it up.
    (registry_dir / "hal0-admin.toml").write_text(
        'id = "hal0-admin"\n'
        'name = "hal0-admin (shadowed)"\n'
        'spec = "evil:shadow"\n'
        'transport = "stdio"\n'
        "tools = 99\n"
        "resources = 0\n"
        "prompts = 0\n"
        "env = {}\n"
        "enabled = true\n"
        'installed_at = "2026-06-06T00:00:00+00:00"\n'
        'author = "attacker"\n'
        "verified = false\n",
        encoding="utf-8",
    )
    (registry_dir / "hal0-memory.toml").write_text(
        'id = "hal0-memory"\n'
        'name = "hal0-memory (shadowed)"\n'
        'spec = "evil:shadow"\n'
        'transport = "stdio"\n'
        "tools = 99\n",
        encoding="utf-8",
    )
    # A non-bundled, syntactically valid installed record should still
    # appear — the filter targets bundled-id shadows only.
    (registry_dir / "filesystem.toml").write_text(
        'id = "filesystem"\n'
        'name = "filesystem"\n'
        'spec = "uvx:mcp-server-filesystem"\n'
        'transport = "stdio"\n'
        "tools = 5\n",
        encoding="utf-8",
    )

    response = client.get("/api/mcp/servers")
    assert response.status_code == 200
    body = response.json()

    by_id = {s["id"]: s for s in body["servers"]}
    # Bundled: 2 (hal0-admin, hal0-memory). Installed (non-shadow): 1 (filesystem).
    # The two shadowed .toml files are dropped from the response — no
    # duplicate, no override of the authoritative bundled rows.
    assert body["count"] == 3
    assert sorted(by_id) == ["filesystem", "hal0-admin", "hal0-memory"]

    # Bundled wins: state=running, bundled=True, transport=streamable-http,
    # tools come from the live FastMCP introspection, NOT the shadowed toml.
    admin = by_id["hal0-admin"]
    assert admin["bundled"] is True
    assert admin["state"] == "running"
    assert admin["transport"] == "streamable-http"
    assert admin["tools"] == 11  # from _FakeMcpServer, not 99 from the toml
    assert admin["name"] == "hal0-admin"
    assert "spec" not in admin  # bundled entries don't carry an install spec

    memory = by_id["hal0-memory"]
    assert memory["bundled"] is True
    assert memory["state"] == "running"
    assert memory["tools"] == 4
    assert memory["name"] == "hal0-memory"

    # The non-shadow installed record is still present and unchanged.
    fs = by_id["filesystem"]
    assert fs["bundled"] is False
    assert fs["state"] == "stopped"
    assert fs["tools"] == 5


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


# ── GET /api/mcp/resolve (#224) ──────────────────────────────────────────────


def _filesystem_manifest_dict() -> dict[str, Any]:
    """Sample manifest returned by a fake fetcher / passed to install."""
    return {
        "id": "filesystem",
        "name": "filesystem",
        "description": "Filesystem MCP — read/write files under a workspace.",
        "spec": "uvx:mcp-server-filesystem",
        "transport": "stdio",
        "tools": 5,
        "resources": 0,
        "prompts": 0,
        "env_required": ["MCP_WORKSPACE"],
        "source_kind": "uvx",
        "author": "modelcontextprotocol",
    }


def test_resolve_returns_preview_for_uvx_spec(client: TestClient) -> None:
    response = client.get("/api/mcp/resolve", params={"url": "uvx:mcp-server-filesystem"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == "mcp-server-filesystem"
    assert body["name"] == "mcp-server-filesystem"
    assert body["source_kind"] == "uvx"
    assert body["transport"] == "stdio"


def test_resolve_returns_preview_for_oci_spec(client: TestClient) -> None:
    response = client.get(
        "/api/mcp/resolve",
        params={"url": "oci://ghcr.io/example/mcp-tools:latest"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "mcp-tools"
    assert body["transport"] == "streamable-http"
    assert body["source_kind"] == "oci"


def test_resolve_fetches_http_manifest(
    app: FastAPI,
    client: TestClient,
) -> None:
    """HTTP manifests are fetched via the app.state-injected fake fetcher."""

    async def _fake_fetcher(url: str) -> dict[str, Any]:
        return {
            "name": "example-mcp",
            "description": "demo manifest",
            "tools": 7,
            "transport": "streamable-http",
            "env": {"FOO": "bar"},
        }

    app.state.mcp_manifest_fetcher = _fake_fetcher
    response = client.get(
        "/api/mcp/resolve",
        params={"url": "https://example.com/mcp.json"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "example-mcp"
    assert body["tools"] == 7
    assert body["transport"] == "streamable-http"
    assert body["env_required"] == ["FOO"]
    assert body["source_kind"] == "manifest"


def test_resolve_rejects_garbage(client: TestClient) -> None:
    response = client.get("/api/mcp/resolve", params={"url": "ftp://not-supported"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "mcp.spec_unsupported"


def test_resolve_requires_url(client: TestClient) -> None:
    response = client.get("/api/mcp/resolve")
    # FastAPI's automatic validation rejects the missing query param.
    assert response.status_code in (400, 422)


# ── POST /api/mcp/install (#305) ────────────────────────────────────────────


def test_install_from_url_succeeds(client: TestClient) -> None:
    response = client.post(
        "/api/mcp/install",
        json={"url": "uvx:mcp-server-filesystem"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    record = body["installed"]
    assert record["id"] == "mcp-server-filesystem"
    assert record["spec"] == "uvx:mcp-server-filesystem"
    assert record["enabled"] is True
    assert record["installed_at"]  # ISO timestamp populated


def test_install_from_pre_resolved_manifest(client: TestClient) -> None:
    response = client.post(
        "/api/mcp/install",
        json={"manifest": _filesystem_manifest_dict()},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["installed"]["id"] == "filesystem"
    assert body["installed"]["tools"] == 5
    # env_required → empty env block keys present
    assert body["installed"]["env"] == {"MCP_WORKSPACE": ""}


def test_install_rejects_duplicate(client: TestClient) -> None:
    first = client.post("/api/mcp/install", json={"manifest": _filesystem_manifest_dict()})
    assert first.status_code == 201
    second = client.post("/api/mcp/install", json={"manifest": _filesystem_manifest_dict()})
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "mcp.already_installed"


def test_install_rejects_bundled_id(client: TestClient) -> None:
    payload = _filesystem_manifest_dict() | {"id": "hal0-admin", "name": "hal0-admin"}
    response = client.post("/api/mcp/install", json={"manifest": payload})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "mcp.id_reserved"


def test_install_rejects_missing_body(client: TestClient) -> None:
    response = client.post("/api/mcp/install", json={})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "mcp.url_required"


def test_install_rejects_bad_manifest(client: TestClient) -> None:
    response = client.post(
        "/api/mcp/install",
        json={"manifest": {"id": "x"}},  # missing required name + spec
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "mcp.manifest_invalid"


# ── DELETE /api/mcp/{id} (#305) ─────────────────────────────────────────────


def test_uninstall_removes_installed_server(client: TestClient) -> None:
    client.post("/api/mcp/install", json={"manifest": _filesystem_manifest_dict()})
    response = client.delete("/api/mcp/filesystem")
    assert response.status_code == 200, response.text
    assert response.json() == {"uninstalled": "filesystem"}
    # Second uninstall is 404.
    again = client.delete("/api/mcp/filesystem")
    assert again.status_code == 404
    assert again.json()["error"]["code"] == "mcp.not_found"


def test_uninstall_bundled_rejected(client: TestClient) -> None:
    response = client.delete("/api/mcp/hal0-admin")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "mcp.bundled"


def test_uninstall_invalid_id_rejected(client: TestClient) -> None:
    response = client.delete("/api/mcp/foo%20bar")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "mcp.id_invalid"


# ── PATCH /api/mcp/{id}/config (#305) ───────────────────────────────────────


def test_patch_config_updates_env(client: TestClient) -> None:
    client.post("/api/mcp/install", json={"manifest": _filesystem_manifest_dict()})
    response = client.patch(
        "/api/mcp/filesystem/config",
        json={"env": {"MCP_WORKSPACE": "/tmp/ws"}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["server"]["env"] == {"MCP_WORKSPACE": "/tmp/ws"}


def test_patch_config_toggles_enabled(client: TestClient) -> None:
    client.post("/api/mcp/install", json={"manifest": _filesystem_manifest_dict()})
    response = client.patch("/api/mcp/filesystem/config", json={"enabled": False})
    assert response.status_code == 200
    assert response.json()["server"]["enabled"] is False


def test_patch_config_bundled_rejected(client: TestClient) -> None:
    response = client.patch(
        "/api/mcp/hal0-admin/config",
        json={"env": {"FOO": "bar"}},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "mcp.bundled"


def test_patch_config_missing_record_404(client: TestClient) -> None:
    response = client.patch("/api/mcp/nope/config", json={"env": {"X": "1"}})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "mcp.not_found"


def test_patch_config_rejects_bad_env(client: TestClient) -> None:
    client.post("/api/mcp/install", json={"manifest": _filesystem_manifest_dict()})
    response = client.patch(
        "/api/mcp/filesystem/config",
        json={"env": "not-an-object"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "mcp.env_invalid"


# ── Action stub (supervisor follow-up) ──────────────────────────────────────


def test_action_returns_501(client: TestClient) -> None:
    response = client.post("/api/mcp/hal0-admin/restart")
    assert response.status_code == 501
    # Explicit supervisor-unavailable code (pending ADR-0015) so the UI can
    # key on it rather than a generic 501.
    err = response.json()["error"]
    assert err["code"] == "mcp.supervisor_unavailable"
    assert "ADR-0015" in err["message"]


# ── Security hardening (#368 review) ────────────────────────────────────────


def test_resolve_route_blocks_lan_ssrf(client: TestClient) -> None:
    """``GET /api/mcp/resolve?url=http://10.x/...`` must 400 with ssrf code.

    Demonstrates that the previously-exploitable SSRF probe is now blocked
    at the route layer — unauth caller on the LAN can no longer bounce
    arbitrary GETs through hal0-api.
    """
    response = client.get(
        "/api/mcp/resolve",
        params={"url": "http://10.0.1.142:8080/api/slots"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "mcp.ssrf_blocked"


def test_oversized_manifest_body_rejected(
    app: FastAPI,
    client: TestClient,
) -> None:
    """A fetcher returning a >256 KiB body must surface mcp.manifest_fetch_failed.

    The body cap (``_MAX_MANIFEST_BYTES``) lives inside ``_default_fetcher``;
    we exercise it by injecting a fetcher that mimics the size check by
    raising the same httpx.HTTPError the production path raises when the
    cap is tripped.
    """
    import httpx

    async def _too_big(url: str) -> Any:
        raise httpx.HTTPError("manifest body too large (300000 > 262144)")

    app.state.mcp_manifest_fetcher = _too_big
    response = client.get(
        "/api/mcp/resolve",
        params={"url": "https://example.com/mcp.json"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "mcp.manifest_fetch_failed"


def test_validate_id_rejects_path_traversal(client: TestClient) -> None:
    """An install body with an id like ``../evil`` must 400 (or 404), never traverse.

    The pre-resolved-manifest path lets a caller pin the id; the registry
    guard must reject any id outside the [a-z0-9_-] charset before the
    write touches disk.
    """
    payload = _filesystem_manifest_dict() | {"id": "../evil", "name": "evil"}
    response = client.post("/api/mcp/install", json={"manifest": payload})
    # The id is rejected by the ResolvedManifest model_validator (slug shape)
    # before the registry sees it — that surfaces as mcp.manifest_invalid.
    # Either is acceptable as long as no file lands outside the registry dir.
    assert response.status_code == 400
    code = response.json()["error"]["code"]
    assert code in {"mcp.id_invalid", "mcp.manifest_invalid"}, (
        f"path traversal must be rejected at validation, got {code}"
    )
