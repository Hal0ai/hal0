"""The ComfyUI GPU gate's credential at the auth layer (#3058).

The gate's token admits its three requests (the arbiter read, the prompt
hand-off, the switch request) on a box that enforces auth, and nothing else:
no other route, no other method, no WebSocket. The switch handler holds a
request it admitted to a plain switch into image mode.
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
]


@pytest.fixture(autouse=True)
def _enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_REQUIRE_AUTH", "1")
    monkeypatch.setenv("HAL0_ADMIN_KEY", "admin-key-for-tests")
    auth_mod._require_auth_cache = None


def _run(
    method: str,
    path: str,
    *,
    bearer: str | None = None,
    scope_type: str = "http",
    peer: str = "127.0.0.1",
) -> tuple[int | None, dict[str, Any] | None]:
    """Drive the middleware with one request: (status sent, scope the app saw)."""
    seen: dict[str, Any] = {}

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        seen.update(scope)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    headers = [(b"authorization", f"Bearer {bearer}".encode())] if bearer is not None else []
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
    monkeypatch.setattr(auth_mod, "is_loopback_bind", lambda: False)
    token = gate_credential.ensure_token()

    assert _run("GET", "/api/comfyui/arbiter", peer="192.0.2.10")[0] == 401
    status, seen = _run("GET", "/api/comfyui/arbiter", bearer=token, peer="192.0.2.10")
    assert status == 200
    assert seen is not None and seen[auth_mod.GATE_SCOPE_KEY] is True


def test_an_admin_request_is_not_marked_as_the_gates() -> None:
    gate_credential.ensure_token()

    status, seen = _run("POST", "/api/comfyui/switchover", bearer="admin-key-for-tests")

    assert status == 200
    assert seen is not None and auth_mod.GATE_SCOPE_KEY not in seen


@pytest.fixture
def client(app: Any) -> Any:
    with TestClient(app) as c:
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
