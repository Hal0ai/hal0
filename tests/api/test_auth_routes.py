"""Token CRUD route tests.

POST /api/auth/tokens, GET /api/auth/tokens, DELETE /api/auth/tokens/{id}.
All admin-protected — non-admin tokens (scope='all', 'v1-only') get 403.

This file also covers the FINDINGS §28 first-run OTP lockfile and the
FINDINGS §32 IP-bucket rate limiter — both bolted onto the /api/auth
surface as v1 pre-launch security fixes.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.api.auth import first_run as first_run_lock
from hal0.api.auth.rate_limit import IpRateLimiter
from hal0.auth.tokens import TokenStore


def _install_loopback_client_middleware(app: object) -> None:
    """Pin ``request.client.host`` to ``127.0.0.1`` for the test app.

    Mirrors the helper in tests/api/test_password_auth.py — the §28
    OTP gate distinguishes loopback callers from LAN peers, so we need
    a way to drive both branches from the same TestClient. Tests set
    ``X-Test-Client-Ip`` to spoof a LAN address.
    """
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request as StarletteRequest

    class _PinClientMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: StarletteRequest, call_next):  # type: ignore[override]
            override = request.headers.get("x-test-client-ip")
            host = override or "127.0.0.1"
            request.scope["client"] = (host, 12345)
            return await call_next(request)

    app.add_middleware(_PinClientMiddleware)  # type: ignore[attr-defined]


@pytest.fixture
def auth_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.delenv("HAL0_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("HAL0_AUTH_ENABLED", "1")
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    app = create_app()
    _install_loopback_client_middleware(app)
    with TestClient(app) as c:
        c.app.state.token_store = TokenStore(tmp_path / "tokens.toml")
        limiter = getattr(c.app.state, "auth_rate_limiter", None)
        if limiter is not None:
            limiter.reset()
        yield c


@pytest.fixture
def admin_headers(auth_app: TestClient) -> dict[str, str]:
    """Mint an admin token via the store and return its Bearer header."""
    store: TokenStore = auth_app.app.state.token_store
    _, raw = store.create(label="bootstrap-admin", scope="admin")
    return {"Authorization": f"Bearer {raw}"}


# ── POST /api/auth/tokens ────────────────────────────────────────────────────


def test_create_token_returns_raw_value_once(
    auth_app: TestClient, admin_headers: dict[str, str]
) -> None:
    response = auth_app.post(
        "/api/auth/tokens",
        json={"label": "openwebui-bridge", "scope": "all"},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["label"] == "openwebui-bridge"
    assert body["scope"] == "all"
    assert body["token"].startswith("hal0_")
    assert "warning" in body  # UI surfaces the "shown once" warning verbatim
    raw = body["token"]

    # The raw token works for auth on the next call.
    me = auth_app.get("/api/auth/me", headers={"Authorization": f"Bearer {raw}"})
    assert me.status_code == 200
    assert me.json()["identity"] == "openwebui-bridge"

    # Subsequent list calls do NOT include the raw token, only metadata.
    listing = auth_app.get("/api/auth/tokens", headers=admin_headers)
    assert listing.status_code == 200
    rows = listing.json()["tokens"]
    target = [r for r in rows if r["label"] == "openwebui-bridge"]
    assert len(target) == 1
    assert "token" not in target[0]
    assert "hash" not in target[0]


def test_create_token_requires_admin(auth_app: TestClient) -> None:
    """A non-admin token (scope='all') gets 403 on token CRUD."""
    store: TokenStore = auth_app.app.state.token_store
    _, raw = store.create(label="non-admin", scope="all")
    response = auth_app.post(
        "/api/auth/tokens",
        json={"label": "another", "scope": "all"},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "auth.forbidden"


def test_create_token_requires_auth(auth_app: TestClient) -> None:
    response = auth_app.post(
        "/api/auth/tokens",
        json={"label": "anon-attempt", "scope": "all"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth.required"


def test_create_token_duplicate_label_409(
    auth_app: TestClient, admin_headers: dict[str, str]
) -> None:
    auth_app.post(
        "/api/auth/tokens",
        json={"label": "dup", "scope": "all"},
        headers=admin_headers,
    )
    response = auth_app.post(
        "/api/auth/tokens",
        json={"label": "dup", "scope": "admin"},
        headers=admin_headers,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "auth.duplicate_label"


def test_create_token_invalid_scope_400(
    auth_app: TestClient, admin_headers: dict[str, str]
) -> None:
    response = auth_app.post(
        "/api/auth/tokens",
        json={"label": "x", "scope": "superuser"},
        headers=admin_headers,
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "auth.invalid_scope"


# ── GET /api/auth/tokens ─────────────────────────────────────────────────────


def test_list_tokens_returns_metadata_only(
    auth_app: TestClient, admin_headers: dict[str, str]
) -> None:
    auth_app.post(
        "/api/auth/tokens",
        json={"label": "a", "scope": "all"},
        headers=admin_headers,
    )
    auth_app.post(
        "/api/auth/tokens",
        json={"label": "b", "scope": "v1-only"},
        headers=admin_headers,
    )
    response = auth_app.get("/api/auth/tokens", headers=admin_headers)
    assert response.status_code == 200
    rows = response.json()["tokens"]
    labels = sorted(r["label"] for r in rows)
    assert "a" in labels and "b" in labels
    for r in rows:
        assert "hash" not in r
        assert "token" not in r
        assert {"id", "label", "scope", "created_at", "last_used_at"} <= set(r)


# ── DELETE /api/auth/tokens/{id} ─────────────────────────────────────────────


def test_revoke_token(auth_app: TestClient, admin_headers: dict[str, str]) -> None:
    create = auth_app.post(
        "/api/auth/tokens",
        json={"label": "victim", "scope": "all"},
        headers=admin_headers,
    )
    token_id = create.json()["id"]
    raw = create.json()["token"]

    response = auth_app.delete(f"/api/auth/tokens/{token_id}", headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["revoked"] == token_id

    # Re-using the raw token now 401s.
    me = auth_app.get("/api/auth/me", headers={"Authorization": f"Bearer {raw}"})
    assert me.status_code == 401
    assert me.json()["error"]["code"] == "auth.invalid"


def test_revoke_unknown_id_404(auth_app: TestClient, admin_headers: dict[str, str]) -> None:
    response = auth_app.delete("/api/auth/tokens/notarealid", headers=admin_headers)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "auth.token_not_found"


def test_revoke_requires_admin(auth_app: TestClient) -> None:
    store: TokenStore = auth_app.app.state.token_store
    target, _ = store.create(label="target", scope="all")
    _, non_admin_raw = store.create(label="non-admin", scope="all")
    response = auth_app.delete(
        f"/api/auth/tokens/{target.id}",
        headers={"Authorization": f"Bearer {non_admin_raw}"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "auth.forbidden"


# ── FINDINGS §28: first-run OTP lockfile ─────────────────────────────────────


def _read_lockfile_otp() -> str | None:
    """Read the current first-run OTP off disk (None when absent).

    The lockfile is minted by the API lifespan against the
    HAL0_HOME-rooted state dir; the auth_app fixture pre-points
    HAL0_HOME at tmp_path so this resolves to the same file the route
    will validate against.
    """
    lock = first_run_lock.read_lockfile()
    return lock.otp if lock else None


def test_first_run_password_with_valid_otp_succeeds(auth_app: TestClient) -> None:
    """A LAN peer presenting the OTP from the lockfile can claim ownership."""
    otp = _read_lockfile_otp()
    assert otp is not None, "lifespan should have minted a lockfile on a fresh install"

    response = auth_app.post(
        "/api/auth/password",
        json={"password": "correcthorse", "otp": otp},
        headers={"X-Test-Client-Ip": "10.0.1.50"},  # simulate LAN peer
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["password_set"] is True
    assert body["rotated"] is False

    # The lockfile must be consumed on a successful first-run set so a
    # later attacker can't replay the same OTP.
    assert _read_lockfile_otp() is None, "lockfile must be unlinked on success"


def test_first_run_password_with_wrong_otp_returns_401(auth_app: TestClient) -> None:
    """A LAN peer with a bad OTP gets 401 auth.first_run_otp_invalid."""
    assert _read_lockfile_otp() is not None
    response = auth_app.post(
        "/api/auth/password",
        json={"password": "correcthorse", "otp": "totally-wrong-token"},
        headers={"X-Test-Client-Ip": "10.0.1.50"},
    )
    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "auth.first_run_otp_invalid"

    # Lockfile must NOT be consumed on a failed attempt — the legitimate
    # operator still needs to finish the wizard.
    assert _read_lockfile_otp() is not None


def test_first_run_password_loopback_bypasses_otp(auth_app: TestClient) -> None:
    """A 127.0.0.1 caller can set the password with no OTP at all."""
    # No OTP, no X-Test-Client-Ip override → fixture pins to 127.0.0.1.
    response = auth_app.post(
        "/api/auth/password",
        json={"password": "correcthorse"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["password_set"] is True
    assert body["rotated"] is False
    # Loopback path still consumes the lockfile so a follow-up LAN
    # attacker can't replay the OTP after the operator finishes.
    assert _read_lockfile_otp() is None


def test_first_run_password_lan_without_otp_returns_401(auth_app: TestClient) -> None:
    """A LAN peer with no OTP at all gets 401 auth.first_run_otp_required."""
    response = auth_app.post(
        "/api/auth/password",
        json={"password": "correcthorse"},
        headers={"X-Test-Client-Ip": "10.0.1.50"},
    )
    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "auth.first_run_otp_required"

    # Lockfile preserved so the legitimate operator can still claim.
    assert _read_lockfile_otp() is not None


def test_first_run_password_header_otp_also_accepted(auth_app: TestClient) -> None:
    """The X-Hal0-First-Run-OTP header is equivalent to the JSON body field."""
    otp = _read_lockfile_otp()
    assert otp is not None
    response = auth_app.post(
        "/api/auth/password",
        json={"password": "correcthorse"},
        headers={
            "X-Test-Client-Ip": "10.0.1.50",
            "X-Hal0-First-Run-OTP": otp,
        },
    )
    assert response.status_code == 200, response.text


def test_set_password_after_first_returns_existing_behaviour(auth_app: TestClient) -> None:
    """Second set without auth → 401 auth.required (existing behaviour preserved).

    The pre-FINDINGS contract was: first-run claim is auth-free, every
    subsequent call requires writer scope. The new §28 lockfile only
    gates the first-run path; the rotation path is unchanged.
    """
    # First-run claim via loopback bypass.
    auth_app.post("/api/auth/password", json={"password": "correcthorse"})

    # Second call without any credential — even from loopback — must
    # still require the writer-scope token. This is the §28 mitigation
    # NOT regressing the rotation path.
    second = auth_app.post(
        "/api/auth/password",
        json={"password": "newbatterystaple"},
    )
    assert second.status_code == 401, second.text
    assert second.json()["error"]["code"] == "auth.required"


# ── FINDINGS §32: login rate-limit ───────────────────────────────────────────


def _set_owner_password(auth_app: TestClient, password: str = "correcthorse") -> None:
    """Helper: claim ownership via the loopback bypass so login tests can run."""
    r = auth_app.post("/api/auth/password", json={"password": password})
    assert r.status_code == 200, r.text


def test_login_under_cap_succeeds(auth_app: TestClient) -> None:
    """5 successful login attempts in a row stay under the cap."""
    _set_owner_password(auth_app)
    # Reset the limiter so the password-set call doesn't count toward
    # the login bucket. (It doesn't share scope, but a defensive reset
    # makes the test order-independent.)
    auth_app.app.state.auth_rate_limiter.reset()

    for _ in range(5):
        r = auth_app.post(
            "/api/auth/login",
            json={"username": "owner", "password": "correcthorse"},
        )
        assert r.status_code == 200, r.text


def test_login_sixth_attempt_in_window_returns_429(auth_app: TestClient) -> None:
    """6th attempt within 1min → 429 auth.rate_limited with Retry-After."""
    _set_owner_password(auth_app)
    auth_app.app.state.auth_rate_limiter.reset()

    # Fire 5 attempts (mix of success and failure — both burn the bucket).
    for _ in range(5):
        auth_app.post(
            "/api/auth/login",
            json={"username": "owner", "password": "wrong"},
        )

    # 6th attempt — even with the correct password — must hit 429.
    response = auth_app.post(
        "/api/auth/login",
        json={"username": "owner", "password": "correcthorse"},
    )
    assert response.status_code == 429, response.text
    assert response.json()["error"]["code"] == "auth.rate_limited"
    # Retry-After header surfaced for client backoff.
    retry_after = response.headers.get("retry-after")
    assert retry_after is not None
    assert int(retry_after) >= 1


def test_login_rate_limit_resets_after_window(auth_app: TestClient) -> None:
    """After the window elapses, new attempts succeed again.

    Drives a stub clock through the limiter so the test doesn't have to
    actually sleep 60s.
    """
    _set_owner_password(auth_app)

    # Replace the limiter with one that has a controllable clock.
    fake_now = [0.0]

    def _clock() -> float:
        return fake_now[0]

    auth_app.app.state.auth_rate_limiter = IpRateLimiter(
        limit=5,
        window_seconds=60.0,
        clock=_clock,
    )

    # Burn the bucket: 5 fast attempts.
    for _ in range(5):
        auth_app.post(
            "/api/auth/login",
            json={"username": "owner", "password": "wrong"},
        )
    # 6th attempt at the same instant → 429.
    blocked = auth_app.post(
        "/api/auth/login",
        json={"username": "owner", "password": "correcthorse"},
    )
    assert blocked.status_code == 429

    # Advance the clock past the window and try again — must succeed.
    fake_now[0] += 61.0
    after = auth_app.post(
        "/api/auth/login",
        json={"username": "owner", "password": "correcthorse"},
    )
    assert after.status_code == 200, after.text


def test_password_endpoint_is_also_rate_limited(auth_app: TestClient) -> None:
    """POST /api/auth/password is throttled the same way as /login.

    Six rapid LAN attempts (each with a bad OTP) must trip the limiter
    so an attacker can't grind the password endpoint either. The 6th
    attempt returns 429 rather than the per-attempt 401.
    """
    auth_app.app.state.auth_rate_limiter.reset()

    for _ in range(5):
        r = auth_app.post(
            "/api/auth/password",
            json={"password": "correcthorse", "otp": "wrong"},
            headers={"X-Test-Client-Ip": "10.0.1.50"},
        )
        assert r.status_code == 401  # OTP rejection, not yet rate-limited

    sixth = auth_app.post(
        "/api/auth/password",
        json={"password": "correcthorse", "otp": "wrong"},
        headers={"X-Test-Client-Ip": "10.0.1.50"},
    )
    assert sixth.status_code == 429, sixth.text
    assert sixth.json()["error"]["code"] == "auth.rate_limited"
    assert sixth.headers.get("retry-after") is not None


def test_rate_limit_is_per_ip(auth_app: TestClient) -> None:
    """A different source IP keeps its own bucket."""
    _set_owner_password(auth_app)
    auth_app.app.state.auth_rate_limiter.reset()

    # Burn one IP's bucket.
    for _ in range(5):
        auth_app.post(
            "/api/auth/login",
            json={"username": "owner", "password": "wrong"},
            headers={"X-Test-Client-Ip": "10.0.1.50"},
        )
    blocked = auth_app.post(
        "/api/auth/login",
        json={"username": "owner", "password": "correcthorse"},
        headers={"X-Test-Client-Ip": "10.0.1.50"},
    )
    assert blocked.status_code == 429

    # A different IP still has a fresh bucket and can log in.
    other = auth_app.post(
        "/api/auth/login",
        json={"username": "owner", "password": "correcthorse"},
        headers={"X-Test-Client-Ip": "10.0.1.99"},
    )
    assert other.status_code == 200, other.text
