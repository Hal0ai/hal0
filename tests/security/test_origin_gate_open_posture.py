"""The Origin check applies whether or not key enforcement is on.

With enforcement off there is no credential to check, so the ``Origin`` header
is the only thing that tells the box's own dashboard from another site open in
the same browser. These tests pin the gate on the open posture: REST and
WebSocket, the allowlist, the IP-literal same-origin fallback, the DNS-name
rule (#2327), and that callers with no ``Origin`` are untouched.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

FOREIGN = "https://other-site.example.com"
LISTED = "http://hal0box:8080"


@pytest.fixture
def open_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A fresh app with enforcement OFF and no keys: the default posture."""
    from hal0.api import create_app

    monkeypatch.setenv("HAL0_AGENT_SECRET_PATH", str(tmp_path / "secret.bin"))
    monkeypatch.setenv("HAL0_HOME", str(tmp_path / "home"))
    os.makedirs(tmp_path / "home" / "etc" / "hal0", exist_ok=True)
    monkeypatch.delenv("HAL0_REQUIRE_AUTH", raising=False)
    monkeypatch.delenv("HAL0_ADMIN_KEY", raising=False)
    monkeypatch.setenv("HAL0_ALLOWED_ORIGINS", f"http://hal0.local,{LISTED}")
    with TestClient(create_app()) as c:
        yield c


def _forbidden(resp) -> bool:  # type: ignore[no-untyped-def]
    return resp.status_code == 403 and resp.json()["error"]["code"] == "auth.origin_forbidden"


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_foreign_origin_state_change_is_refused(open_app: TestClient, method: str) -> None:
    resp = getattr(open_app, method)("/api/settings", headers={"Origin": FOREIGN})
    assert _forbidden(resp), resp.text


def test_foreign_origin_is_refused_on_an_open_class_route(open_app: TestClient) -> None:
    resp = open_app.post("/v1/chat/completions", headers={"Origin": FOREIGN}, json={})
    assert _forbidden(resp), resp.text


def test_refusal_names_the_remedy(open_app: TestClient) -> None:
    resp = open_app.post("/api/settings", headers={"Origin": FOREIGN}, json={})
    message = resp.json()["error"]["message"]
    assert "hal0 network public-url set" in message
    assert "hal0 network origins add" in message


def test_foreign_origin_websocket_is_refused(open_app: TestClient) -> None:
    with (
        pytest.raises(WebSocketDisconnect) as excinfo,
        open_app.websocket_connect("/api/board/events", headers={"origin": FOREIGN}),
    ):
        pass
    assert excinfo.value.code == 4403


def test_no_origin_state_change_passes(open_app: TestClient) -> None:
    """curl, SDKs and server-side callers send no Origin and are not browsers."""
    resp = open_app.post("/api/settings", json={})
    assert not _forbidden(resp), resp.text
    assert resp.status_code != 401


def test_listed_origin_passes(open_app: TestClient) -> None:
    resp = open_app.post(
        "/api/settings", headers={"Origin": LISTED, "Host": "hal0box:8080"}, json={}
    )
    assert not _forbidden(resp), resp.text


def test_own_origin_on_ip_literal_host_passes(open_app: TestClient) -> None:
    """The dashboard reached by a LAN IP the allowlist does not know."""
    resp = open_app.post(
        "/api/settings",
        headers={"Origin": "http://192.0.2.50:8080", "Host": "192.0.2.50:8080"},
        json={},
    )
    assert not _forbidden(resp), resp.text


def test_own_origin_on_unlisted_dns_name_is_refused(open_app: TestClient) -> None:
    """#2327: a matching Origin/Host pair on a DNS name proves nothing."""
    resp = open_app.post(
        "/api/settings",
        headers={"Origin": "http://unlisted.example:8080", "Host": "unlisted.example:8080"},
        json={},
    )
    assert _forbidden(resp), resp.text


def test_foreign_origin_safe_method_passes(open_app: TestClient) -> None:
    """GET carries no side effect; the gate covers state changes and upgrades only."""
    resp = open_app.get("/api/health", headers={"Origin": FOREIGN})
    assert resp.status_code == 200


# Clients that are not web pages but send an Origin: browser extensions and
# desktop apps present their own scheme, which no web page can.
APP_ORIGINS = ["chrome-extension://abcdefghijklmnop", "moz-extension://1b2c", "app://obsidian.md"]
NOT_APP_ORIGINS = [
    "null",
    "file://",
    "file:///home/u/page.html",
    "data:text/html,x",
    "garbage",
    "://x",
]


@pytest.fixture
def keyed_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A fresh app with enforcement ON and an admin key."""
    from hal0.api import create_app

    monkeypatch.setenv("HAL0_AGENT_SECRET_PATH", str(tmp_path / "secret.bin"))
    monkeypatch.setenv("HAL0_HOME", str(tmp_path / "home"))
    os.makedirs(tmp_path / "home" / "etc" / "hal0", exist_ok=True)
    monkeypatch.setenv("HAL0_REQUIRE_AUTH", "1")
    monkeypatch.setenv("HAL0_ADMIN_KEY", "admin-xyz")
    monkeypatch.setenv("HAL0_ALLOWED_ORIGINS", "http://hal0.local")
    with TestClient(create_app()) as c:
        yield c


@pytest.mark.parametrize("origin", APP_ORIGINS)
def test_app_scheme_origin_passes_when_open(open_app: TestClient, origin: str) -> None:
    resp = open_app.post("/v1/chat/completions", headers={"Origin": origin}, json={})
    assert not _forbidden(resp), resp.text


@pytest.mark.parametrize("origin", APP_ORIGINS)
def test_app_scheme_origin_passes_with_a_key_when_enforced(
    keyed_app: TestClient, origin: str
) -> None:
    resp = keyed_app.post(
        "/api/settings", headers={"Origin": origin, "Authorization": "Bearer admin-xyz"}, json={}
    )
    assert resp.status_code not in (401, 403), resp.text


def test_app_scheme_origin_still_needs_a_key_when_enforced(keyed_app: TestClient) -> None:
    resp = keyed_app.post("/api/settings", headers={"Origin": APP_ORIGINS[0]}, json={})
    assert resp.status_code == 401, resp.text


@pytest.mark.parametrize("origin", NOT_APP_ORIGINS)
def test_null_and_malformed_origins_are_refused_when_open(
    open_app: TestClient, origin: str
) -> None:
    resp = open_app.post("/api/settings", headers={"Origin": origin}, json={})
    assert _forbidden(resp), resp.text


@pytest.mark.parametrize("origin", NOT_APP_ORIGINS)
def test_null_and_malformed_origins_are_refused_when_enforced(
    keyed_app: TestClient, origin: str
) -> None:
    resp = keyed_app.post(
        "/api/settings", headers={"Origin": origin, "Authorization": "Bearer admin-xyz"}, json={}
    )
    assert _forbidden(resp), resp.text


def test_app_scheme_websocket_passes_when_open(open_app: TestClient) -> None:
    with open_app.websocket_connect("/api/board/events", headers={"origin": APP_ORIGINS[0]}):
        pass
