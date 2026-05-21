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
    monkeypatch.setenv("HAL0_AUTH_ENABLED", "1")
    monkeypatch.setenv("HAL0_TRUST_FORWARDED_EMAIL", "1")
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    app = create_app()
    with TestClient(app) as c:
        store = TokenStore(tmp_path / "tokens.toml")
        c.app.state.token_store = store
        yield c


# ── HAL0_AUTH_ENABLED=0 (default) — pass-through ──────────────────────────────


def test_auth_disabled_protected_route_open(client: TestClient) -> None:
    """When HAL0_AUTH_ENABLED is unset, protected routes work without auth."""
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
    ],
)
def test_protected_routes_require_auth(auth_app: TestClient, path: str) -> None:
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
