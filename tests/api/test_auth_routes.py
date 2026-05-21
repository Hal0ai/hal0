"""Token CRUD route tests.

POST /api/auth/tokens, GET /api/auth/tokens, DELETE /api/auth/tokens/{id}.
All admin-protected — non-admin tokens (scope='all', 'v1-only') get 403.
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
    monkeypatch.delenv("HAL0_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("HAL0_AUTH_ENABLED", "1")
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    app = create_app()
    with TestClient(app) as c:
        c.app.state.token_store = TokenStore(tmp_path / "tokens.toml")
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
