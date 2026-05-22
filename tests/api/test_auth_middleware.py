"""Auth middleware (require_token) tests.

Validates the precedence rules and that routes mounted without an auth
dependency stay public under ``HAL0_AUTH_ENABLED=1``. Per ADR-0001
Child B, publicness is declared by NOT attaching an auth dep at
``include_router(...)`` time — not by being on a magic allowlist.

Pattern: each test rebuilds the FastAPI app with HAL0_AUTH_ENABLED=1 set
before create_app() runs, then swaps the auto-created TokenStore on
``app.state`` with one rooted at a tmp_path so the global tokens.toml
isn't touched. Because the conftest ``client`` fixture is per-function,
tests can mint tokens against the swapped store and assert on them.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.auth.tokens import TokenStore


@pytest.fixture
def auth_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Build an app with auth enabled and an isolated token store.

    Yields a TestClient whose underlying app exposes the swapped store at
    ``app.state.token_store`` so tests can mint tokens through it.
    """
    # The autouse conftest fixture sets HAL0_AUTH_DISABLED=1 — undo here so
    # the explicit HAL0_AUTH_ENABLED=1 takes effect for this app instance.
    monkeypatch.delenv("HAL0_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("HAL0_AUTH_ENABLED", "1")
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    app = create_app()
    with TestClient(app) as c:
        # Swap the lifespan-installed store with one we control. The app
        # state-keyed singleton in get_or_create_store() picks this up
        # transparently on the next dependency resolution.
        store = TokenStore(tmp_path / "tokens.toml")
        c.app.state.token_store = store
        yield c


@pytest.fixture
def auth_app_trusted_proxy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """auth_app variant that opts in to trusting X-Forwarded-Email.

    The default install REJECTS the header (post-§26 fix); operators behind
    a trusted edge proxy that validates the email themselves set
    ``HAL0_TRUST_FORWARDED_EMAIL=1`` to re-enable that path.
    """
    # autouse conftest fixture sets HAL0_AUTH_DISABLED=1 — undo so the
    # explicit HAL0_AUTH_ENABLED=1 takes effect.
    monkeypatch.delenv("HAL0_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("HAL0_AUTH_ENABLED", "1")
    monkeypatch.setenv("HAL0_TRUST_FORWARDED_EMAIL", "1")
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    app = create_app()
    with TestClient(app) as c:
        store = TokenStore(tmp_path / "tokens.toml")
        c.app.state.token_store = store
        yield c


# ── HAL0_AUTH_DISABLED=1 (test default) — pass-through ────────────────────────


def test_auth_disabled_protected_route_open(client: TestClient) -> None:
    """When HAL0_AUTH_DISABLED is set, protected routes work without auth.

    The conftest autouse fixture sets ``HAL0_AUTH_DISABLED=1`` for every
    test by default (so the pre-v1 test suite keeps passing under the
    v1.0 auth-on-by-default flip). This test asserts that override
    still bypasses the gate the same way unsetting HAL0_AUTH_ENABLED did
    pre-v1.
    """
    # /api/slots is normally admin-protected when auth is on.
    response = client.get("/api/slots")
    assert response.status_code == 200, response.text


# ── HAL0_AUTH_ENABLED=1 — public routes still open ────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/api/status",
        "/api/health/system",
        "/api/metrics",
        "/api/features",
        # Note: ``/api/install/*`` is no longer public — the entire
        # installer router is gated by ``require_token`` per FINDINGS §29.
        # On a fresh install with ``HAL0_AUTH_ENABLED`` unset the gate is
        # a pass-through (covered by tests/api/test_install_routes.py);
        # once auth is on, the wizard rides the operator's session
        # cookie like any other admin surface.
        "/api/config/urls",
        "/api/auth/status",
        "/api/auth/login",
        "/v1/models",
    ],
)
def test_public_routes_bypass_auth(auth_app: TestClient, path: str) -> None:
    response = auth_app.get(path)
    # All public routes return 2xx (or 4xx for content reasons), never 401.
    assert response.status_code != 401, (
        f"public route {path} returned 401 with body {response.text}"
    )


# ── HAL0_AUTH_ENABLED=1 — protected routes require credentials ────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/api/slots",
        "/api/models",
        "/api/hardware",
        "/api/logs",
        "/api/settings",
        "/api/providers",
        "/api/capabilities",
    ],
)
def test_protected_routes_require_auth(auth_app: TestClient, path: str) -> None:
    # Drop the first-run lockfile so the claim window is closed for this
    # assertion. /api/hardware and /api/capabilities are deliberately
    # admitted while .first-run.lock + no-password — see
    # _FIRST_RUN_CLAIM_PATHS in src/hal0/api/middleware/auth.py — so
    # without this the wizard-claim layer would mask the writer gate
    # and the test would 200 instead of 401. The protected-route
    # contract this test pins is "auth required once the wizard has
    # finished", which matches the install-complete posture.
    from hal0.api.auth import first_run as first_run_lock

    first_run_lock.consume_lockfile()

    response = auth_app.get(path)
    assert response.status_code == 401, (
        f"protected route {path} did not 401: status={response.status_code} body={response.text}"
    )
    body = response.json()
    assert body["error"]["code"] == "auth.required"


def test_v1_chat_completions_requires_auth(auth_app: TestClient) -> None:
    response = auth_app.post(
        "/v1/chat/completions",
        json={"model": "primary", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "auth.required"


# ── Bearer-token happy path ──────────────────────────────────────────────────


def test_valid_bearer_token_allows_access(auth_app: TestClient) -> None:
    store: TokenStore = auth_app.app.state.token_store
    _, raw = store.create(label="test-bridge", scope="all")
    response = auth_app.get(
        "/api/slots",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200, response.text


def test_invalid_bearer_token_returns_envelope(auth_app: TestClient) -> None:
    response = auth_app.get(
        "/api/slots",
        headers={"Authorization": "Bearer hal0_deadbeef.notreal"},
    )
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "auth.invalid"


def test_malformed_bearer_falls_through_to_required(auth_app: TestClient) -> None:
    """An Authorization header without 'Bearer ' prefix → 401 auth.required.

    The middleware treats malformed headers as "no Bearer presented" so the
    X-Forwarded-Email path can still fire. With neither, it's auth.required.
    """
    response = auth_app.get(
        "/api/slots",
        headers={"Authorization": "Basic some-other-scheme"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth.required"


# ── X-Forwarded-Email path (post-§26 fix: opt-in only) ────────────────────────


def test_forwarded_email_ignored_by_default(auth_app: TestClient) -> None:
    """Default install (HAL0_TRUST_FORWARDED_EMAIL unset) MUST ignore the header.

    Regression guard for harness finding #26 — the bypass was that any LAN
    peer could send X-Forwarded-Email and be granted admin scope.
    """
    response = auth_app.get(
        "/api/slots",
        headers={"X-Forwarded-Email": "alex@example.com"},
    )
    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "auth.required"


def test_forwarded_email_grants_admin_access(auth_app_trusted_proxy: TestClient) -> None:
    """When HAL0_TRUST_FORWARDED_EMAIL=1, the edge-validated email is honoured."""
    response = auth_app_trusted_proxy.get(
        "/api/slots",
        headers={"X-Forwarded-Email": "alex@example.com"},
    )
    assert response.status_code == 200, response.text


def test_forwarded_email_grants_admin_scope(auth_app_trusted_proxy: TestClient) -> None:
    """With trust env var set, /api/auth/me reports scope=admin for the forwarded user."""
    response = auth_app_trusted_proxy.get(
        "/api/auth/me",
        headers={"X-Forwarded-Email": "alex@example.com"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["identity"] == "alex@example.com"
    assert body["scope"] == "admin"
    assert body["source"] == "forwarded"


def test_bearer_takes_precedence_over_forwarded(
    auth_app_trusted_proxy: TestClient,
) -> None:
    """Even when forwarded-email is trusted, a valid Bearer wins; invalid 401s."""
    store: TokenStore = auth_app_trusted_proxy.app.state.token_store
    _, raw = store.create(label="bridge", scope="v1-only")
    response = auth_app_trusted_proxy.get(
        "/api/auth/me",
        headers={
            "Authorization": f"Bearer {raw}",
            "X-Forwarded-Email": "alex@example.com",
        },
    )
    assert response.status_code == 200
    body = response.json()
    # The token's label + scope wins, NOT the forwarded email.
    assert body["identity"] == "bridge"
    assert body["scope"] == "v1-only"
    assert body["source"] == "token"


def test_invalid_bearer_blocks_even_with_forwarded(
    auth_app_trusted_proxy: TestClient,
) -> None:
    """Even when forwarded-email is trusted, a bad Bearer + good forwarded still 401s."""
    response = auth_app_trusted_proxy.get(
        "/api/slots",
        headers={
            "Authorization": "Bearer hal0_deadbeef.bad",
            "X-Forwarded-Email": "alex@example.com",
        },
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth.invalid"


# ── Revocation takes effect immediately ──────────────────────────────────────


def test_revoked_token_returns_401(auth_app: TestClient) -> None:
    store: TokenStore = auth_app.app.state.token_store
    tok, raw = store.create(label="bridge", scope="all")
    # First call: works.
    assert auth_app.get("/api/slots", headers={"Authorization": f"Bearer {raw}"}).status_code == 200
    # Revoke.
    store.revoke(tok.id)
    # Second call: 401 invalid.
    response = auth_app.get("/api/slots", headers={"Authorization": f"Bearer {raw}"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth.invalid"


# ── auth.me endpoint for token paths ─────────────────────────────────────────


def test_me_returns_token_label_and_scope(auth_app: TestClient) -> None:
    store: TokenStore = auth_app.app.state.token_store
    _, raw = store.create(label="my-bridge", scope="v1-only")
    response = auth_app.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["identity"] == "my-bridge"
    assert body["scope"] == "v1-only"
    assert body["source"] == "token"


# ── Status endpoint reports the env state ────────────────────────────────────


def test_auth_status_reports_enabled(auth_app: TestClient) -> None:
    response = auth_app.get("/api/auth/status")
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert "bearer" in body["modes"]
    assert "forwarded-email" in body["modes"]


def test_auth_status_reports_disabled(client: TestClient) -> None:
    response = client.get("/api/auth/status")
    assert response.status_code == 200
    assert response.json()["enabled"] is False


# ── §36 — auth on by default + first-run claim window ────────────────────────


def test_auth_enabled_default_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Security review §36: ``auth_enabled()`` defaults to True.

    Pre-v1 the default was False — anyone running ``curl install | bash``
    on a multi-tenant LAN got a wide-open dashboard. v1 flips the
    default so the wizard owns the credential-capture step before any
    state-changing API call succeeds.
    """
    from hal0.auth.tokens import auth_enabled

    monkeypatch.delenv("HAL0_AUTH_DISABLED", raising=False)
    monkeypatch.delenv("HAL0_AUTH_ENABLED", raising=False)
    assert auth_enabled() is True


def test_hal0_auth_disabled_takes_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    """The opt-out env var beats an explicit enable.

    Useful for CI fixtures that pass through host env vars: setting
    HAL0_AUTH_DISABLED=1 in the test runner short-circuits every gate
    even if a downstream tool also sets HAL0_AUTH_ENABLED=1.
    """
    from hal0.auth.tokens import auth_enabled

    monkeypatch.setenv("HAL0_AUTH_ENABLED", "1")
    monkeypatch.setenv("HAL0_AUTH_DISABLED", "1")
    assert auth_enabled() is False


@pytest.mark.parametrize("val", ["0", "false", "no", "off", ""])
def test_hal0_auth_enabled_explicit_falsy_disables(
    monkeypatch: pytest.MonkeyPatch, val: str
) -> None:
    """Explicit falsy ``HAL0_AUTH_ENABLED`` still turns auth off.

    Operators who scripted ``HAL0_AUTH_ENABLED=0`` into their unit
    file before the v1 flip should still see the same behaviour after
    upgrading — surprising them with a locked dashboard on next restart
    would be worse than the breakage from the original posture.
    """
    from hal0.auth.tokens import auth_enabled

    monkeypatch.delenv("HAL0_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("HAL0_AUTH_ENABLED", val)
    assert auth_enabled() is False


def test_first_run_claim_lockfile_unlocks_install_routes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lockfile + no-password lets anonymous hit the wizard claim paths.

    Models the production first-run window: the installer drops
    ``$VAR_LIB/.first-run.lock``, no owner password is set yet, and the
    wizard needs to reach POST /api/install/probe and POST
    /api/auth/password without yet holding a credential. The middleware
    short-circuits to an anonymous identity on those paths and leaves
    every other admin route locked.

    Today the install routes are mounted without an auth dep so this
    test exercises the helper-side state more than the wiring; once
    §28/§29 land and attach require_writer to /api/install/*, the
    helper becomes load-bearing.
    """
    from hal0.api.middleware.auth import _first_run_claim_active

    monkeypatch.delenv("HAL0_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("HAL0_AUTH_ENABLED", "1")
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))

    # Plant the lockfile where paths.first_run_lock() looks for it.
    from hal0.config import paths

    lock = paths.first_run_lock()
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("otp=deadbeefcafebabe1234567890abcdef\n", encoding="utf-8")
    lock.chmod(0o600)

    app = create_app()
    with TestClient(app):
        # Sanity: lockfile present + password unset → claim helper True
        # for the install paths only. Use a request scope to drive
        # _first_run_claim_active. (We don't actually fire HTTP requests
        # here — the TestClient is just running lifespan so app.state
        # is fully initialised when the helper resolves the token store.)
        from starlette.requests import Request

        async def receive() -> dict:
            return {"type": "http.request"}

        for path in ("/api/install/probe", "/api/auth/password"):
            scope = {
                "type": "http",
                "method": "POST",
                "path": path,
                "headers": [],
                "query_string": b"",
                "app": app,
            }
            req = Request(scope, receive)
            assert _first_run_claim_active(req) is True, (
                f"first-run claim should unlock {path} when lockfile present + no password"
            )

        # An admin route is NOT in the claim path-list and stays locked.
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/slots",
            "headers": [],
            "query_string": b"",
            "app": app,
        }
        req = Request(scope, receive)
        assert _first_run_claim_active(req) is False, (
            "admin routes must not be unlocked by the first-run claim window"
        )

        # Without the lockfile, even the wizard paths stay locked — the
        # installer's lockfile is the trust anchor for the claim.
        lock.unlink()
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/auth/password",
            "headers": [],
            "query_string": b"",
            "app": app,
        }
        req = Request(scope, receive)
        assert _first_run_claim_active(req) is False, (
            "claim window must close when the lockfile is absent"
        )


def test_locked_admin_route_returns_401_with_auth_default_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With HAL0_AUTH_ENABLED unset (new default), /api/slots 401s.

    This is the headline test for §36: a freshly-built app with no env
    overrides at all enforces auth on every admin route. Pre-v1 this
    test would have returned 200 (the route was wide open).
    """
    monkeypatch.delenv("HAL0_AUTH_DISABLED", raising=False)
    monkeypatch.delenv("HAL0_AUTH_ENABLED", raising=False)
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))

    app = create_app()
    with TestClient(app) as c:
        response = c.get("/api/slots")
        assert response.status_code == 401, (
            f"v1.0 default install must lock /api/slots without creds; got {response.status_code}"
        )
        assert response.json()["error"]["code"] == "auth.required"


def test_install_routes_remain_reachable_under_default_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wizard's read-state endpoint stays public under the default lock.

    The first-run wizard renders before any credential exists, so
    ``GET /api/install/state`` must be reachable without auth even
    with the v1.0 default-on flip. This is the practical lower bound
    on "is the wizard still usable" — if this 401s, fresh installs
    are bricked.
    """
    monkeypatch.delenv("HAL0_AUTH_DISABLED", raising=False)
    monkeypatch.delenv("HAL0_AUTH_ENABLED", raising=False)
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))

    app = create_app()
    with TestClient(app) as c:
        response = c.get("/api/install/state")
        assert response.status_code == 200, (
            f"first-run wizard must reach /api/install/state under default lock; "
            f"got {response.status_code}: {response.text}"
        )

        # POST /api/auth/password is reachable on first run too — but
        # per §28 it requires either an OTP from the .first-run.lock OR
        # a 127.0.0.1 loopback call. TestClient appears as a non-loopback
        # host, so a call without the OTP 401s auth.first_run_otp_required.
        # That's the documented contract, not a regression — the wizard
        # reads the OTP from the lockfile (printed by install.sh) and
        # includes it in the body.
        response = c.post(
            "/api/auth/password",
            json={"password": "hunter2hunter2"},
        )
        # 401 (no OTP), 400 (validation), or 200 (loopback bypass) are all
        # acceptable — what matters is the route exists and is reachable.
        assert response.status_code in (200, 400, 401), (
            f"set-password must be reachable on first-run; got {response.text}"
        )
        if response.status_code == 401:
            assert response.json()["error"]["code"] in (
                "auth.first_run_otp_required",
                "auth.first_run_otp_invalid",
            ), response.text
