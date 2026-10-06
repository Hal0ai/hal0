"""Auth tests for the chat-proxy WS surface.

DA-sec-ops MUST-FIX #2: the WS routes that bridge the browser to the
hermes runtime MUST verify Origin + HMAC session cookie on every
upgrade. These tests lock that contract — both the happy path and
every rejection mode.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from hal0.api.agents import _auth


@pytest.fixture(autouse=True)
def isolate_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Force the HMAC secret onto a per-test path so secrets don't leak."""
    secret_path = tmp_path / "secret.bin"
    monkeypatch.setenv("HAL0_AGENT_SECRET_PATH", str(secret_path))
    yield secret_path


def test_mint_then_verify_roundtrip() -> None:
    """A freshly minted cookie verifies cleanly."""
    cookie = _auth.mint_session_cookie()
    assert _auth.verify_session_cookie(cookie) is True


def test_verify_rejects_garbage() -> None:
    """Random junk that vaguely looks like a cookie is rejected."""
    assert _auth.verify_session_cookie("not-a-cookie") is False
    assert _auth.verify_session_cookie("a.b") is False
    assert _auth.verify_session_cookie("") is False


def test_verify_rejects_tampered_payload() -> None:
    """Mutating the payload (even one bit) invalidates the HMAC."""
    cookie = _auth.mint_session_cookie()
    payload_b64, sig_b64 = cookie.split(".", 1)
    # Append a printable char to the payload portion → b64 still valid,
    # signature mismatches.
    tampered = f"{payload_b64}X.{sig_b64}"
    assert _auth.verify_session_cookie(tampered) is False


def test_verify_rejects_tampered_signature() -> None:
    """Mutating the signature invalidates the cookie."""
    cookie = _auth.mint_session_cookie()
    payload_b64, sig_b64 = cookie.split(".", 1)
    # Flip the first character of the signature (legal b64url).
    new_first = "A" if sig_b64[0] != "A" else "B"
    tampered = f"{payload_b64}.{new_first}{sig_b64[1:]}"
    assert _auth.verify_session_cookie(tampered) is False


def test_verify_rejects_expired_cookie() -> None:
    """A cookie whose ``expires_at`` is in the past is rejected."""
    # Mint with a fake clock far in the past so expiry has elapsed.
    cookie = _auth.mint_session_cookie(now=0)
    # Verify "now" = far future.
    assert _auth.verify_session_cookie(cookie, now=10**12) is False


def test_mint_honours_a_longer_ttl() -> None:
    """Remember-me sessions are the same signed cookie with a later expiry."""
    thirty_days = _auth.SESSION_COOKIE_REMEMBER_TTL_SECONDS
    assert thirty_days == 30 * 24 * 60 * 60
    cookie = _auth.mint_session_cookie(now=1_000, ttl_seconds=thirty_days)
    assert _auth.verify_session_cookie(cookie, now=1_000 + thirty_days - 1) is True
    assert _auth.verify_session_cookie(cookie, now=1_000 + thirty_days) is False
    # ...and the default is still the 8h workday session.
    default = _auth.mint_session_cookie(now=1_000)
    assert _auth.verify_session_cookie(default, now=1_000 + 8 * 3600 - 1) is True
    assert _auth.verify_session_cookie(default, now=1_000 + 8 * 3600) is False


def test_session_cookie_expiry_reads_only_verified_cookies() -> None:
    cookie = _auth.mint_session_cookie(now=1_000, ttl_seconds=600)
    assert _auth.session_cookie_expiry(cookie, now=1_000) == 1_600
    assert _auth.session_cookie_expiry(cookie, now=1_600) is None  # expired
    assert _auth.session_cookie_expiry("garbage") is None
    payload, _, sig = cookie.partition(".")
    assert _auth.session_cookie_expiry(f"{payload}.{sig[:-2]}AA") is None  # forged


def test_secret_file_chmod_0600(isolate_secret: Path) -> None:
    """The on-disk secret is mode 0600 after first creation."""
    _auth.mint_session_cookie()
    mode = isolate_secret.stat().st_mode & 0o777
    assert mode == 0o600, f"expected 0600 secret perms, got {oct(mode)}"


def test_secret_reused_across_mints(isolate_secret: Path) -> None:
    """The HMAC secret is not regenerated on every call.

    Otherwise every cookie would re-verify against a different secret
    and the whole scheme falls over.
    """
    a = _auth.mint_session_cookie()
    secret_bytes_first = isolate_secret.read_bytes()
    b = _auth.mint_session_cookie()
    secret_bytes_second = isolate_secret.read_bytes()
    assert a != b  # nonces in payload differ
    assert secret_bytes_first == secret_bytes_second
    # Both must still verify.
    assert _auth.verify_session_cookie(a)
    assert _auth.verify_session_cookie(b)


def test_allowed_origins_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """``HAL0_ALLOWED_ORIGINS`` replaces the default tuple."""
    monkeypatch.setenv(
        "HAL0_ALLOWED_ORIGINS",
        "https://demo.example.com, http://other.example.com",
    )
    origins = _auth.allowed_origins()
    assert origins == ("https://demo.example.com", "http://other.example.com")


def test_allowed_origins_empty_env_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty override falls back to the default allowlist (dev convenience)."""
    monkeypatch.setenv("HAL0_ALLOWED_ORIGINS", "")
    assert _auth.allowed_origins() == _auth.DEFAULT_ALLOWED_ORIGINS


# ---------------------------------------------------------------------------
# Integration: drive the WS gate via TestClient.


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Spin up the app with isolated secret + HAL0_HOME.

    We intentionally don't reuse the project-wide ``client`` fixture so
    the secret path is on a per-test tmp + lifespan is fresh.
    """
    monkeypatch.setenv("HAL0_AGENT_SECRET_PATH", str(tmp_path / "secret.bin"))
    monkeypatch.setenv("HAL0_HOME", str(tmp_path / "hal0_home"))
    os.makedirs(tmp_path / "hal0_home" / "etc" / "hal0", exist_ok=True)
    # Lock origins to a known set so tests are deterministic.
    monkeypatch.setenv("HAL0_ALLOWED_ORIGINS", "http://127.0.0.1:8080")

    from hal0.api import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c


def _get_cookie_value(client: TestClient) -> str:
    """Drive the handshake endpoint + read the cookie out of the response."""
    resp = client.get("/api/agents/hermes/session/handshake")
    assert resp.status_code == 200, resp.text
    cookie = resp.cookies.get(_auth.SESSION_COOKIE_NAME)
    assert cookie is not None
    return cookie


def test_handshake_sets_session_cookie(client: TestClient) -> None:
    """The handshake endpoint mints a cookie + returns identity info."""
    resp = client.get("/api/agents/hermes/session/handshake")
    assert resp.status_code == 200
    assert resp.json() == {"agent_id": "hermes", "ok": True}
    assert _auth.SESSION_COOKIE_NAME in resp.cookies


def test_handshake_does_not_shorten_a_longer_lived_session(client: TestClient) -> None:
    """Opening agent chat must not swap a 30-day remember-me cookie for an 8h one.

    The handshake mints the same cookie the login does; left unconditional it
    silently logged a "remembered" operator out the next morning.
    """
    import time

    long_lived = _auth.mint_session_cookie(ttl_seconds=_auth.SESSION_COOKIE_REMEMBER_TTL_SECONDS)
    client.cookies.set(_auth.SESSION_COOKIE_NAME, long_lived)

    resp = client.get("/api/agents/hermes/session/handshake")

    assert resp.status_code == 200
    assert _auth.SESSION_COOKIE_NAME not in resp.cookies  # nothing re-issued
    held = client.cookies.get(_auth.SESSION_COOKIE_NAME)
    assert _auth.verify_session_cookie(held, now=time.time() + 29 * 24 * 3600) is True


def test_handshake_still_renews_a_session_about_to_lapse(client: TestClient) -> None:
    """The existing behaviour for workday sessions: attaching extends to a full 8h."""
    import time

    nearly_gone = _auth.mint_session_cookie(ttl_seconds=60)
    client.cookies.set(_auth.SESSION_COOKIE_NAME, nearly_gone)

    resp = client.get("/api/agents/hermes/session/handshake")

    renewed = resp.cookies.get(_auth.SESSION_COOKIE_NAME)
    assert renewed is not None
    assert _auth.verify_session_cookie(renewed, now=time.time() + 7 * 3600) is True


def test_ws_upgrade_with_missing_cookie_rejected(client: TestClient) -> None:
    """A WS upgrade with no cookie at all is rejected (4403)."""
    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect(
            "/api/agents/hermes/events",
            headers={"origin": "http://127.0.0.1:8080"},
        ),
    ):
        pass
    assert exc_info.value.code == 4403


def test_ws_upgrade_with_bad_cookie_rejected(client: TestClient) -> None:
    """A WS upgrade with a junk cookie is rejected (4403)."""
    client.cookies.set(_auth.SESSION_COOKIE_NAME, "not.real")
    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect(
            "/api/agents/hermes/events",
            headers={"origin": "http://127.0.0.1:8080"},
        ),
    ):
        pass
    assert exc_info.value.code == 4403


def test_ws_upgrade_with_disallowed_origin_rejected(client: TestClient) -> None:
    """A valid cookie + a non-allowlisted Origin is rejected (4403)."""
    cookie = _get_cookie_value(client)
    client.cookies.set(_auth.SESSION_COOKIE_NAME, cookie)
    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect(
            "/api/agents/hermes/events",
            headers={"origin": "https://attacker.example.com"},
        ),
    ):
        pass
    assert exc_info.value.code == 4403


def test_rest_session_create_requires_cookie(client: TestClient) -> None:
    """REST shim refuses to call hermes without a session cookie."""
    resp = client.post("/api/agents/hermes/session/create", json={})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Same-origin fallback (#2277): the allowlist is an install-time snapshot of
# the box's LAN IPs, so after a DHCP change the dashboard served by this very
# hal0-api must still be able to upgrade its WebSocket. RFC 5737 addresses.


@pytest.fixture
def accepted_ws(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the upstream hop so an accepted upgrade sends ``ok`` and closes.

    Lets the gate tests tell "accepted" from "4403" without a hermes runtime.
    """
    from starlette.websockets import WebSocket

    from hal0.api.agents import chat_proxy

    async def _stub_proxy_ws(browser_ws: WebSocket, **_: object) -> None:
        await browser_ws.send_text("ok")
        await browser_ws.close()

    monkeypatch.setattr(chat_proxy, "_proxy_ws", _stub_proxy_ws)


def _ws_close_code(client: TestClient, headers: dict[str, str], path: str = "events") -> int | None:
    """Open the WS with a valid cookie; return the close code, or None if accepted."""
    client.cookies.set(_auth.SESSION_COOKIE_NAME, _get_cookie_value(client))
    try:
        with client.websocket_connect(f"/api/agents/hermes/{path}", headers=headers) as ws:
            assert ws.receive_text() == "ok"
    except WebSocketDisconnect as exc:
        return exc.code
    return None


@pytest.mark.parametrize("path", ["events", "submit"])
def test_ws_same_origin_accepted_after_lan_ip_change(
    client: TestClient, accepted_ws: None, path: str
) -> None:
    """Origin == Host but absent from the install-time allowlist is accepted."""
    headers = {"origin": "http://192.0.2.20:8080", "host": "192.0.2.20:8080"}
    assert _ws_close_code(client, headers, path) is None


def test_ws_same_origin_https_behind_tls_proxy_accepted(
    client: TestClient, accepted_ws: None
) -> None:
    """A TLS-terminating reverse proxy hands hal0 ``ws`` for an ``https`` page."""
    headers = {"origin": "https://192.0.2.20", "host": "192.0.2.20"}
    assert _ws_close_code(client, headers) is None


def _wss_close_code(client: TestClient, origin: str) -> int | None:
    """Like :func:`_ws_close_code` but over ``wss`` terminated by hal0 itself."""
    client.cookies.set(_auth.SESSION_COOKIE_NAME, _get_cookie_value(client))
    url = "wss://192.0.2.20:8443/api/agents/hermes/events"
    try:
        with client.websocket_connect(url, headers={"origin": origin}) as ws:
            assert ws.receive_text() == "ok"
    except WebSocketDisconnect as exc:
        return exc.code
    return None


def test_wss_same_origin_https_page_accepted(client: TestClient, accepted_ws: None) -> None:
    assert _wss_close_code(client, "https://192.0.2.20:8443") is None


def test_wss_rejects_plain_http_page_on_same_authority(
    client: TestClient, accepted_ws: None
) -> None:
    """A TLS endpoint hal0 terminates itself never trusts a plain-HTTP page."""
    assert _wss_close_code(client, "http://192.0.2.20:8443") == 4403


def test_ws_same_origin_ipv6_literal_accepted(client: TestClient, accepted_ws: None) -> None:
    headers = {"origin": "http://[2001:db8::20]:8080", "host": "[2001:DB8::20]:8080"}
    assert _ws_close_code(client, headers) is None


@pytest.mark.parametrize(
    ("origin", "host"),
    [
        # Different host entirely: cross-origin.
        ("http://192.0.2.99:8080", "192.0.2.20:8080"),
        ("https://attacker.example.com", "192.0.2.20:8080"),
        # Same host, different port: cross-origin.
        ("http://192.0.2.20:9999", "192.0.2.20:8080"),
        ("http://192.0.2.20", "192.0.2.20:8080"),
        # A DNS name can be rebound to the box: matching Origin/Host on a
        # name is not proof of same-origin, so only IP literals qualify.
        ("http://rebind.example.com:8080", "rebind.example.com:8080"),
        ("http://hal0.example.net", "hal0.example.net"),
        # Non-web schemes and opaque origins never count as same-origin.
        ("chrome-extension://192.0.2.20:8080", "192.0.2.20:8080"),
        ("null", "null"),
        # An Origin carrying userinfo or a path is not a browser Origin.
        ("http://user@192.0.2.20:8080", "user@192.0.2.20:8080"),
        ("http://192.0.2.20:8080/x", "192.0.2.20:8080"),
        ("http://192.0.2.20:8080/", "192.0.2.20:8080"),
        # Same address, different spelling: never normalised into a match.
        ("http://[::ffff:192.0.2.20]:8080", "192.0.2.20:8080"),
        ("http://192.0.2.20", "192.0.2.20:80"),
        # A trailing-dot Host is not an IP literal.
        ("http://192.0.2.20.:8080", "192.0.2.20.:8080"),
        # IPv6 zone ids are rejected even when byte-identical.
        ("http://[fe80::1%25eth0]:8080", "[fe80::1%25eth0]:8080"),
    ],
)
def test_ws_cross_origin_still_rejected(
    client: TestClient, accepted_ws: None, origin: str, host: str
) -> None:
    assert _ws_close_code(client, {"origin": origin, "host": host}) == 4403


def test_ws_configured_origin_still_accepted(client: TestClient, accepted_ws: None) -> None:
    """An allowlisted Origin is accepted even when it differs from Host."""
    headers = {"origin": "http://127.0.0.1:8080", "host": "192.0.2.20:8080"}
    assert _ws_close_code(client, headers) is None


def test_ws_missing_origin_still_rejected(client: TestClient, accepted_ws: None) -> None:
    """No Origin header keeps the existing deny (browsers always send one on WS)."""
    assert _ws_close_code(client, {"host": "192.0.2.20:8080"}) == 4403


def test_ws_same_origin_without_cookie_still_rejected(
    client: TestClient, accepted_ws: None
) -> None:
    """The fallback relaxes only the Origin half of the gate, never the cookie."""
    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect(
            "/api/agents/hermes/events",
            headers={"origin": "http://192.0.2.20:8080", "host": "192.0.2.20:8080"},
        ),
    ):
        pass
    assert exc_info.value.code == 4403
