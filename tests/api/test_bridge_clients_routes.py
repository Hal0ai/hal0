"""``/api/bridge/clients*`` and ``/api/bridge/me`` (ADR-0043, Task 4).

The REST surface behind ``hal0 bridge``: the operator mints, lists, revokes,
rotates and re-scopes bridge clients, and a client asks who it is. These tests
run the real app (``create_app()`` with its lifespan) with auth on, so the
middleware, the boot phase that opens the store and the routes are all the
production ones. ``bridge.db`` lands under the per-test ``HAL0_HOME``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hal0.api import auth as auth_mod
from hal0.api import create_app, live_sockets
from hal0.bridge.store import BridgeStore
from hal0.config import paths

ADMIN_KEY = "admin-key-for-bridge-routes"

#: The tools of the ``hal0-agents`` MCP server, as ``/api/bridge/me`` lists them.
AGENT_TOOLS = [
    "agents_list",
    "agent_run",
    "agent_run_wait",
    "agent_run_status",
    "agent_run_events",
    "agent_run_answer",
    "agent_run_steer",
    "agent_run_stop",
    "agent_runs_list",
]


def bearer(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}


def _write_hal0_toml(body: str) -> None:
    import os

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
def admin_client() -> Iterator[TestClient]:
    """The real app, called with the admin key by default."""
    _write_hal0_toml("")
    with TestClient(create_app(), headers=bearer(ADMIN_KEY)) as client:
        yield client


@pytest.fixture
def disabled_client() -> Iterator[TestClient]:
    _write_hal0_toml("[bridge]\nenabled = false\n")
    with TestClient(create_app(), headers=bearer(ADMIN_KEY)) as client:
        yield client


@pytest.fixture
def revalidations(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Record every ``live_sockets.revalidate()`` call the routes make."""
    calls: list[int] = []

    def _recorder(*, tick: bool = False) -> int:
        calls.append(1)
        return 0

    monkeypatch.setattr(live_sockets, "revalidate", _recorder)
    return calls


# ---------------------------------------------------------------------------
# Clients: create, list, scopes, rotate, revoke


def test_create_lists_revokes_rotates(admin_client: TestClient) -> None:
    r = admin_client.post("/api/bridge/clients", json={"name": "claude-code", "note": "devops box"})
    assert r.status_code == 201
    body = r.json()
    key = body["key"]
    assert key.startswith("hal0bk_claude-code.")
    lines = body["onboarding"]["lines"]
    assert [line["server"] for line in lines] == ["hal0-agents", "hal0-memory"]  # default scopes
    assert all(
        line["claude_mcp_add"].endswith(f'--header "Authorization: Bearer {key}"') for line in lines
    )
    assert lines[1]["mcp_url"].endswith("/mcp/memory/mcp")
    adm = admin_client.post("/api/bridge/clients", json={"name": "ops", "scopes": ["admin"]}).json()
    assert [line["server"] for line in adm["onboarding"]["lines"]] == ["hal0-agents", "hal0-admin"]
    patched = admin_client.patch("/api/bridge/clients/ops", json={"scopes": ["memory"]})
    assert patched.json()["client"]["scopes"] == ["agents", "memory"]
    assert (
        admin_client.post("/api/bridge/clients", json={"name": "z", "scopes": ["root"]}).status_code
        == 422
    )
    # ``ops`` is listed too; the point is that ``z`` was refused and nothing was half-written.
    assert [c["name"] for c in admin_client.get("/api/bridge/clients").json()["items"]] == [
        "claude-code",
        "ops",
    ]
    assert admin_client.post("/api/bridge/clients", json={"name": "claude-code"}).status_code == 409
    me = admin_client.get("/api/bridge/me", headers=bearer(key))
    assert me.status_code == 200 and me.json()["name"] == "claude-code"
    new = admin_client.post("/api/bridge/clients/claude-code/rotate").json()["key"]
    assert admin_client.get("/api/bridge/me", headers=bearer(key)).status_code == 401
    assert admin_client.get("/api/bridge/me", headers=bearer(new)).status_code == 200
    assert admin_client.delete("/api/bridge/clients/claude-code").json()["client"]["revoked_at"]
    assert admin_client.get("/api/bridge/me", headers=bearer(new)).status_code == 401


def test_create_response_shape(admin_client: TestClient) -> None:
    body = admin_client.post(
        "/api/bridge/clients", json={"name": "cc", "scopes": ["admin", "memory"]}
    ).json()
    assert set(body) == {"client", "key", "onboarding"}
    assert body["client"]["name"] == "cc"
    assert body["client"]["scopes"] == ["admin", "agents", "memory"]
    assert body["client"]["created_by"] == "api:admin"
    assert "key_hash" not in body["client"]
    lines = body["onboarding"]["lines"]
    assert [(line["server"], line["scope"]) for line in lines] == [
        ("hal0-agents", "agents"),
        ("hal0-memory", "memory"),
        ("hal0-admin", "admin"),
    ]
    for line in lines:
        assert line["mcp_url"].endswith(f"/mcp/{line['scope']}/mcp")
        assert line["claude_mcp_add"] == (
            f"claude mcp add --transport http {line['server']} {line['mcp_url']} "
            f'--header "Authorization: Bearer {body["key"]}"'
        )


def test_create_defaults_to_agents_and_memory(admin_client: TestClient) -> None:
    body = admin_client.post("/api/bridge/clients", json={"name": "dflt"}).json()
    assert body["client"]["scopes"] == ["agents", "memory"]
    assert body["client"]["note"] == ""


def test_list_includes_revoked_flagged(admin_client: TestClient) -> None:
    admin_client.post("/api/bridge/clients", json={"name": "alive"})
    admin_client.post("/api/bridge/clients", json={"name": "gone"})
    admin_client.delete("/api/bridge/clients/gone")
    items = {c["name"]: c for c in admin_client.get("/api/bridge/clients").json()["items"]}
    assert items["alive"]["revoked_at"] is None
    assert items["gone"]["revoked_at"] is not None
    assert all("key" not in c and "key_hash" not in c for c in items.values())


def test_unknown_client_is_404(admin_client: TestClient) -> None:
    for call in (
        lambda: admin_client.delete("/api/bridge/clients/nope"),
        lambda: admin_client.post("/api/bridge/clients/nope/rotate"),
        lambda: admin_client.patch("/api/bridge/clients/nope", json={"scopes": ["memory"]}),
    ):
        r = call()
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "bridge.client_not_found"


def test_rotating_a_revoked_client_is_409(admin_client: TestClient) -> None:
    admin_client.post("/api/bridge/clients", json={"name": "cc"})
    admin_client.delete("/api/bridge/clients/cc")
    r = admin_client.post("/api/bridge/clients/cc/rotate")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "bridge.client_revoked"


def test_duplicate_name_is_409_with_the_store_code(admin_client: TestClient) -> None:
    admin_client.post("/api/bridge/clients", json={"name": "cc"})
    r = admin_client.post("/api/bridge/clients", json={"name": "cc"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "bridge.client_exists"


def test_invalid_name_422(admin_client: TestClient) -> None:
    r = admin_client.post("/api/bridge/clients", json={"name": "Bad"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "bridge.invalid_name"


def test_unknown_scope_is_the_bridge_422_not_the_pydantic_shape(admin_client: TestClient) -> None:
    r = admin_client.post("/api/bridge/clients", json={"name": "z", "scopes": ["root"]})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "bridge.invalid_scope"
    admin_client.post("/api/bridge/clients", json={"name": "cc"})
    r = admin_client.patch("/api/bridge/clients/cc", json={"scopes": ["root"]})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "bridge.invalid_scope"


@pytest.mark.parametrize(
    "name",
    [
        "hermes",
        "pi",
        "admin",
        "anonymous",
        "hal0",
        "hal0-cli",
        "hal0-doctor",
        "shared",
        "hermes__scout",
    ],
)
def test_reserved_names_are_422(admin_client: TestClient, name: str) -> None:
    """A bridge identity is the bare client name; it must not alias a bundled agent's memory."""
    r = admin_client.post("/api/bridge/clients", json={"name": name})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "bridge.invalid_name"
    assert "reserved" in r.json()["error"]["message"]
    assert admin_client.get("/api/bridge/clients").json()["items"] == []


def test_names_near_the_reserved_ones_are_allowed(admin_client: TestClient) -> None:
    for name in ("hermes2", "my-pi", "hermes-scout", "hermes_scout"):
        assert admin_client.post("/api/bridge/clients", json={"name": name}).status_code == 201


def test_malformed_body_is_a_422(admin_client: TestClient) -> None:
    assert admin_client.post("/api/bridge/clients", json={}).status_code == 422
    assert admin_client.post("/api/bridge/clients", json={"name": 5}).status_code == 422


def test_scopes_replace_and_agents_stays(admin_client: TestClient) -> None:
    admin_client.post("/api/bridge/clients", json={"name": "cc", "scopes": ["admin"]})
    r = admin_client.patch("/api/bridge/clients/cc", json={"scopes": []})
    assert r.status_code == 200
    assert r.json()["client"]["scopes"] == ["agents"]


def test_a_revoked_clients_key_stops_working_at_once(admin_client: TestClient) -> None:
    key = admin_client.post("/api/bridge/clients", json={"name": "cc"}).json()["key"]
    assert admin_client.get("/api/bridge/me", headers=bearer(key)).status_code == 200
    admin_client.delete("/api/bridge/clients/cc")
    assert admin_client.get("/api/bridge/me", headers=bearer(key)).status_code == 401


# ---------------------------------------------------------------------------
# Open sockets and streams follow a credential change


def test_revoke_and_rotate_revalidate_open_streams(
    admin_client: TestClient, revalidations: list[int]
) -> None:
    admin_client.post("/api/bridge/clients", json={"name": "cc"})
    assert revalidations == []
    admin_client.post("/api/bridge/clients/cc/rotate")
    assert len(revalidations) == 1
    admin_client.delete("/api/bridge/clients/cc")
    assert len(revalidations) == 2


def test_scope_change_revalidates_open_streams(
    admin_client: TestClient, revalidations: list[int]
) -> None:
    """Dropping a scope must also end the streams that scope was holding open."""
    admin_client.post("/api/bridge/clients", json={"name": "cc"})
    admin_client.patch("/api/bridge/clients/cc", json={"scopes": ["agents"]})
    assert len(revalidations) == 1


def test_a_refused_change_does_not_revalidate(
    admin_client: TestClient, revalidations: list[int]
) -> None:
    admin_client.delete("/api/bridge/clients/nope")
    admin_client.post("/api/bridge/clients/nope/rotate")
    assert revalidations == []


# ---------------------------------------------------------------------------
# Onboarding URL base


def test_onboarding_uses_the_request_host_and_api_port(admin_client: TestClient) -> None:
    body = admin_client.post(
        "/api/bridge/clients", json={"name": "cc"}, headers={"host": "hal0.lan:8080"}
    ).json()
    assert body["onboarding"]["lines"][0]["mcp_url"] == "http://hal0.lan:8080/mcp/agents/mcp"


def test_onboarding_follows_the_api_port_env(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAL0_PORT", "9090")
    body = admin_client.post(
        "/api/bridge/clients", json={"name": "cc"}, headers={"host": "hal0.lan"}
    ).json()
    assert body["onboarding"]["lines"][0]["mcp_url"] == "http://hal0.lan:9090/mcp/agents/mcp"


def test_onboarding_behind_a_proxy_uses_the_forwarded_origin(admin_client: TestClient) -> None:
    body = admin_client.post(
        "/api/bridge/clients",
        json={"name": "cc"},
        headers={"x-forwarded-host": "hal0.example.com", "x-forwarded-proto": "https"},
    ).json()
    assert body["onboarding"]["lines"][0]["mcp_url"] == "https://hal0.example.com/mcp/agents/mcp"


def test_onboarding_prefers_the_configured_public_url(admin_client: TestClient) -> None:
    _write_hal0_toml('[network]\npublic_url = "https://hal0.example.com/"\n')
    body = admin_client.post(
        "/api/bridge/clients",
        json={"name": "cc"},
        headers={"x-forwarded-host": "other.example.net", "x-forwarded-proto": "http"},
    ).json()
    assert body["onboarding"]["lines"][0]["mcp_url"] == "https://hal0.example.com/mcp/agents/mcp"


@pytest.mark.parametrize("host", ["127.0.0.1:8080", "localhost:8080", "[::1]:8080", "127.0.1.1"])
def test_onboarding_from_loopback_names_the_lan_address(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch, host: str
) -> None:
    """I2: ``hal0 bridge add`` calls 127.0.0.1; the lines are for another machine."""
    from hal0.config import network

    monkeypatch.setattr(network, "detect_lan_ips", lambda: ["192.0.2.10", "198.51.100.7"])
    body = admin_client.post("/api/bridge/clients", json={"name": "cc"}, headers={"host": host})
    lines = body.json()["onboarding"]["lines"]
    assert [line["mcp_url"] for line in lines] == [
        "http://192.0.2.10:8080/mcp/agents/mcp",
        "http://192.0.2.10:8080/mcp/memory/mcp",
    ]


def test_onboarding_names_the_default_route_address_not_a_container_bridge(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``detect_lan_ips`` is string-sorted, so docker0's 172.17.0.1 came first."""
    import socket
    from types import SimpleNamespace

    import psutil

    from hal0.config import network

    table = tmp_path / "route"
    table.write_text(
        "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
        "docker0\t000011AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0\n"
        "eth0\t00000000\t0101A8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0\n"
    )
    monkeypatch.setattr(network, "PROC_NET_ROUTE", table)
    monkeypatch.setattr(
        psutil,
        "net_if_addrs",
        lambda: {
            "docker0": [SimpleNamespace(family=socket.AF_INET, address="172.17.0.1")],
            "eth0": [SimpleNamespace(family=socket.AF_INET, address="192.168.1.20")],
        },
    )
    body = admin_client.post(
        "/api/bridge/clients", json={"name": "cc"}, headers={"host": "127.0.0.1:8080"}
    ).json()
    assert body["onboarding"]["lines"][0]["mcp_url"] == "http://192.168.1.20:8080/mcp/agents/mcp"


def test_onboarding_from_loopback_without_a_lan_address_keeps_loopback(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hal0.config import network

    monkeypatch.setattr(network, "detect_lan_ips", lambda: [])
    body = admin_client.post(
        "/api/bridge/clients", json={"name": "cc"}, headers={"host": "127.0.0.1:8080"}
    ).json()
    assert body["onboarding"]["lines"][0]["mcp_url"] == "http://127.0.0.1:8080/mcp/agents/mcp"


def test_onboarding_public_url_beats_the_lan_address(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hal0.config import network

    monkeypatch.setattr(network, "detect_lan_ips", lambda: ["192.0.2.10"])
    _write_hal0_toml('[network]\npublic_url = "https://hal0.example.com"\n')
    body = admin_client.post(
        "/api/bridge/clients", json={"name": "cc"}, headers={"host": "127.0.0.1:8080"}
    ).json()
    assert body["onboarding"]["lines"][0]["mcp_url"] == "https://hal0.example.com/mcp/agents/mcp"


def test_onboarding_brackets_an_ipv6_request_host(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hal0.config import network

    monkeypatch.setattr(network, "detect_lan_ips", lambda: pytest.fail("not a loopback host"))
    body = admin_client.post(
        "/api/bridge/clients", json={"name": "cc"}, headers={"host": "[2001:db8:100::5]:8080"}
    ).json()
    assert (
        body["onboarding"]["lines"][0]["mcp_url"] == "http://[2001:db8:100::5]:8080/mcp/agents/mcp"
    )


def test_rotate_returns_onboarding_lines_for_the_current_scopes(admin_client: TestClient) -> None:
    admin_client.post("/api/bridge/clients", json={"name": "cc", "scopes": ["admin"]})
    rotated = admin_client.post("/api/bridge/clients/cc/rotate")
    assert rotated.status_code == 200
    body = rotated.json()
    assert [line["server"] for line in body["onboarding"]["lines"]] == [
        "hal0-agents",
        "hal0-admin",
    ]
    assert all(body["key"] in line["claude_mcp_add"] for line in body["onboarding"]["lines"])


# ---------------------------------------------------------------------------
# GET /api/bridge/me


def test_me_as_admin(admin_client: TestClient) -> None:
    body = admin_client.get("/api/bridge/me").json()
    assert body["name"] == "admin"
    assert body["tier"] == "admin"
    assert body["scopes"] == ["agents", "memory", "admin"]
    assert body["servers"] == ["hal0-agents", "hal0-memory", "hal0-admin"]
    assert body["tools"] == AGENT_TOOLS
    assert body["created_at"] is None and body["last_seen_at"] is None


def test_me_as_a_bridge_client(admin_client: TestClient) -> None:
    key = admin_client.post("/api/bridge/clients", json={"name": "cc"}).json()["key"]
    body = admin_client.get("/api/bridge/me", headers=bearer(key)).json()
    assert body["name"] == "cc"
    assert body["tier"] == "bridge"
    assert body["scopes"] == ["agents", "memory"]
    assert body["servers"] == ["hal0-agents", "hal0-memory"]
    assert isinstance(body["created_at"], float)
    # The verify that authenticated this very request stamped the row.
    assert isinstance(body["last_seen_at"], float)
    assert body["tools"] == AGENT_TOOLS


def test_me_servers_follow_the_scopes(admin_client: TestClient) -> None:
    key = admin_client.post("/api/bridge/clients", json={"name": "cc", "scopes": ["admin"]}).json()[
        "key"
    ]
    assert admin_client.get("/api/bridge/me", headers=bearer(key)).json()["servers"] == [
        "hal0-agents",
        "hal0-admin",
    ]
    admin_client.patch("/api/bridge/clients/cc", json={"scopes": []})
    me = admin_client.get("/api/bridge/me", headers=bearer(key)).json()
    assert me["scopes"] == ["agents"] and me["servers"] == ["hal0-agents"]


def test_me_verifies_the_key_exactly_once(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I1: the middleware verifies; ``GET /me`` reads the principal it stored."""
    key = admin_client.post("/api/bridge/clients", json={"name": "cc"}).json()["key"]
    store = auth_mod.bridge_store()
    calls: list[str] = []
    real = store.verify_key

    def _counted(candidate: str) -> Any:
        calls.append(candidate)
        return real(candidate)

    monkeypatch.setattr(store, "verify_key", _counted)
    assert admin_client.get("/api/bridge/me", headers=bearer(key)).status_code == 200
    assert calls == [key]


def test_a_bridge_key_cannot_reach_the_client_routes(admin_client: TestClient) -> None:
    key = admin_client.post("/api/bridge/clients", json={"name": "cc"}).json()["key"]
    assert admin_client.get("/api/bridge/clients", headers=bearer(key)).status_code == 403
    assert (
        admin_client.post(
            "/api/bridge/clients", json={"name": "x"}, headers=bearer(key)
        ).status_code
        == 403
    )


# ---------------------------------------------------------------------------
# Disabled and unavailable


#: Off while the bridge is disabled.
_GATED: list[tuple[str, str, dict[str, Any] | None]] = [
    ("POST", "/api/bridge/clients", {"name": "cc"}),
    ("POST", "/api/bridge/clients/cc/rotate", None),
    ("PATCH", "/api/bridge/clients/cc", {"scopes": ["memory"]}),
    ("GET", "/api/bridge/me", None),
]
#: Still on while the bridge is disabled, so the operator can clean up.
_CLEANUP: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", "/api/bridge/clients", None),
    ("DELETE", "/api/bridge/clients/cc", None),
]
_ROUTES = [*_GATED, *_CLEANUP]


def _assert_disabled(r: Any) -> None:
    assert r.status_code == 404, r.text
    error = r.json()["error"]
    assert error["code"] == "bridge.disabled"
    assert error["message"] == "The agent bridge is disabled in [bridge] config."


@pytest.mark.parametrize(("method", "path", "payload"), _GATED)
def test_disabled_404(
    disabled_client: TestClient, method: str, path: str, payload: dict[str, Any] | None
) -> None:
    _assert_disabled(disabled_client.request(method, path, json=payload))


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("POST", "/api/bridge/clients", {}),
        ("POST", "/api/bridge/clients", {"name": 5}),
        ("POST", "/api/bridge/clients", {"name": "cc", "extra": True}),
        ("PATCH", "/api/bridge/clients/cc", {}),
        ("PATCH", "/api/bridge/clients/cc", {"scopes": "memory"}),
    ],
)
def test_disabled_beats_body_validation(
    disabled_client: TestClient, method: str, path: str, payload: dict[str, Any]
) -> None:
    """C1: the router-level check runs before pydantic, so a bad body is 404, not 422."""
    _assert_disabled(disabled_client.request(method, path, json=payload))


def test_disabled_without_a_store_lists_nothing_and_revokes_nothing(
    disabled_client: TestClient,
) -> None:
    assert disabled_client.get("/api/bridge/clients").json() == {"items": []}
    r = disabled_client.delete("/api/bridge/clients/cc")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "bridge.client_not_found"
    assert not paths.bridge_db().exists()


def test_disabled_bridge_still_lists_and_revokes(revalidations: list[int]) -> None:
    """C1: the operator can clean up while the bridge is off; the key opens nothing meanwhile."""
    _write_hal0_toml("")
    with TestClient(create_app(), headers=bearer(ADMIN_KEY)) as client:
        key = client.post(
            "/api/bridge/clients", json={"name": "cc", "scopes": ["memory", "admin"]}
        ).json()["key"]
        assert client.post("/mcp/memory/mcp", headers=bearer(key), json={}).status_code not in (
            401,
            403,
        )
    _write_hal0_toml("[bridge]\nenabled = false\n")
    with TestClient(create_app(), headers=bearer(ADMIN_KEY)) as client:
        assert client.app.state.bridge_store is not None  # type: ignore[attr-defined]
        # The key no longer authenticates anywhere, the mounts included.
        for method, path in [
            ("POST", "/mcp/admin/mcp"),
            ("POST", "/mcp/memory/mcp"),
            ("GET", "/api/bridge/me"),
        ]:
            assert client.request(method, path, headers=bearer(key), json={}).status_code == 401
        listed = client.get("/api/bridge/clients")
        assert listed.status_code == 200
        assert [c["name"] for c in listed.json()["items"]] == ["cc"]
        revoked = client.delete("/api/bridge/clients/cc")
        assert revoked.status_code == 200
        assert revoked.json()["client"]["revoked_at"] is not None
        assert revalidations
    _write_hal0_toml("")
    with TestClient(create_app(), headers=bearer(ADMIN_KEY)) as client:
        assert client.get("/api/bridge/me", headers=bearer(key)).status_code == 401


def test_disabled_bridge_never_creates_bridge_db() -> None:
    """C1: a ``hal0bk_`` bearer on a disabled box does not create the store on its behalf."""
    _write_hal0_toml("[bridge]\nenabled = false\n")
    with TestClient(create_app()) as client:
        for method, path in [
            ("POST", "/mcp/memory/mcp"),
            ("POST", "/mcp/admin/mcp"),
            ("GET", "/api/bridge/me"),
            ("GET", "/api/slots"),
        ]:
            r = client.request(method, path, headers=bearer("hal0bk_cc.forged"), json={})
            assert r.status_code == 401
    assert not paths.bridge_db().exists()


def test_disabled_does_not_open_the_store(disabled_client: TestClient) -> None:
    assert disabled_client.app.state.bridge_store is None  # type: ignore[attr-defined]
    assert not paths.bridge_db().exists()


@pytest.mark.parametrize(
    ("method", "path", "payload"), [r for r in _ROUTES if r[1] != "/api/bridge/me"]
)
def test_store_unavailable_is_503(
    monkeypatch: pytest.MonkeyPatch, method: str, path: str, payload: dict[str, Any] | None
) -> None:
    """An unwritable ``bridge.db`` costs the bridge, not the boot."""

    def _boom(self: BridgeStore) -> None:
        raise PermissionError("read-only var_lib")

    monkeypatch.setattr(auth_mod, "_bridge_store_instance", None)
    monkeypatch.setattr(BridgeStore, "open", _boom)
    _write_hal0_toml("")
    with TestClient(create_app(), headers=bearer(ADMIN_KEY)) as client:
        assert client.app.state.bridge_store is None  # type: ignore[attr-defined]
        r = client.request(method, path, json=payload)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "bridge.store_unavailable"


# ---------------------------------------------------------------------------
# One store per process


def test_routes_and_middleware_share_one_store(admin_client: TestClient) -> None:
    store = admin_client.app.state.bridge_store  # type: ignore[attr-defined]
    assert isinstance(store, BridgeStore)
    assert store is auth_mod.bridge_store()
    key = admin_client.post("/api/bridge/clients", json={"name": "cc"}).json()["key"]
    assert store.get_client("cc") is not None
    # The row the route wrote is the row the middleware verifies against.
    assert admin_client.get("/api/bridge/me", headers=bearer(key)).status_code == 200
