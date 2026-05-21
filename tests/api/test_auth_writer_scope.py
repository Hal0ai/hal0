"""Scope x verb matrix tests for the ``require_writer`` gate.

Issue #29: admin routers previously accepted any valid token regardless of
scope. A ``read-only`` or ``v1-only`` token could PUT/POST/DELETE on every
admin route. This module asserts the corrected contract:

  scope       | GET (reader)       | POST/PUT/PATCH/DELETE (writer)
  ------------+--------------------+-------------------------------
  admin       | 200                | 200
  all         | 200                | 200
  read-only   | 200                | 403 auth.forbidden
  v1-only     | 200                | 403 auth.forbidden
  (no creds)  | 401 auth.required  | 401 auth.required

Tests below mint tokens for each scope, hit a representative GET + a
representative mutation on each admin router, and assert on the matrix.
The mutating call payloads are intentionally minimal — we want auth to
gate *before* business validation, so the test never asserts on 2xx
shapes (only on the auth status codes 200 / 401 / 403).

For 200 cases, a passing auth check may still surface a 4xx/5xx from the
handler (e.g. a missing slot). We assert "not 401 and not 403" rather
than "== 200" to keep the matrix focused on the scope gate.
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
    monkeypatch.setenv("HAL0_AUTH_ENABLED", "1")
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    app = create_app()
    with TestClient(app) as c:
        c.app.state.token_store = TokenStore(tmp_path / "tokens.toml")
        yield c


@pytest.fixture
def auth_app_trusted_proxy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """auth_app variant that opts in to trusting X-Forwarded-Email (§26)."""
    monkeypatch.setenv("HAL0_AUTH_ENABLED", "1")
    monkeypatch.setenv("HAL0_TRUST_FORWARDED_EMAIL", "1")
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    app = create_app()
    with TestClient(app) as c:
        c.app.state.token_store = TokenStore(tmp_path / "tokens.toml")
        yield c


def _bearer(auth_app: TestClient, scope: str, label: str | None = None) -> dict[str, str]:
    """Mint a token at ``scope`` and return its Bearer header dict."""
    store: TokenStore = auth_app.app.state.token_store
    _, raw = store.create(label=label or f"test-{scope}", scope=scope)
    return {"Authorization": f"Bearer {raw}"}


# ── representative GETs across the admin routers ─────────────────────────────
# read-only and v1-only must still be able to observe.


READER_ROUTES = [
    "/api/slots",
    "/api/models",
    "/api/hardware",
    "/api/logs",
    "/api/settings",
    "/api/settings/schema",
    "/api/providers",
    "/api/upstreams",
    "/api/updates/check",
    "/api/updates/channel",
]


@pytest.mark.parametrize("path", READER_ROUTES)
@pytest.mark.parametrize("scope", ["admin", "all", "read-only", "v1-only"])
def test_reader_routes_accept_every_scope(auth_app: TestClient, scope: str, path: str) -> None:
    """GETs on admin routers must accept every valid scope.

    We allow any non-401/403 status — the handler may still 5xx on a
    missing upstream or 404 on an unknown id; the gate is what matters.
    """
    response = auth_app.get(path, headers=_bearer(auth_app, scope))
    assert response.status_code not in (401, 403), (
        f"reader route {path} rejected scope={scope!r}: "
        f"status={response.status_code} body={response.text}"
    )


# ── representative writes across the admin routers ───────────────────────────
# Each tuple: (method, path, json_body). Bodies are minimal because we expect
# the auth check to fire *before* business validation when the scope is wrong.


WRITER_ROUTES: list[tuple[str, str, dict | None]] = [
    # slots
    ("POST", "/api/slots", {"name": "test-slot-via-auth"}),
    ("DELETE", "/api/slots/test-slot-via-auth", None),
    ("PUT", "/api/slots/primary/config", {}),
    ("PATCH", "/api/slots/primary/defaults", {}),
    ("POST", "/api/slots/primary/backend", {"backend": "vulkan"}),
    ("POST", "/api/slots/primary/load", None),
    ("POST", "/api/slots/primary/unload", None),
    ("POST", "/api/slots/primary/restart", None),
    ("POST", "/api/slots/primary/swap", {"model_id": "x"}),
    # models
    ("POST", "/api/models", {"id": "test"}),
    ("PUT", "/api/models/test", {}),
    ("DELETE", "/api/models/test", None),
    ("POST", "/api/models/test/pull", None),
    ("POST", "/api/models/test/pull/cancel", None),
    # hardware
    ("POST", "/api/hardware/probe", None),
    # settings
    ("PUT", "/api/settings", {}),
    ("POST", "/api/settings/reload", None),
    # providers (upstreams)
    ("POST", "/api/upstreams/haloai/test", None),
    # updater
    ("POST", "/api/updates/apply", None),
    ("POST", "/api/updates/rollback", None),
    ("PUT", "/api/updates/channel", {"channel": "stable"}),
]


@pytest.mark.parametrize("scope", ["read-only", "v1-only"])
@pytest.mark.parametrize("method,path,body", WRITER_ROUTES)
def test_writer_routes_reject_non_writer_scopes(
    auth_app: TestClient, scope: str, method: str, path: str, body: dict | None
) -> None:
    """Mutating verbs on admin routers must reject read-only / v1-only with 403."""
    response = auth_app.request(method, path, json=body, headers=_bearer(auth_app, scope))
    assert response.status_code == 403, (
        f"{method} {path} did not 403 for scope={scope!r}: "
        f"status={response.status_code} body={response.text}"
    )
    body_json = response.json()
    assert body_json["error"]["code"] == "auth.forbidden", body_json
    # The envelope should name the scope so the caller can debug.
    details = body_json["error"].get("details") or {}
    assert details.get("scope") == scope


@pytest.mark.parametrize("scope", ["admin", "all"])
@pytest.mark.parametrize("method,path,body", WRITER_ROUTES)
def test_writer_routes_accept_writer_scopes(
    auth_app: TestClient, scope: str, method: str, path: str, body: dict | None
) -> None:
    """admin / all scopes must pass the writer gate.

    Handler-level failures (404 on a missing slot, 4xx on bad payload, 5xx
    on a missing background dep) are fine — we only assert that the auth
    layer does NOT reject the request.
    """
    response = auth_app.request(method, path, json=body, headers=_bearer(auth_app, scope))
    assert response.status_code not in (401, 403), (
        f"{method} {path} rejected writer scope={scope!r}: "
        f"status={response.status_code} body={response.text}"
    )


# ── missing credentials still 401 (not 403) on mutating routes ────────────────


@pytest.mark.parametrize("method,path,body", WRITER_ROUTES)
def test_writer_routes_require_auth(
    auth_app: TestClient, method: str, path: str, body: dict | None
) -> None:
    response = auth_app.request(method, path, json=body)
    assert response.status_code == 401, (
        f"{method} {path} did not 401 without credentials: "
        f"status={response.status_code} body={response.text}"
    )
    assert response.json()["error"]["code"] == "auth.required"


# ── X-Forwarded-Email (post-§26 fix: opt-in only) ─────────────────────────────


def test_forwarded_email_writes_blocked_by_default(auth_app: TestClient) -> None:
    """Default install MUST 401 on X-Forwarded-Email writes (§26 regression guard)."""
    response = auth_app.put(
        "/api/settings",
        json={},
        headers={"X-Forwarded-Email": "owner@example.com"},
    )
    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "auth.required"


def test_forwarded_email_can_write(auth_app_trusted_proxy: TestClient) -> None:
    """With HAL0_TRUST_FORWARDED_EMAIL=1, forwarded identity gets writer access."""
    response = auth_app_trusted_proxy.put(
        "/api/settings",
        json={},
        headers={"X-Forwarded-Email": "owner@example.com"},
    )
    assert response.status_code not in (401, 403), response.text


# ── HAL0_AUTH_ENABLED unset: every route is open ──────────────────────────────


def test_writer_routes_open_when_auth_disabled(client: TestClient) -> None:
    """With auth off, anonymous can hit a write route — preserves trusted-LAN posture."""
    response = client.put("/api/settings", json={})
    assert response.status_code not in (401, 403), response.text
