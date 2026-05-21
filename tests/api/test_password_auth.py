"""Password auth + session cookie tests (ADR-0001 Child A, refs #54/#55).

Covers the contract spelled out in issue #55:

  - First-run ``POST /api/auth/password`` works without any credentials
    (the wizard's claim-ownership path).
  - Second ``POST /api/auth/password`` without credentials is rejected
    with 401 ``auth.required``.
  - Second ``POST /api/auth/password`` with a writer Bearer token works.
  - ``POST /api/auth/login`` happy path sets the ``hal0_session`` cookie
    and subsequent writer requests with the cookie + the
    ``X-Requested-With`` CSRF tripwire succeed.
  - The same writer request *without* the tripwire returns 403
    ``auth.csrf_required``.
  - Bearer auth bypasses the CSRF check entirely.
  - Bad password returns 401 ``auth.invalid``.
  - ``POST /api/auth/logout`` clears the cookie.
  - ``GET /api/auth/status`` reports ``password_set`` and ``auth_mode``.
  - Pre-existing token-only tests still pass (regression covered by
    the wider suite; this file is additive).

Test-app pattern mirrors tests/api/test_auth_routes.py: build a fresh
FastAPI app per test with HAL0_AUTH_ENABLED=1 and HAL0_HOME pointed at a
tmp dir so the keyring + tokens.toml are scoped to the test.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.api.middleware.auth import SESSION_COOKIE_NAME
from hal0.auth.tokens import TokenStore


def _install_loopback_client_middleware(app: object) -> None:
    """Pin ``request.client.host`` to ``127.0.0.1`` for the test app.

    Starlette's TestClient hard-codes ``scope['client']`` to
    ``('testclient', 50000)``. Routes that distinguish loopback callers
    from LAN peers (the first-run OTP gate, FINDINGS §28) need a real
    127.0.0.1 here so the loopback bypass exercises in tests. The
    middleware reads ``X-Test-Client-Ip`` when present so individual
    tests can simulate a LAN attacker by setting the header.
    """
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request as StarletteRequest

    class _PinClientMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: StarletteRequest, call_next):  # type: ignore[override]
            override = request.headers.get("x-test-client-ip")
            host = override or "127.0.0.1"
            # ASGI scope is the mutable source of truth for
            # ``request.client``.
            request.scope["client"] = (host, 12345)
            return await call_next(request)

    app.add_middleware(_PinClientMiddleware)  # type: ignore[attr-defined]


@pytest.fixture
def auth_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A fresh app with auth enabled and an isolated store + keyring.

    The TestClient appears as a loopback caller by default (FINDINGS
    §28 OTP bypass). Tests that need to simulate a LAN attacker can
    pass ``headers={"X-Test-Client-Ip": "10.0.1.50"}``.
    """
    monkeypatch.delenv("HAL0_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("HAL0_AUTH_ENABLED", "1")
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    app = create_app()
    _install_loopback_client_middleware(app)
    with TestClient(app) as c:
        # Swap in a tmp-rooted store so the password hash + tokens land
        # under tmp_path/etc/hal0/tokens.toml. The keyring file lands at
        # tmp_path/etc/hal0/keyring on first session-token mint.
        store = TokenStore(tmp_path / "etc" / "hal0" / "tokens.toml")
        c.app.state.token_store = store
        # Reset the rate-limit bucket so the per-test login matrices
        # don't tip into 429 just because the fixture itself fired a
        # few attempts.
        limiter = getattr(c.app.state, "auth_rate_limiter", None)
        if limiter is not None:
            limiter.reset()
        yield c


def _admin_bearer(auth_app: TestClient, label: str = "test-admin") -> dict[str, str]:
    """Mint an admin token and return its Bearer header dict."""
    store: TokenStore = auth_app.app.state.token_store
    _, raw = store.create(label=label, scope="admin")
    return {"Authorization": f"Bearer {raw}"}


# ── GET /api/auth/status ─────────────────────────────────────────────────────


def test_status_reports_open_mode_on_fresh_install(auth_app: TestClient) -> None:
    response = auth_app.get("/api/auth/status")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["enabled"] is True
    assert body["password_set"] is False
    assert body["auth_mode"] == "open"
    # Bearer + session + forwarded all advertised.
    assert "bearer" in body["modes"]
    assert "session" in body["modes"]


def test_status_reports_password_mode_after_first_set(auth_app: TestClient) -> None:
    auth_app.post("/api/auth/password", json={"password": "correcthorse"})
    response = auth_app.get("/api/auth/status")
    body = response.json()
    assert body["password_set"] is True
    assert body["auth_mode"] == "password"


# ── POST /api/auth/password (first-run + rotation) ───────────────────────────


def test_first_run_set_password_no_auth_required(auth_app: TestClient) -> None:
    """No credentials are required when the store has no password yet."""
    response = auth_app.post("/api/auth/password", json={"password": "correcthorse"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["password_set"] is True
    assert body["rotated"] is False


def test_second_set_password_without_auth_returns_401(auth_app: TestClient) -> None:
    """Once a password is set, the endpoint requires writer scope."""
    # First run claims ownership.
    first = auth_app.post("/api/auth/password", json={"password": "correcthorse"})
    assert first.status_code == 200

    # Second call with no auth → 401 auth.required.
    second = auth_app.post("/api/auth/password", json={"password": "newbatterystaple"})
    assert second.status_code == 401
    assert second.json()["error"]["code"] == "auth.required"


def test_second_set_password_with_writer_bearer_works(auth_app: TestClient) -> None:
    """Rotation succeeds when called with a writer-scoped Bearer."""
    auth_app.post("/api/auth/password", json={"password": "correcthorse"})

    bearer = _admin_bearer(auth_app)
    response = auth_app.post(
        "/api/auth/password",
        json={"password": "newbatterystaple"},
        headers=bearer,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["password_set"] is True
    assert body["rotated"] is True


def test_set_password_too_short_returns_400(auth_app: TestClient) -> None:
    response = auth_app.post("/api/auth/password", json={"password": "short"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "auth.password_too_short"


# ── POST /api/auth/login ─────────────────────────────────────────────────────


def test_login_happy_path_sets_session_cookie(auth_app: TestClient) -> None:
    auth_app.post("/api/auth/password", json={"password": "correcthorse"})
    response = auth_app.post(
        "/api/auth/login",
        json={"username": "owner", "password": "correcthorse"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user"] == "owner"
    assert body["scope"] == "admin"
    # TestClient stashes the cookie in its jar.
    assert SESSION_COOKIE_NAME in auth_app.cookies
    assert auth_app.cookies[SESSION_COOKIE_NAME]


def test_login_wrong_password_returns_401(auth_app: TestClient) -> None:
    auth_app.post("/api/auth/password", json={"password": "correcthorse"})
    response = auth_app.post(
        "/api/auth/login",
        json={"username": "owner", "password": "wrong"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth.invalid"
    assert SESSION_COOKIE_NAME not in auth_app.cookies


def test_login_wrong_username_returns_401(auth_app: TestClient) -> None:
    auth_app.post("/api/auth/password", json={"password": "correcthorse"})
    response = auth_app.post(
        "/api/auth/login",
        json={"username": "admin", "password": "correcthorse"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth.invalid"


def test_login_before_password_set_returns_401(auth_app: TestClient) -> None:
    """No password configured → login still returns 401 (not a leakier error)."""
    response = auth_app.post(
        "/api/auth/login",
        json={"username": "owner", "password": "anything"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth.invalid"


# ── Cookie + writer routes + CSRF tripwire ───────────────────────────────────


def _login(auth_app: TestClient, password: str = "correcthorse") -> None:
    auth_app.post("/api/auth/password", json={"password": password})
    response = auth_app.post(
        "/api/auth/login",
        json={"username": "owner", "password": password},
    )
    assert response.status_code == 200, response.text


def test_cookie_writer_request_with_xrw_succeeds(auth_app: TestClient) -> None:
    """Logged-in browser flow: cookie + X-Requested-With → 200."""
    _login(auth_app)
    # /api/auth/tokens is admin-only and POST is a writer mutation that
    # exercises require_admin (which itself goes through require_token);
    # combined with the CSRF check inside require_writer, this hits the
    # cookie-writer + tripwire path.
    response = auth_app.post(
        "/api/auth/tokens",
        json={"label": "from-cookie", "scope": "all"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    # Admin gate must pass (owner scope is "admin") and CSRF tripwire is
    # present, so we expect a 200 ticket creation.
    assert response.status_code == 200, response.text
    assert response.json()["label"] == "from-cookie"


def test_cookie_writer_request_with_csrf_token_succeeds(auth_app: TestClient) -> None:
    """The X-CSRF-Token alternative also unblocks cookie-authed writers."""
    _login(auth_app)
    session_cookie = auth_app.cookies[SESSION_COOKIE_NAME]
    response = auth_app.post(
        "/api/auth/tokens",
        json={"label": "from-cookie-csrf", "scope": "all"},
        headers={"X-CSRF-Token": session_cookie[:16]},
    )
    assert response.status_code == 200, response.text


def test_cookie_writer_request_without_xrw_returns_403(auth_app: TestClient) -> None:
    """Cookie auth on a writer route without the tripwire → 403 csrf."""
    _login(auth_app)
    response = auth_app.post(
        "/api/auth/tokens",
        json={"label": "from-cookie-no-csrf", "scope": "all"},
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "auth.csrf_required"


def test_cookie_writer_request_with_wrong_csrf_token_returns_403(
    auth_app: TestClient,
) -> None:
    """A garbage X-CSRF-Token doesn't satisfy the tripwire."""
    _login(auth_app)
    response = auth_app.post(
        "/api/auth/tokens",
        json={"label": "from-cookie-bad-csrf", "scope": "all"},
        headers={"X-CSRF-Token": "nope-not-the-prefix"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "auth.csrf_required"


def test_bearer_writer_request_without_xrw_succeeds(auth_app: TestClient) -> None:
    """Bearer auth on a writer route is exempt from the CSRF check."""
    bearer = _admin_bearer(auth_app)
    response = auth_app.post(
        "/api/auth/tokens",
        json={"label": "from-bearer", "scope": "all"},
        headers=bearer,
    )
    assert response.status_code == 200, response.text


def test_cookie_reader_request_does_not_require_csrf(auth_app: TestClient) -> None:
    """The CSRF tripwire only applies to writer routes."""
    _login(auth_app)
    # /api/auth/me is a reader route (require_token only, no
    # require_writer); cookie auth should pass with no tripwire.
    response = auth_app.get("/api/auth/me")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "session"
    assert body["identity"] == "owner"


# ── POST /api/auth/logout ────────────────────────────────────────────────────


def test_logout_clears_cookie(auth_app: TestClient) -> None:
    _login(auth_app)
    assert SESSION_COOKIE_NAME in auth_app.cookies

    response = auth_app.post("/api/auth/logout")
    assert response.status_code == 204
    # Starlette emits a Set-Cookie with Max-Age=0; httpx pops the key.
    assert SESSION_COOKIE_NAME not in auth_app.cookies

    # A subsequent cookie-protected writer call now falls through to
    # auth.required (no cookie, no Bearer).
    followup = auth_app.post(
        "/api/auth/tokens",
        json={"label": "post-logout", "scope": "all"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert followup.status_code == 401
    assert followup.json()["error"]["code"] == "auth.required"


# ── Expired/invalid cookie path ──────────────────────────────────────────────


def test_invalid_cookie_returns_401_auth_invalid(auth_app: TestClient) -> None:
    """A cookie that doesn't decode as a valid JWT → 401 auth.invalid."""
    auth_app.cookies.set(SESSION_COOKIE_NAME, "totally-not-a-jwt")
    response = auth_app.get(
        "/api/auth/me",
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth.invalid"


# ── Cookie attributes (Secure flag detection) ────────────────────────────────


def test_login_cookie_is_httponly(auth_app: TestClient) -> None:
    """The Set-Cookie header must carry the HttpOnly flag."""
    auth_app.post("/api/auth/password", json={"password": "correcthorse"})
    response = auth_app.post(
        "/api/auth/login",
        json={"username": "owner", "password": "correcthorse"},
    )
    set_cookie = response.headers.get("set-cookie", "")
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie.lower() or "samesite=lax" in set_cookie.lower()


def test_login_cookie_is_secure_when_xfp_https(auth_app: TestClient) -> None:
    """``X-Forwarded-Proto: https`` triggers the Secure cookie attribute."""
    auth_app.post("/api/auth/password", json={"password": "correcthorse"})
    response = auth_app.post(
        "/api/auth/login",
        json={"username": "owner", "password": "correcthorse"},
        headers={"X-Forwarded-Proto": "https"},
    )
    set_cookie = response.headers.get("set-cookie", "")
    assert "Secure" in set_cookie
