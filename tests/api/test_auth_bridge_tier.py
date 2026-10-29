"""The bridge credential tier at the auth layer (ADR-0043).

A bridge key (``hal0bk_<name>.<token>``) resolves to the ``bridge`` tier
through ``Authorization: Bearer`` only. It opens the BRIDGE-class surfaces
(``/mcp/agents``, ``GET /api/bridge/me``) and the MCP mounts its scopes name
(``memory`` -> ``/mcp/memory``, ``admin`` -> ``/mcp/admin``), and nothing
else: every other ``/api/*`` and ``/v1/*`` route answers 403. Scope is held
before the dev-open bypass, so it also applies on a trusted-LAN box with auth
off, and a BRIDGE-class request never rides that bypass at all.

The middleware tests wrap :class:`AuthEnforcementMiddleware` around a stub
ASGI app that answers 200 to every path, the way
``tests/api/test_auth_gate_credential.py`` isolates the gate from the routes:
``/api/bridge/me`` and ``/mcp/agents`` do not exist yet, and the question
here is only what the gate lets through.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.websockets import WebSocketDisconnect

from hal0.api import auth as auth_mod
from hal0.api import mcp_mount
from hal0.api.auth import (
    AuthEnforcementMiddleware,
    AuthPrincipal,
    _decide,
    bridge_scope_admits,
    resolve_principal,
)
from hal0.bridge.store import BridgeStore
from hal0.security.exposure import AuthClass

ADMIN_KEY = "admin-key-for-bridge-tests"
CLIENT_KEY = "client-key-for-bridge-tests"


# ---------------------------------------------------------------------------
# Fixtures and helpers


@pytest.fixture(autouse=True)
def _fresh_posture_cache() -> Iterator[None]:
    auth_mod._require_auth_cache = None
    yield
    auth_mod._require_auth_cache = None


@pytest.fixture
def bridge_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """``[bridge].enabled = false`` as the boot phase records it."""
    monkeypatch.setattr(auth_mod, "_bridge_enabled", False)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> BridgeStore:
    """A tmp bridge store, installed as the one the auth layer reads."""
    s = BridgeStore(tmp_path / "bridge.db")
    s.open()
    monkeypatch.setattr(auth_mod, "bridge_store", lambda: s)
    return s


@pytest.fixture
def key(store: BridgeStore) -> str:
    """Key for client ``cc`` with the default scopes (agents, memory)."""
    _row, plain = store.add_client("cc", created_by="t")
    return plain


@pytest.fixture
def key_with_memory(store: BridgeStore) -> str:
    _row, plain = store.add_client("cc", created_by="t", scopes=("agents", "memory"))
    return plain


@pytest.fixture
def key_with_admin(store: BridgeStore) -> str:
    _row, plain = store.add_client("cc", created_by="t", scopes=("agents", "admin"))
    return plain


@pytest.fixture
def auth_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_REQUIRE_AUTH", "1")
    monkeypatch.setenv("HAL0_ADMIN_KEY", ADMIN_KEY)
    monkeypatch.setenv("HAL0_CLIENT_KEY", CLIENT_KEY)


@pytest.fixture
def auth_off(monkeypatch: pytest.MonkeyPatch) -> None:
    # The suite conftest already pins this; restated so the test reads alone.
    monkeypatch.setenv("HAL0_REQUIRE_AUTH", "0")
    monkeypatch.setenv("HAL0_BIND_HOST", "127.0.0.1")


async def _ok_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    """Answers 200 on every HTTP path and accepts every WebSocket."""
    if scope["type"] == "websocket":
        await receive()
        await send({"type": "websocket.accept"})
        await send({"type": "websocket.close", "code": 1000})
        return
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"{}"})


def _client(peer: str = "127.0.0.1") -> TestClient:
    return TestClient(AuthEnforcementMiddleware(_ok_app), client=(peer, 50000))


@pytest.fixture
def client() -> TestClient:
    return _client()


@pytest.fixture
def app_auth_off(auth_off: None) -> TestClient:
    return _client()


@pytest.fixture
def app_auth_on(auth_on: None) -> TestClient:
    return _client()


@pytest.fixture
def admin_key(auth_on: None) -> str:
    return ADMIN_KEY


def bearer(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}


def _scope(
    *,
    headers: dict[str, str] | None = None,
    query: str = "",
    path: str = "/api/slots",
    method: str = "GET",
    peer: str = "127.0.0.1",
) -> dict[str, Any]:
    raw = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in (headers or {}).items()]
    return {
        "type": "http",
        "method": method,
        "path": path,
        "headers": raw,
        "query_string": query.encode("latin-1"),
        "client": (peer, 50000),
    }


def request_with(
    *,
    bearer: str | None = None,
    headers: dict[str, str] | None = None,
    path: str = "/mcp/memory/mcp",
) -> Request:
    """A Starlette ``Request`` the auth middleware never saw (no judged principal on it)."""
    merged = dict(headers or {})
    if bearer is not None:
        merged["Authorization"] = f"Bearer {bearer}"
    return Request(_scope(headers=merged, path=path, method="POST"))


def judged_request(
    *,
    bearer: str | None = None,
    headers: dict[str, str] | None = None,
    path: str = "/mcp/memory/mcp",
) -> Request:
    """The ``Request`` an MCP tool handler sees after the real middleware judged it.

    Runs the request through :class:`AuthEnforcementMiddleware` around a stub
    app and returns a ``Request`` on the scope the middleware passed on, so a
    bridge principal it admitted is stored there exactly as in production. A
    refused request never reaches the stub; its scope carries no principal.
    """
    request = request_with(bearer=bearer, headers=headers, path=path)
    scope = request.scope

    async def _receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def _send(_message: dict[str, Any]) -> None:
        return None

    asyncio.run(AuthEnforcementMiddleware(_ok_app)(scope, _receive, _send))
    return Request(scope)


@contextlib.contextmanager
def _in_mcp_request(request: Request) -> Iterator[None]:
    """Set the MCP SDK's ``request_ctx`` the way the streamable-HTTP transport does.

    Mirrors ``tests/mcp/test_mcp_mount_private_header.py``.
    """
    lowlevel = pytest.importorskip("mcp.server.lowlevel.server")
    context = pytest.importorskip("mcp.shared.context")
    ctx = context.RequestContext(
        request_id=1, meta=None, session=None, lifespan_context=None, request=request
    )
    token = lowlevel.request_ctx.set(ctx)
    try:
        yield
    finally:
        lowlevel.request_ctx.reset(token)


def client_id_resolver_under(request: Request) -> str:
    with _in_mcp_request(request):
        return mcp_mount.client_id_resolver()


def bearer_resolver_under(request: Request) -> tuple[str | None, str]:
    with _in_mcp_request(request):
        return mcp_mount.bearer_resolver()


def ok(response: Any) -> bool:
    return response.status_code not in (401, 403)


# ---------------------------------------------------------------------------
# Principal resolution


def test_resolve_principal_bridge_key(key: str) -> None:
    p = resolve_principal(_scope(headers={"authorization": f"Bearer {key}"}))
    assert (p.tier, p.name, p.source) == ("bridge", "cc", "bearer")
    assert p.scopes == frozenset({"agents", "memory"})


def test_bridge_key_in_query_string_is_anon(key: str) -> None:
    """Review Focus 1: a bridge key is accepted from the Authorization header only."""
    assert resolve_principal(_scope(query=f"api_key={key}")).tier == "anon"


def test_revoked_key_is_anon(store: BridgeStore, key: str) -> None:
    store.revoke_client("cc")
    assert resolve_principal(_scope(headers={"authorization": f"Bearer {key}"})).tier == "anon"


def test_rotated_out_key_is_anon(store: BridgeStore, key: str) -> None:
    _row, new_key = store.rotate_client("cc")
    assert resolve_principal(_scope(headers=bearer(key))).tier == "anon"
    assert resolve_principal(_scope(headers=bearer(new_key))).tier == "bridge"


@pytest.mark.parametrize(
    "candidate",
    ["hal0bk_cc.not-the-token", "hal0bk_nobody.xyz", "hal0bk_", "hal0bk_CC.x", "hal0bk_cc"],
)
def test_unknown_bridge_shaped_key_is_anon(key: str, candidate: str) -> None:
    assert resolve_principal(_scope(headers=bearer(candidate))).tier == "anon"


def test_box_keys_still_resolve_with_a_store_present(store: BridgeStore, auth_on: None) -> None:
    assert resolve_principal(_scope(headers=bearer(ADMIN_KEY))).tier == "admin"
    assert resolve_principal(_scope(headers=bearer(CLIENT_KEY))).tier == "client"


def test_store_unavailable_treats_bridge_keys_as_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unwritable var_lib must not 500 a request: the key simply does not verify."""
    from hal0.bridge import store as store_mod

    def _refuse(self: BridgeStore) -> None:
        raise PermissionError(13, "Permission denied", str(self.path))

    monkeypatch.setattr(auth_mod, "_bridge_store_instance", None)
    monkeypatch.setattr(store_mod.BridgeStore, "open", _refuse)
    p = resolve_principal(_scope(headers=bearer("hal0bk_cc.whatever")))
    assert p.tier == "anon"
    assert _client().get("/api/bridge/me", headers=bearer("hal0bk_cc.whatever")).status_code == 401


def test_bridge_store_is_built_from_bridge_db(monkeypatch: pytest.MonkeyPatch) -> None:
    from hal0.config import paths

    monkeypatch.setattr(auth_mod, "_bridge_store_instance", None)
    first = auth_mod.bridge_store()
    assert first.path == paths.bridge_db()
    assert auth_mod.bridge_store() is first
    assert stat.S_IMODE(os.stat(first.path).st_mode) == 0o600


# ---------------------------------------------------------------------------
# The decision table


@pytest.mark.parametrize(
    ("tier", "allowed", "status"),
    [("bridge", True, 200), ("admin", True, 200), ("anon", False, 401), ("client", False, 403)],
)
def test_decide_bridge_class(tier: Any, allowed: bool, status: int) -> None:
    ok_, st, _code = _decide(AuthClass.BRIDGE, AuthPrincipal(tier=tier))
    assert (ok_, st) == (allowed, status)


def test_bridge_principal_forbidden_elsewhere() -> None:
    p = AuthPrincipal(tier="bridge", name="cc")
    assert _decide(AuthClass.ADMIN, p)[1:] == (403, "auth.forbidden")
    assert _decide(AuthClass.CLIENT, p)[1:] == (403, "auth.forbidden")


_BOTH = frozenset({"agents", "memory", "admin"})


@pytest.mark.parametrize(
    ("path", "scopes", "admitted"),
    [
        ("/mcp/memory", frozenset({"agents", "memory"}), True),
        ("/mcp/memory/mcp", frozenset({"agents", "memory"}), True),
        ("/mcp/admin/mcp", frozenset({"agents", "memory"}), False),
        ("/mcp/admin/mcp", frozenset({"agents", "admin"}), True),
        ("/mcp/memory/mcp", frozenset({"agents", "admin"}), False),
        ("/mcp/memory2/mcp", _BOTH, False),
        ("/mcp/administrator", _BOTH, False),
        ("/mcp/agents/mcp", _BOTH, False),
        ("/api/memory/recall", _BOTH, False),
        ("/mcp/memory/../admin/mcp", frozenset({"agents", "memory"}), False),
        ("/mcp/memory/./mcp", frozenset({"agents", "memory"}), False),
        ("/mcp/memory/mcp", frozenset({"agents"}), False),
    ],
)
def test_bridge_scope_admits(path: str, scopes: frozenset[str], admitted: bool) -> None:
    assert bridge_scope_admits(path, AuthPrincipal(tier="bridge", scopes=scopes)) is admitted


@pytest.mark.parametrize("tier", ["anon", "client", "admin"])
def test_bridge_scope_admits_only_bridge_principals(tier: Any) -> None:
    assert bridge_scope_admits("/mcp/memory/mcp", AuthPrincipal(tier=tier, scopes=_BOTH)) is False


# ---------------------------------------------------------------------------
# Middleware, auth off


def test_middleware_enforces_bridge_even_when_auth_off(app_auth_off: TestClient, key: str) -> None:
    # No credential: 401 although require_auth is off and the peer is loopback.
    assert app_auth_off.get("/api/bridge/me").status_code == 401
    assert app_auth_off.post("/mcp/agents/mcp").status_code == 401
    assert app_auth_off.get("/api/bridge/me", headers=bearer(key)).status_code == 200
    assert app_auth_off.post("/mcp/agents/mcp", headers=bearer(key)).status_code == 200


def test_bridge_class_refuses_the_client_key_when_auth_off(
    app_auth_off: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAL0_CLIENT_KEY", CLIENT_KEY)
    assert app_auth_off.get("/api/bridge/me", headers=bearer(CLIENT_KEY)).status_code == 403


#: Every scope set a client can hold, and which of the two scoped mounts it opens.
_SCOPE_MATRIX = [
    (("agents",), False, False),
    (("agents", "memory"), True, False),
    (("agents", "admin"), False, True),
    (("agents", "memory", "admin"), True, True),
]


@pytest.mark.parametrize(("scopes", "memory_ok", "admin_ok"), _SCOPE_MATRIX)
def test_scope_is_enforced_when_auth_off(
    app_auth_off: TestClient,
    store: BridgeStore,
    scopes: tuple[str, ...],
    memory_ok: bool,
    admin_ok: bool,
) -> None:
    """Every scope set on a dev-open box, from loopback: the key opens only its mounts."""
    client = app_auth_off
    _, key = store.add_client("cc", created_by="t", scopes=scopes)
    assert ok(client.get("/api/bridge/me", headers=bearer(key)))
    assert ok(client.post("/mcp/agents/mcp", headers=bearer(key)))
    assert ok(client.post("/mcp/memory/mcp", headers=bearer(key))) is memory_ok
    assert ok(client.post("/mcp/admin/mcp", headers=bearer(key))) is admin_ok
    assert client.get("/api/slots", headers=bearer(key)).status_code == 403
    assert client.post("/v1/chat/completions", headers=bearer(key)).status_code == 403
    assert client.get("/api/bridge/clients", headers=bearer(key)).status_code == 403
    # OPEN stays open with or without the key; dev-open is unchanged for anon.
    assert client.get("/api/health", headers=bearer(key)).status_code == 200
    assert client.get("/v1/models", headers=bearer(key)).status_code == 200
    assert client.get("/api/slots").status_code == 200
    assert client.get("/api/bridge/clients").status_code == 200


def test_unknown_bridge_shaped_key_rides_dev_open_as_anon(app_auth_off: TestClient) -> None:
    assert app_auth_off.get("/api/slots", headers=bearer("hal0bk_cc.forged")).status_code == 200
    assert app_auth_off.get("/api/bridge/me", headers=bearer("hal0bk_cc.forged")).status_code == 401


def test_revoked_key_is_refused_on_bridge_surfaces_when_auth_off(
    app_auth_off: TestClient, store: BridgeStore, key: str
) -> None:
    store.revoke_client("cc")
    assert app_auth_off.get("/api/bridge/me", headers=bearer(key)).status_code == 401
    assert app_auth_off.post("/mcp/agents/mcp", headers=bearer(key)).status_code == 401


@pytest.mark.parametrize(("scopes", "memory_ok", "admin_ok"), _SCOPE_MATRIX)
def test_lan_bound_box_with_auth_off(
    monkeypatch: pytest.MonkeyPatch,
    store: BridgeStore,
    scopes: tuple[str, ...],
    memory_ok: bool,
    admin_ok: bool,
) -> None:
    """The trusted-LAN posture: auth off, wide bind, admin key set, LAN peer; every scope set."""
    monkeypatch.setenv("HAL0_REQUIRE_AUTH", "0")
    monkeypatch.setenv("HAL0_BIND_HOST", "0.0.0.0")
    monkeypatch.setenv("HAL0_ADMIN_KEY", ADMIN_KEY)
    _, key = store.add_client("cc", created_by="t", scopes=scopes)
    lan = _client(peer="203.0.113.5")
    assert lan.get("/api/bridge/me", headers=bearer(key)).status_code == 200
    assert ok(lan.post("/mcp/agents/mcp", headers=bearer(key)))
    assert ok(lan.post("/mcp/memory/mcp", headers=bearer(key))) is memory_ok
    assert ok(lan.post("/mcp/admin/mcp", headers=bearer(key))) is admin_ok
    assert lan.get("/api/slots", headers=bearer(key)).status_code == 403
    assert lan.get("/api/settings", headers=bearer(key)).status_code == 403
    assert lan.post("/v1/chat/completions", headers=bearer(key)).status_code == 403
    assert lan.get("/api/health", headers=bearer(key)).status_code == 200
    assert lan.get("/api/bridge/me").status_code == 401


@pytest.mark.parametrize(("scopes", "memory_ok", "admin_ok"), _SCOPE_MATRIX)
def test_lan_peer_with_auth_on(
    monkeypatch: pytest.MonkeyPatch,
    store: BridgeStore,
    auth_on: None,
    scopes: tuple[str, ...],
    memory_ok: bool,
    admin_ok: bool,
) -> None:
    """Auth on, from a LAN peer: the same answers as from loopback, for every scope set."""
    monkeypatch.setenv("HAL0_BIND_HOST", "0.0.0.0")
    _, key = store.add_client("cc", created_by="t", scopes=scopes)
    lan = _client(peer="203.0.113.5")
    assert ok(lan.get("/api/bridge/me", headers=bearer(key)))
    assert ok(lan.post("/mcp/agents/mcp", headers=bearer(key)))
    assert ok(lan.post("/mcp/memory/mcp", headers=bearer(key))) is memory_ok
    assert ok(lan.post("/mcp/admin/mcp", headers=bearer(key))) is admin_ok
    assert lan.get("/api/slots", headers=bearer(key)).status_code == 403
    assert lan.post("/v1/chat/completions", headers=bearer(key)).status_code == 403


# ---------------------------------------------------------------------------
# Middleware, auth on


@pytest.mark.parametrize(("scopes", "memory_ok", "admin_ok"), _SCOPE_MATRIX)
def test_middleware_matrix_auth_on(
    app_auth_on: TestClient,
    store: BridgeStore,
    admin_key: str,
    scopes: tuple[str, ...],
    memory_ok: bool,
    admin_ok: bool,
) -> None:
    client = app_auth_on
    _, key = store.add_client("cc", created_by="t", scopes=scopes)
    assert ok(client.get("/api/bridge/me", headers=bearer(key)))
    assert ok(client.post("/mcp/agents/mcp", headers=bearer(key)))
    assert ok(client.post("/mcp/memory/mcp", headers=bearer(key))) is memory_ok
    assert ok(client.post("/mcp/admin/mcp", headers=bearer(key))) is admin_ok
    assert client.get("/api/slots", headers=bearer(key)).status_code == 403
    assert client.post("/v1/chat/completions", headers=bearer(key)).status_code == 403
    assert client.get("/api/bridge/clients", headers=bearer(key)).status_code == 403
    assert client.get("/api/bridge/clients", headers=bearer(admin_key)).status_code == 200


def test_bridge_class_tiers_auth_on(app_auth_on: TestClient) -> None:
    assert app_auth_on.get("/api/bridge/me").status_code == 401
    assert app_auth_on.get("/api/bridge/me", headers=bearer(CLIENT_KEY)).status_code == 403
    assert app_auth_on.get("/api/bridge/me", headers=bearer(ADMIN_KEY)).status_code == 200
    assert app_auth_on.post("/mcp/agents/mcp", headers=bearer(ADMIN_KEY)).status_code == 200


def test_revoked_key_is_refused_everywhere_auth_on(
    app_auth_on: TestClient, store: BridgeStore, key: str
) -> None:
    assert ok(app_auth_on.post("/mcp/memory/mcp", headers=bearer(key)))
    store.revoke_client("cc")
    for method, path in [
        ("GET", "/api/bridge/me"),
        ("POST", "/mcp/agents/mcp"),
        ("POST", "/mcp/memory/mcp"),
        ("GET", "/api/slots"),
    ]:
        assert app_auth_on.request(method, path, headers=bearer(key)).status_code == 401


def test_bridge_key_websocket(app_auth_on: TestClient, key: str) -> None:
    with app_auth_on.websocket_connect("/mcp/agents/ws", headers=bearer(key)):
        pass
    with (
        pytest.raises(WebSocketDisconnect) as excinfo,
        app_auth_on.websocket_connect("/api/events/ws", headers=bearer(key)),
    ):
        pass
    assert excinfo.value.code == 4403


def test_still_admitted_follows_revocation(store: BridgeStore, key: str, auth_on: None) -> None:
    """An open stream or socket on a bridge key ends once the key is revoked."""
    agents = _scope(headers=bearer(key), path="/mcp/agents/mcp", method="POST")
    memory = _scope(headers=bearer(key), path="/mcp/memory/mcp", method="POST")
    assert auth_mod._still_admitted(AuthClass.BRIDGE, agents) is True
    assert auth_mod._still_admitted(AuthClass.CLIENT, memory) is True
    store.revoke_client("cc")
    assert auth_mod._still_admitted(AuthClass.BRIDGE, agents) is False
    assert auth_mod._still_admitted(AuthClass.CLIENT, memory) is False


def test_still_admitted_holds_scope_when_auth_off(
    store: BridgeStore, key: str, auth_off: None
) -> None:
    agents = _scope(headers=bearer(key), path="/mcp/agents/mcp", method="POST")
    events = _scope(headers=bearer(key), path="/api/events")
    assert auth_mod._still_admitted(AuthClass.BRIDGE, agents) is True
    assert auth_mod._still_admitted(AuthClass.ADMIN, events) is False
    store.revoke_client("cc")
    assert auth_mod._still_admitted(AuthClass.BRIDGE, agents) is False


def test_real_app_refuses_bridge_key_outside_scope(
    store: BridgeStore, key: str, auth_on: None
) -> None:
    """End to end through ``create_app()``: the middleware is the real one."""
    from hal0.api import create_app

    os.makedirs(Path(os.environ["HAL0_HOME"]) / "etc" / "hal0", exist_ok=True)
    with TestClient(create_app()) as client:
        assert client.get("/api/slots", headers=bearer(key)).status_code == 403
        assert client.get("/api/settings", headers=bearer(key)).status_code == 403
        assert client.get("/api/bridge/me").status_code == 401
        assert client.get("/api/health", headers=bearer(key)).status_code == 200


# ---------------------------------------------------------------------------
# MCP identity and the admin mount's REST credential


def test_memory_identity_is_key_bound(key_with_memory: str) -> None:
    # X-hal0-Agent: hermes is ignored for a bridge principal.
    cid = client_id_resolver_under(
        judged_request(bearer=key_with_memory, headers={"X-hal0-Agent": "hermes"})
    )
    assert cid == "cc"


def test_memory_identity_without_a_bridge_key_reads_the_header(store: BridgeStore) -> None:
    assert client_id_resolver_under(judged_request(headers={"X-hal0-Agent": "hermes"})) == "hermes"


def test_revoked_key_loses_its_identity(store: BridgeStore, key_with_memory: str) -> None:
    """A revoked key is never the header's claim, though dev-open still lets the call through."""
    store.revoke_client("cc")
    request = judged_request(bearer=key_with_memory, headers={"X-hal0-Agent": "hermes"})
    assert client_id_resolver_under(request) == "anonymous"
    assert bearer_resolver_under(request) == (None, "anonymous")
    assert client_id_resolver_under(judged_request(bearer=key_with_memory)) == "anonymous"


def test_admin_scope_calls_rest_as_service(
    key_with_admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAL0_ADMIN_KEY", "adminsecret")
    bearer_, label = bearer_resolver_under(
        judged_request(bearer=key_with_admin, path="/mcp/admin/mcp")
    )
    assert (bearer_, label) == ("adminsecret", "bridge:cc")


def test_memory_scope_never_gets_the_service_key(
    key_with_memory: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAL0_ADMIN_KEY", "adminsecret")
    bearer_, label = bearer_resolver_under(judged_request(bearer=key_with_memory))
    assert bearer_ == key_with_memory
    assert label == mcp_mount._bearer_label(key_with_memory)


@pytest.mark.parametrize("posture", ["auth_off", "auth_on"])
def test_store_error_never_falls_back_to_the_header(
    request: pytest.FixtureRequest,
    store: BridgeStore,
    key_with_memory: str,
    monkeypatch: pytest.MonkeyPatch,
    posture: str,
) -> None:
    """I1: a key that cannot be verified (locked store) is anonymous, not ``X-hal0-Agent``.

    Before the fix a memory-scope key landed a private write in ``private:hermes``
    whenever the store errored or a revoke raced the request.
    """
    import sqlite3

    request.getfixturevalue(posture)
    monkeypatch.setenv("HAL0_ADMIN_KEY", "adminsecret")

    def _locked(_key: str) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "verify_key", _locked)
    judged = judged_request(bearer=key_with_memory, headers={"X-hal0-Agent": "hermes"})
    assert client_id_resolver_under(judged) == "anonymous"
    assert bearer_resolver_under(judged) == (None, "anonymous")
    # Nor does a request the middleware never judged (no stored principal at all).
    unjudged = request_with(bearer=key_with_memory, headers={"X-hal0-Agent": "hermes"})
    assert client_id_resolver_under(unjudged) == "anonymous"
    assert bearer_resolver_under(unjudged) == (None, "anonymous")


def test_resolvers_read_the_judged_principal_without_verifying_again(
    store: BridgeStore, key_with_admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I1: the middleware's verify is the only one; the resolvers read what it stored."""
    monkeypatch.setenv("HAL0_ADMIN_KEY", "adminsecret")
    judged = judged_request(bearer=key_with_admin, path="/mcp/admin/mcp")
    stored = judged.scope[auth_mod.BRIDGE_PRINCIPAL_SCOPE_KEY]
    assert (stored.tier, stored.name) == ("bridge", "cc")

    def _no_second_verify(_key: str) -> None:
        raise AssertionError("a resolver verified the bridge key again")

    monkeypatch.setattr(store, "verify_key", _no_second_verify)
    assert client_id_resolver_under(judged) == "cc"
    assert bearer_resolver_under(judged) == ("adminsecret", "bridge:cc")
    assert auth_mod.judged_principal(judged) is stored


def test_still_admitted_verifies_live_not_from_the_stored_principal(
    store: BridgeStore, key: str, auth_on: None
) -> None:
    """I1 (4): revocation ends an open stream even though its scope holds a judged principal."""
    judged = judged_request(bearer=key, path="/mcp/agents/mcp")
    assert auth_mod.BRIDGE_PRINCIPAL_SCOPE_KEY in judged.scope
    assert auth_mod._still_admitted(AuthClass.BRIDGE, judged.scope) is True
    store.revoke_client("cc")
    assert auth_mod._still_admitted(AuthClass.BRIDGE, judged.scope) is False


def test_middleware_stores_no_principal_for_other_tiers(store: BridgeStore, auth_on: None) -> None:
    judged = judged_request(bearer=ADMIN_KEY, path="/api/slots")
    assert auth_mod.BRIDGE_PRINCIPAL_SCOPE_KEY not in judged.scope


@pytest.mark.parametrize(
    "credential",
    [
        {"headers": {"authorization": f"Bearer {ADMIN_KEY}"}},
        {"headers": {"authorization": f"Bearer {CLIENT_KEY}"}},
        {"headers": {"authorization": "Bearer some-other-token"}},
        {"headers": {"authorization": "Basic aGFsMGJrX2NjLng="}},
        {"query": "api_key=hal0bk_cc.via-query"},
        {"query": f"api_key={ADMIN_KEY}"},
    ],
)
def test_a_non_bridge_bearer_never_touches_the_store(
    store: BridgeStore, monkeypatch: pytest.MonkeyPatch, auth_on: None, credential: dict[str, Any]
) -> None:
    """I1 (7): only a ``hal0bk_`` bearer costs a store read."""

    def _never(_key: str) -> None:
        raise AssertionError("verify_key called for a non-bridge credential")

    monkeypatch.setattr(store, "verify_key", _never)
    monkeypatch.setattr(auth_mod, "bridge_store", lambda: pytest.fail("store opened"))
    resolve_principal(_scope(**credential))
    headers = credential.get("headers", {})
    path = "/api/slots" + (f"?{credential['query']}" if "query" in credential else "")
    for method, target in [("GET", path), ("POST", "/mcp/memory/mcp"), ("GET", "/api/bridge/me")]:
        _client().request(method, target, headers=headers)


def test_bearer_parsing_is_shared() -> None:
    """I1 (6): one parser decides what bearer a request carries, for every caller."""
    assert auth_mod.bearer_token({"authorization": "Bearer  hal0bk_cc.x "}) == "hal0bk_cc.x"
    assert auth_mod.bearer_token({"authorization": "bearer hal0bk_cc.x"}) == "hal0bk_cc.x"
    assert auth_mod.bearer_token({"authorization": "Basic abc"}) is None
    assert auth_mod.bearer_token({}) is None
    assert auth_mod.bears_bridge_key(_scope(headers=bearer("hal0bk_cc.x"))) is True
    assert auth_mod.bears_bridge_key(_scope(headers=bearer(ADMIN_KEY))) is False
    request = request_with(bearer="hal0bk_cc.x")
    assert mcp_mount._resolve_bearer(request) == "hal0bk_cc.x"


def test_admin_bearer_resolver_unchanged_for_box_keys(
    store: BridgeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAL0_ADMIN_KEY", "adminsecret")
    bearer_, label = bearer_resolver_under(request_with(bearer="adminsecret"))
    assert (bearer_, label) == ("adminsecret", mcp_mount._bearer_label("adminsecret"))


def test_middleware_runs_off_the_event_loop_thread_safely(store: BridgeStore, key: str) -> None:
    """``_still_admitted`` also runs on the live-socket ticker thread."""

    async def _both() -> list[bool]:
        scope = _scope(headers=bearer(key), path="/mcp/agents/mcp", method="POST")
        return list(
            await asyncio.gather(
                *(
                    asyncio.to_thread(auth_mod._still_admitted, AuthClass.BRIDGE, scope)
                    for _ in range(8)
                )
            )
        )

    assert asyncio.run(_both()) == [True] * 8


# ---------------------------------------------------------------------------
# [bridge].enabled = false (C1)


def test_disabled_bridge_verifies_no_key_and_opens_no_store(
    store: BridgeStore,
    key_with_admin: str,
    monkeypatch: pytest.MonkeyPatch,
    auth_on: None,
    bridge_disabled: None,
) -> None:
    """C1: a key minted while enabled opens nothing once the bridge is off."""
    monkeypatch.setattr(auth_mod, "bridge_store", lambda: pytest.fail("store opened"))
    _, both = store.add_client("both", created_by="t", scopes=("memory", "admin"))
    for key_ in (key_with_admin, both):
        assert resolve_principal(_scope(headers=bearer(key_))).tier == "anon"
        for method, path in [
            ("POST", "/mcp/admin/mcp"),
            ("POST", "/mcp/memory/mcp"),
            ("POST", "/mcp/agents/mcp"),
            ("GET", "/api/bridge/me"),
        ]:
            assert _client().request(method, path, headers=bearer(key_)).status_code == 401


def test_disabled_bridge_key_is_anonymous_to_the_mcp_resolvers(
    store: BridgeStore,
    key_with_memory: str,
    monkeypatch: pytest.MonkeyPatch,
    bridge_disabled: None,
) -> None:
    """Dev-open still waves the request through as anon, but never as the header's claim."""
    monkeypatch.setenv("HAL0_ADMIN_KEY", "adminsecret")
    judged = judged_request(bearer=key_with_memory, headers={"X-hal0-Agent": "hermes"})
    assert client_id_resolver_under(judged) == "anonymous"
    assert bearer_resolver_under(judged) == (None, "anonymous")


def test_set_bridge_enabled_records_the_boot_value(monkeypatch: pytest.MonkeyPatch) -> None:
    auth_mod.set_bridge_enabled(False)
    assert auth_mod.bridge_enabled() is False
    auth_mod.set_bridge_enabled(True)
    assert auth_mod.bridge_enabled() is True
