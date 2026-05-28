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
