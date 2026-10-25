"""The ComfyUI GPU gate's credential at the auth layer (#3058).

The gate's token admits its four requests (the arbiter read, the prompt
hand-off, the switch request, the refusal report) on a box that enforces auth,
and nothing else: no other route, no other method, no WebSocket. The switch
handler holds a request it admitted to a plain switch into image mode.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hal0.api import auth as auth_mod
from hal0.comfyui import gate_credential

_GATE_REQUESTS = [
    ("GET", "/api/comfyui/arbiter"),
    ("POST", "/api/comfyui/prompt-handoff"),
    ("POST", "/api/comfyui/switchover"),
    ("POST", "/api/comfyui/gate-refusal"),
]


@pytest.fixture(autouse=True)
def _enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_REQUIRE_AUTH", "1")
    monkeypatch.setenv("HAL0_ADMIN_KEY", "admin-key-for-tests")
    auth_mod._require_auth_cache = None


@pytest.fixture(autouse=True)
def _no_refusal_marker() -> Any:
    auth_mod._gate_path_refused_at = None
    yield
    auth_mod._gate_path_refused_at = None


def _run(
    method: str,
    path: str,
    *,
    bearer: str | None = None,
    scope_type: str = "http",
    peer: str = "127.0.0.1",
    version: str | None = None,
    user_agent: str | None = None,
) -> tuple[int | None, dict[str, Any] | None]:
    """Drive the middleware with one request: (status sent, scope the app saw)."""
    seen: dict[str, Any] = {}

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        seen.update(scope)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    headers = [(b"authorization", f"Bearer {bearer}".encode())] if bearer is not None else []
    if version is not None:
        headers.append((b"x-hal0-gate-version", version.encode()))
    if user_agent is not None:
        headers.append((b"user-agent", user_agent.encode()))
    scope: dict[str, Any] = {
        "type": scope_type,
        "method": method,
        "path": path,
        "headers": headers,
        "query_string": b"",
        "client": (peer, 50000),
    }
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    asyncio.run(auth_mod.AuthEnforcementMiddleware(app)(scope, receive, send))
    status = next((m.get("status") for m in sent if m["type"] == "http.response.start"), None)
    if scope_type == "websocket":
        status = next((m.get("code") for m in sent if m["type"] == "websocket.close"), status)
    return status, (seen or None)


@pytest.mark.parametrize(("method", "path"), _GATE_REQUESTS)
def test_the_gate_token_admits_the_gates_requests(method: str, path: str) -> None:
    token = gate_credential.ensure_token()

    status, seen = _run(method, path, bearer=token)

    assert status == 200
    assert seen is not None and seen[auth_mod.GATE_SCOPE_KEY] is True


@pytest.mark.parametrize(("method", "path"), _GATE_REQUESTS)
def test_without_the_token_the_gates_requests_stay_refused(method: str, path: str) -> None:
    gate_credential.ensure_token()

    assert _run(method, path)[0] == 401
    assert _run(method, path, bearer="not-the-token")[0] == 401


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/comfyui/status"),
        ("POST", "/api/comfyui/pin"),
        ("POST", "/api/comfyui/restart"),
        ("DELETE", "/api/comfyui/models/sdxl-turbo"),
        ("POST", "/api/comfyui/arbiter"),
        ("GET", "/api/comfyui/switchover"),
        ("GET", "/api/comfyui/arbiter/"),
        ("GET", "//api/comfyui/arbiter"),
        ("POST", "/api/comfyui/switchover/../pin"),
        ("GET", "/api/comfyui/gate-refusal"),
        ("PUT", "/api/comfyui/gate-refusal"),
        ("POST", "/api/comfyui/gate-refusal/"),
        ("GET", "/api/settings"),
        ("POST", "/v1/images/generations"),
    ],
)
def test_the_gate_token_admits_nothing_else(method: str, path: str) -> None:
    """Refused, or (``//api/...`` classifies OPEN and routes to a 404) passed
    through untouched: never marked as the gate's."""
    token = gate_credential.ensure_token()

    status, seen = _run(method, path, bearer=token)

    if seen is None:
        assert status in (401, 403)
    else:
        assert auth_mod.GATE_SCOPE_KEY not in seen


def test_the_gate_token_never_opens_a_websocket() -> None:
    token = gate_credential.ensure_token()

    status, seen = _run("GET", "/api/comfyui/arbiter", bearer=token, scope_type="websocket")

    assert status == 4403
    assert seen is None


def test_no_token_on_disk_admits_nothing() -> None:
    """An empty bearer must not match an absent token."""
    assert _run("GET", "/api/comfyui/arbiter", bearer="")[0] == 401


def test_the_lan_admin_gate_admits_the_gate_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Auth off, hal0-api bound to one LAN address with an admin key: the gate's
    requests arrive from that address, so the LAN rule for ADMIN routes applies."""
    monkeypatch.setenv("HAL0_REQUIRE_AUTH", "0")
    monkeypatch.setenv("HAL0_BIND_HOST", "192.0.2.10")
    token = gate_credential.ensure_token()

    assert _run("GET", "/api/comfyui/arbiter", peer="192.0.2.10")[0] == 401
    status, seen = _run("GET", "/api/comfyui/arbiter", bearer=token, peer="192.0.2.10")
    assert status == 200
    assert seen is not None and seen[auth_mod.GATE_SCOPE_KEY] is True


@pytest.mark.parametrize("bind_host", ["0.0.0.0", "192.0.2.10", "127.0.0.1"])
def test_the_gate_token_is_refused_from_another_machine(
    monkeypatch: pytest.MonkeyPatch, bind_host: str
) -> None:
    """The gate dials hal0-api from the box itself (host networking): loopback,
    or the one address hal0-api binds. A token carried off the box opens nothing."""
    monkeypatch.setenv("HAL0_BIND_HOST", bind_host)
    token = gate_credential.ensure_token()

    status, seen = _run("GET", "/api/comfyui/arbiter", bearer=token, peer="192.0.2.99")

    assert status == 401
    assert seen is None


@pytest.mark.parametrize("peer", ["127.0.0.1", "::1"])
def test_the_gate_token_is_admitted_from_loopback(peer: str) -> None:
    token = gate_credential.ensure_token()

    assert _run("GET", "/api/comfyui/arbiter", bearer=token, peer=peer)[0] == 200


def test_the_allow_list_is_exactly_the_gates_requests() -> None:
    """A new gate request needs its route added with a test, in the change that adds it
    (ADR-0040): this list and the allow-list move together."""
    assert set(_GATE_REQUESTS) == set(auth_mod._GATE_REQUESTS)


def test_an_admin_request_is_not_marked_as_the_gates() -> None:
    gate_credential.ensure_token()

    status, seen = _run("POST", "/api/comfyui/switchover", bearer="admin-key-for-tests")

    assert status == 200
    assert seen is not None and auth_mod.GATE_SCOPE_KEY not in seen


@pytest.fixture
def client(app: Any) -> Any:
    """The gate's requests come from the box itself."""
    with TestClient(app, client=("127.0.0.1", 51000)) as c:
        yield c


@pytest.mark.parametrize(
    "body",
    [
        {"mode": "inference"},
        {"mode": "generation", "force": True},
        {"mode": "generation", "pin": True},
        {"mode": "nonsense"},
        {},
    ],
)
def test_the_gate_may_only_ask_for_a_plain_switch_into_image_mode(
    client: Any, body: dict[str, Any]
) -> None:
    token = gate_credential.ensure_token()

    resp = client.post(
        "/api/comfyui/switchover", json=body, headers={"Authorization": f"Bearer {token}"}
    )

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "auth.forbidden"


def test_the_gates_plain_switch_request_reaches_the_handler(client: Any) -> None:
    token = gate_credential.ensure_token()

    resp = client.post(
        "/api/comfyui/switchover",
        json={"mode": "generation"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code not in (401, 403)


def test_an_admin_may_still_switch_to_inference(client: Any) -> None:
    gate_credential.ensure_token()

    resp = client.post(
        "/api/comfyui/switchover",
        json={"mode": "inference"},
        headers={"Authorization": "Bearer admin-key-for-tests"},
    )

    assert resp.status_code not in (401, 403)


# ---------------------------------------------------------------------------
# Does hal0 require the gate to authenticate? (the enforcement middleware's own rule)


@pytest.mark.parametrize(
    ("require", "admin_key", "bind", "required"),
    [
        # Enforcement on: loopback is not exempt.
        ("1", True, "127.0.0.1", True),
        ("1", False, "127.0.0.1", True),
        ("1", True, "0.0.0.0", True),
        # Enforcement off, no admin key: nothing stands in the way.
        ("0", False, "192.0.2.10", False),
        ("0", False, "0.0.0.0", False),
        # Enforcement off, an admin key, loopback or wildcard bind: the gate dials loopback.
        ("0", True, "127.0.0.1", False),
        ("0", True, "localhost", False),
        ("0", True, "::1", False),
        ("0", True, "0.0.0.0", False),
        ("0", True, "::", False),
        # Enforcement off, an admin key, one address: the gate dials that address (the LAN rule).
        ("0", True, "192.0.2.10", True),
        ("0", True, "2001:db8::10", True),
        ("0", True, "hal0.example.lan", True),
    ],
)
def test_the_gate_must_authenticate_when_the_middleware_would_ask_it_to(
    monkeypatch: pytest.MonkeyPatch, require: str, admin_key: bool, bind: str, required: bool
) -> None:
    from hal0.api.routes import comfyui as comfyui_routes

    monkeypatch.setenv("HAL0_REQUIRE_AUTH", require)
    monkeypatch.setenv("HAL0_BIND_HOST", bind)
    if not admin_key:
        monkeypatch.delenv("HAL0_ADMIN_KEY", raising=False)

    assert comfyui_routes._gate_must_authenticate() is required


@pytest.mark.parametrize(
    ("bind", "gate_peer"),
    [
        ("127.0.0.1", "127.0.0.1"),
        ("0.0.0.0", "127.0.0.1"),
        ("192.0.2.10", "192.0.2.10"),
        # A name resolves to an address of the box, never to loopback.
        ("hal0.example.lan", "192.0.2.10"),
    ],
)
def test_what_the_status_route_says_is_what_the_middleware_does_to_the_gate(
    monkeypatch: pytest.MonkeyPatch, bind: str, gate_peer: str
) -> None:
    """The gate's own request is refused (401) without a credential exactly when
    ``/status`` says the gate must authenticate."""
    from hal0.api.routes import comfyui as comfyui_routes

    monkeypatch.setenv("HAL0_REQUIRE_AUTH", "0")
    monkeypatch.setenv("HAL0_BIND_HOST", bind)

    status, _seen = _run("GET", "/api/comfyui/arbiter", peer=gate_peer)

    assert (status == 401) is comfyui_routes._gate_must_authenticate()


# ---------------------------------------------------------------------------
# A refused request that looks like an old gate's leaves a marker (and changes nothing else)


#: The User-Agent of the gate's requests: urllib's default.
_GATE_UA = "Python-urllib/3.12"


def _noted_between(before: float, after: float) -> bool:
    noted = auth_mod.gate_path_refused_at()
    return noted is not None and before <= noted <= after


@pytest.mark.parametrize(("method", "path"), _GATE_REQUESTS)
@pytest.mark.parametrize("bearer", [None, "not-the-token"])
def test_a_refused_gate_request_without_the_version_header_is_marked(
    method: str, path: str, bearer: str | None
) -> None:
    """An engine running a gate older than the version header is turned away here, before
    any route could record its call; the marker is how /status still hears of it."""
    import time

    gate_credential.ensure_token()
    before = time.time()

    status, seen = _run(method, path, bearer=bearer, user_agent=_GATE_UA)

    assert status == 401
    assert seen is None
    assert _noted_between(before, time.time())


@pytest.mark.parametrize(("method", "path"), _GATE_REQUESTS)
def test_a_refused_gate_request_with_the_version_header_is_not_marked(
    method: str, path: str
) -> None:
    """A gate that sends the header is current; its refusal is not an old gate's."""
    gate_credential.ensure_token()

    status, _seen = _run(method, path, bearer="not-the-token", version="2", user_agent=_GATE_UA)

    assert status == 401
    assert auth_mod.gate_path_refused_at() is None


@pytest.mark.parametrize(("method", "path"), _GATE_REQUESTS)
def test_an_admitted_gate_request_is_not_marked(method: str, path: str) -> None:
    """An old gate that does hold the token is let in: nothing was refused."""
    token = gate_credential.ensure_token()

    assert _run(method, path, bearer=token, user_agent=_GATE_UA)[0] == 200
    assert auth_mod.gate_path_refused_at() is None


@pytest.mark.parametrize("bind_host", ["0.0.0.0", "192.0.2.10", "127.0.0.1"])
def test_a_refusal_for_another_machine_is_not_marked(
    monkeypatch: pytest.MonkeyPatch, bind_host: str
) -> None:
    """Only a request from where the gate dials (the box itself) can be the gate's."""
    monkeypatch.setenv("HAL0_BIND_HOST", bind_host)

    assert _run("GET", "/api/comfyui/arbiter", peer="192.0.2.99", user_agent=_GATE_UA)[0] == 401
    assert _run("GET", "/api/comfyui/arbiter", peer="testclient", user_agent=_GATE_UA)[0] == 401
    assert auth_mod.gate_path_refused_at() is None


@pytest.mark.parametrize("peer", ["127.0.0.1", "::1"])
def test_a_refusal_for_loopback_is_marked(peer: str) -> None:
    assert _run("GET", "/api/comfyui/arbiter", peer=peer, user_agent=_GATE_UA)[0] == 401
    assert auth_mod.gate_path_refused_at() is not None


def test_a_refusal_for_the_bind_address_is_marked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bound to one address with an admin key (auth off): the gate dials that address."""
    monkeypatch.setenv("HAL0_REQUIRE_AUTH", "0")
    monkeypatch.setenv("HAL0_BIND_HOST", "192.0.2.10")

    assert _run("GET", "/api/comfyui/arbiter", peer="192.0.2.10", user_agent=_GATE_UA)[0] == 401
    assert auth_mod.gate_path_refused_at() is not None


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/comfyui/status"),
        ("POST", "/api/comfyui/pin"),
        ("POST", "/api/comfyui/arbiter"),
        ("GET", "/api/comfyui/switchover"),
        ("GET", "/api/comfyui/gate-refusal"),
        ("GET", "/api/comfyui/arbiter/"),
        ("POST", "/api/comfyui/gate-refusal/"),
        ("POST", "/api/comfyui/switchover/../pin"),
        ("GET", "/api/settings"),
        ("POST", "/v1/images/generations"),
    ],
)
def test_a_refusal_of_anything_but_the_gates_requests_is_not_marked(method: str, path: str) -> None:
    status, _seen = _run(method, path, user_agent=_GATE_UA)

    assert status in (401, 403)
    assert auth_mod.gate_path_refused_at() is None


def test_a_refused_websocket_is_not_marked() -> None:
    status, _seen = _run("GET", "/api/comfyui/arbiter", scope_type="websocket", user_agent=_GATE_UA)

    assert status == 4403
    assert auth_mod.gate_path_refused_at() is None


def test_without_enforcement_nothing_is_refused_or_marked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_REQUIRE_AUTH", "0")
    monkeypatch.delenv("HAL0_ADMIN_KEY", raising=False)

    assert _run("GET", "/api/comfyui/arbiter", user_agent=_GATE_UA)[0] == 200
    assert auth_mod.gate_path_refused_at() is None


@pytest.mark.parametrize(
    "user_agent",
    [
        "curl/8.5.0",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36",
        "python-requests/2.32.3",
        "python-urllib3/2.2.3",
        "Python-urllib3/2.2.3",
        "Python-urllib",
        "",
        None,
    ],
)
def test_a_refusal_for_a_client_that_is_not_the_gates_is_not_marked(user_agent: str | None) -> None:
    """Heuristic: the gate's requests come from urllib. A browser or curl on the box
    (an operator, a dashboard session that expired) does not mark the engine as old."""
    status, _seen = _run("GET", "/api/comfyui/arbiter", user_agent=user_agent)

    assert status == 401
    assert auth_mod.gate_path_refused_at() is None


@pytest.mark.parametrize(
    "user_agent", ["Python-urllib/3.12", "Python-urllib/3.13", "Python-urllib/2.7"]
)
def test_a_refusal_for_urllibs_default_client_is_marked(user_agent: str) -> None:
    assert _run("GET", "/api/comfyui/arbiter", user_agent=user_agent)[0] == 401
    assert auth_mod.gate_path_refused_at() is not None


def test_the_gate_really_sends_the_user_agent_the_auth_layer_looks_for() -> None:
    """The heuristic rests on one fact: a request built by the gate's own opener, which
    adds no User-Agent of its own, goes out as ``Python-urllib/<major>.<minor>``."""
    import http.server
    import threading

    from tests.fixtures.comfyui_gate import load_gate_asset

    seen: list[str] = []

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen.append(self.headers.get("User-Agent", ""))
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *_args: Any) -> None:
            return None

    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        gate = load_gate_asset()
        url = f"http://127.0.0.1:{server.server_address[1]}/api/comfyui/arbiter"
        with gate._urlopen(gate._hal0_request(url), timeout=2.0):
            pass
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert len(seen) == 1
    assert seen[0].startswith(auth_mod._GATE_USER_AGENT_PREFIX)
