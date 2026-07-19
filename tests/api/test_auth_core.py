"""Unit tests for hal0.api.auth (KB-1 / §1): principal resolution + posture.

Covers the pieces the exposure-CI + login/status routes lean on:
``resolve_principal`` (cookie -> bearer -> api_key priority),
``require_auth_enabled`` (the dev-open-by-default posture derivation),
``verify_admin_key``, and ``_decide`` (the OPEN/BOOTSTRAP/CLIENT/ADMIN
enforcement table). End-to-end middleware behaviour through a real app is
covered by ``tests/security/test_exposure.py::test_enforcement_wired`` plus
the route-level ``TestClient`` tests at the bottom of this file (login /
status + the dev-open bypass).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from hal0.api import auth as auth_mod
from hal0.api.agents import _auth as agents_auth
from hal0.security.exposure import AuthClass


@pytest.fixture(autouse=True)
def isolate_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Force the HMAC secret + config home onto per-test paths.

    HAL0_HOME isolation keeps ``require_auth_enabled``'s persisted-config
    read (``[security].require_auth``) hermetic — it must never see a
    stray hal0.toml on the dev/CI box. The module-level mtime cache is
    also reset so no prior test's read leaks across.
    """
    secret_path = tmp_path / "secret.bin"
    monkeypatch.setenv("HAL0_AGENT_SECRET_PATH", str(secret_path))
    monkeypatch.setenv("HAL0_HOME", str(tmp_path / "hal0_home"))
    auth_mod._require_auth_cache = None
    yield secret_path


def _scope(
    *,
    headers: list[tuple[bytes, bytes]] | None = None,
    query_string: bytes = b"",
    scope_type: str = "http",
) -> dict[str, object]:
    return {
        "type": scope_type,
        "path": "/api/whatever",
        "headers": headers or [],
        "query_string": query_string,
    }


# ---------------------------------------------------------------------------
# resolve_principal


def test_resolve_principal_anon_by_default() -> None:
    assert auth_mod.resolve_principal(_scope()) == auth_mod.ANON


def test_resolve_principal_bearer_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_ADMIN_KEY", "s3cr3t-admin")
    principal = auth_mod.resolve_principal(
        _scope(headers=[(b"authorization", b"Bearer s3cr3t-admin")])
    )
    assert principal.tier == "admin"
    assert principal.source == "bearer"


def test_resolve_principal_bearer_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_CLIENT_KEY", "cli3nt-key")
    principal = auth_mod.resolve_principal(
        _scope(headers=[(b"authorization", b"Bearer cli3nt-key")])
    )
    assert principal.tier == "client"


def test_resolve_principal_bearer_wrong_key_is_anon(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_ADMIN_KEY", "s3cr3t-admin")
    principal = auth_mod.resolve_principal(
        _scope(headers=[(b"authorization", b"Bearer not-the-key")])
    )
    assert principal == auth_mod.ANON


def test_resolve_principal_api_key_query_param(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_CLIENT_KEY", "cli3nt-key")
    principal = auth_mod.resolve_principal(_scope(query_string=b"api_key=cli3nt-key&x=1"))
    assert principal.tier == "client"
    assert principal.source == "api_key"


def test_resolve_principal_admin_key_wins_over_client_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An admin key presented anywhere resolves to admin, not client."""
    monkeypatch.setenv("HAL0_ADMIN_KEY", "admin-key")
    monkeypatch.setenv("HAL0_CLIENT_KEY", "client-key")
    principal = auth_mod.resolve_principal(_scope(query_string=b"api_key=admin-key"))
    assert principal.tier == "admin"


def test_resolve_principal_cookie_beats_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Priority is cookie -> bearer -> api_key; a valid cookie wins outright."""
    monkeypatch.setenv("HAL0_CLIENT_KEY", "cli3nt-key")
    cookie = agents_auth.mint_session_cookie()
    scope = _scope(
        headers=[
            (b"cookie", f"{agents_auth.SESSION_COOKIE_NAME}={cookie}".encode()),
            (b"authorization", b"Bearer cli3nt-key"),
        ]
    )
    principal = auth_mod.resolve_principal(scope)
    assert principal.tier == "admin"
    assert principal.source == "cookie"


def test_resolve_principal_invalid_cookie_falls_through_to_bearer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HAL0_CLIENT_KEY", "cli3nt-key")
    scope = _scope(
        headers=[
            (b"cookie", f"{agents_auth.SESSION_COOKIE_NAME}=garbage".encode()),
            (b"authorization", b"Bearer cli3nt-key"),
        ]
    )
    principal = auth_mod.resolve_principal(scope)
    assert principal.tier == "client"
    assert principal.source == "bearer"


def test_resolve_principal_from_scope_accepts_wrapper_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``resolve_principal_from_scope`` unwraps a Request/WebSocket-like object."""
    monkeypatch.setenv("HAL0_ADMIN_KEY", "s3cr3t-admin")

    class _FakeRequest:
        scope = _scope(headers=[(b"authorization", b"Bearer s3cr3t-admin")])

    principal = auth_mod.resolve_principal_from_scope(_FakeRequest())
    assert principal.tier == "admin"


# ---------------------------------------------------------------------------
# verify_admin_key / has_admin_key


def test_verify_admin_key_no_key_configured() -> None:
    assert auth_mod.verify_admin_key("anything") is False


def test_verify_admin_key_match(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_ADMIN_KEY", "the-key")
    assert auth_mod.verify_admin_key("the-key") is True
    assert auth_mod.verify_admin_key("wrong") is False
    assert auth_mod.verify_admin_key("") is False


def test_has_admin_key(monkeypatch: pytest.MonkeyPatch) -> None:
    assert auth_mod.has_admin_key() is False
    monkeypatch.setenv("HAL0_ADMIN_KEY", "x")
    assert auth_mod.has_admin_key() is True
    monkeypatch.setenv("HAL0_ADMIN_KEY", "   ")
    assert auth_mod.has_admin_key() is False


# ---------------------------------------------------------------------------
# require_auth_enabled posture


# New posture (operator decision 2026-07-19, finding O19): auth is OFF unless
# explicitly enabled. KB-1's bind-address / key-presence auto-on is retired —
# it locked operators out of a login-less dashboard, so they disabled auth
# wholesale. These tests PIN the inverted default: off-by-default, explicit
# env / persisted-config enable still works, and enforcement once on is
# unchanged (covered by test_exposure::test_enforcement_wired).


def test_require_auth_disabled_by_default_on_loopback_no_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HAL0_REQUIRE_AUTH", raising=False)
    monkeypatch.delenv("HAL0_ADMIN_KEY", raising=False)
    monkeypatch.delenv("HAL0_CLIENT_KEY", raising=False)
    monkeypatch.delenv("HAL0_BIND_HOST", raising=False)
    assert auth_mod.require_auth_enabled() is False


def test_require_auth_key_presence_no_longer_auto_enables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Configuring a key no longer arms enforcement — explicit-enable only."""
    monkeypatch.delenv("HAL0_REQUIRE_AUTH", raising=False)
    monkeypatch.setenv("HAL0_ADMIN_KEY", "x")
    assert auth_mod.require_auth_enabled() is False

    monkeypatch.delenv("HAL0_ADMIN_KEY", raising=False)
    monkeypatch.setenv("HAL0_CLIENT_KEY", "x")
    assert auth_mod.require_auth_enabled() is False


def test_require_auth_bind_host_no_longer_auto_enables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 0.0.0.0 bind no longer arms enforcement (the retired KB-1 auto-on)."""
    monkeypatch.delenv("HAL0_REQUIRE_AUTH", raising=False)
    monkeypatch.delenv("HAL0_ADMIN_KEY", raising=False)
    monkeypatch.delenv("HAL0_CLIENT_KEY", raising=False)
    monkeypatch.setenv("HAL0_BIND_HOST", "0.0.0.0")
    assert auth_mod.require_auth_enabled() is False


def test_require_auth_persisted_config_enables(monkeypatch: pytest.MonkeyPatch) -> None:
    """The persisted [security].require_auth toggle arms enforcement."""
    from hal0.config import paths
    from hal0.config.loader import save_hal0_config
    from hal0.config.schema import Hal0Config

    monkeypatch.delenv("HAL0_REQUIRE_AUTH", raising=False)
    cfg = Hal0Config()
    cfg.security.require_auth = True
    paths.hal0_toml().parent.mkdir(parents=True, exist_ok=True)
    save_hal0_config(cfg)
    auth_mod._require_auth_cache = None
    assert auth_mod.require_auth_enabled() is True

    # And an explicit disable persists as False.
    cfg.security.require_auth = False
    save_hal0_config(cfg)
    auth_mod._require_auth_cache = None
    assert auth_mod.require_auth_enabled() is False


def test_require_auth_env_override_beats_persisted_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hal0.config import paths
    from hal0.config.loader import save_hal0_config
    from hal0.config.schema import Hal0Config

    cfg = Hal0Config()
    cfg.security.require_auth = True
    paths.hal0_toml().parent.mkdir(parents=True, exist_ok=True)
    save_hal0_config(cfg)
    auth_mod._require_auth_cache = None

    # Env OFF beats persisted ON.
    monkeypatch.setenv("HAL0_REQUIRE_AUTH", "0")
    assert auth_mod.require_auth_enabled() is False


def test_require_auth_env_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_ADMIN_KEY", "x")
    monkeypatch.setenv("HAL0_REQUIRE_AUTH", "0")
    assert auth_mod.require_auth_enabled() is False

    monkeypatch.delenv("HAL0_ADMIN_KEY", raising=False)
    monkeypatch.setenv("HAL0_REQUIRE_AUTH", "true")
    assert auth_mod.require_auth_enabled() is True


# ---------------------------------------------------------------------------
# _decide (the OPEN/BOOTSTRAP/CLIENT/ADMIN enforcement table)


def test_decide_open_always_allowed() -> None:
    allowed, _, _ = auth_mod._decide(AuthClass.OPEN, auth_mod.ANON)
    assert allowed is True


def test_decide_bootstrap_open_without_admin_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HAL0_ADMIN_KEY", raising=False)
    allowed, _, _ = auth_mod._decide(AuthClass.BOOTSTRAP, auth_mod.ANON)
    assert allowed is True


def test_decide_bootstrap_becomes_admin_once_keyed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_ADMIN_KEY", "x")
    allowed, status, _ = auth_mod._decide(AuthClass.BOOTSTRAP, auth_mod.ANON)
    assert allowed is False
    assert status == 401

    admin = auth_mod.AuthPrincipal(tier="admin", source="bearer")
    allowed, _, _ = auth_mod._decide(AuthClass.BOOTSTRAP, admin)
    assert allowed is True


def test_decide_client_requires_client_or_admin_tier() -> None:
    anon = auth_mod.ANON
    client = auth_mod.AuthPrincipal(tier="client", source="bearer")
    admin = auth_mod.AuthPrincipal(tier="admin", source="cookie")

    allowed, status, _ = auth_mod._decide(AuthClass.CLIENT, anon)
    assert (allowed, status) == (False, 401)

    allowed, _, _ = auth_mod._decide(AuthClass.CLIENT, client)
    assert allowed is True

    allowed, _, _ = auth_mod._decide(AuthClass.CLIENT, admin)
    assert allowed is True


def test_decide_admin_requires_admin_tier() -> None:
    anon = auth_mod.ANON
    client = auth_mod.AuthPrincipal(tier="client", source="bearer")
    admin = auth_mod.AuthPrincipal(tier="admin", source="cookie")

    allowed, status, _ = auth_mod._decide(AuthClass.ADMIN, anon)
    assert (allowed, status) == (False, 401)

    allowed, status, _ = auth_mod._decide(AuthClass.ADMIN, client)
    assert (allowed, status) == (False, 403)

    allowed, _, _ = auth_mod._decide(AuthClass.ADMIN, admin)
    assert allowed is True


# ---------------------------------------------------------------------------
# Route-level: POST /api/auth/login + GET /api/auth/status, and the
# dev-open bypass end-to-end through a real TestClient app.


@pytest.fixture
def auth_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A fresh app with an isolated secret + HAL0_HOME (own fixture, not the
    project-wide ``client``, so each test controls HAL0_ADMIN_KEY /
    HAL0_REQUIRE_AUTH before the app -- and its middleware's per-request env
    reads -- come into play).
    """
    import os

    from fastapi.testclient import TestClient

    from hal0.api import create_app

    monkeypatch.setenv("HAL0_AGENT_SECRET_PATH", str(tmp_path / "secret.bin"))
    monkeypatch.setenv("HAL0_HOME", str(tmp_path / "hal0_home"))
    os.makedirs(tmp_path / "hal0_home" / "etc" / "hal0", exist_ok=True)

    app = create_app()
    with TestClient(app) as c:
        yield c


def test_status_route_reports_posture(auth_client) -> None:
    resp = auth_client.get("/api/auth/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"auth_required": False, "has_admin_key": False, "tier": "anon"}


def test_status_route_never_leaks_the_key(auth_client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_ADMIN_KEY", "super-secret-value")
    resp = auth_client.get("/api/auth/status")
    assert resp.status_code == 200
    assert "super-secret-value" not in resp.text
    assert resp.json()["has_admin_key"] is True


def test_login_rejects_when_no_admin_key_configured(auth_client) -> None:
    resp = auth_client.post("/api/auth/login", json={"key": "anything"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "auth.invalid_key"


def test_login_rejects_wrong_key(auth_client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_ADMIN_KEY", "the-real-key")
    resp = auth_client.post("/api/auth/login", json={"key": "wrong"})
    assert resp.status_code == 401


def test_login_success_sets_session_cookie(auth_client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_ADMIN_KEY", "the-real-key")
    resp = auth_client.post("/api/auth/login", json={"key": "the-real-key"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "tier": "admin"}
    assert agents_auth.SESSION_COOKIE_NAME in resp.cookies


def test_logout_clears_session_cookie(auth_client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_ADMIN_KEY", "the-real-key")
    auth_client.post("/api/auth/login", json={"key": "the-real-key"})
    resp = auth_client.post("/api/auth/logout")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    # The Set-Cookie header expires the session cookie (max-age 0 / past date).
    set_cookie = resp.headers.get("set-cookie", "")
    assert agents_auth.SESSION_COOKIE_NAME in set_cookie


def test_require_toggle_persists_and_applies_live(
    auth_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PUT /api/auth/require flips the persisted toggle; the gate reads it live."""
    monkeypatch.setenv("HAL0_ADMIN_KEY", "the-real-key")
    monkeypatch.delenv("HAL0_REQUIRE_AUTH", raising=False)
    auth_mod._require_auth_cache = None

    # Auth off by default → an ADMIN route is reachable with no creds.
    assert auth_client.get("/api/settings").status_code not in (401, 403)

    # Enable auth (rides through unauthenticated because enforcement is still off).
    resp = auth_client.put("/api/auth/require", json={"require_auth": True})
    assert resp.status_code == 200
    assert resp.json() == {"require_auth": True, "applies_live": True}
    auth_mod._require_auth_cache = None

    # Now the same ADMIN route denies with no creds — applied live, no restart.
    assert auth_client.get("/api/settings").status_code in (401, 403)


def test_require_toggle_refuses_enable_without_admin_key(auth_client) -> None:
    """Enabling auth with no admin key would lock everyone out → 400."""
    resp = auth_client.put("/api/auth/require", json={"require_auth": True})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "auth.no_admin_key"


def test_dev_open_bypass_reaches_admin_route_with_no_creds(auth_client) -> None:
    """Loopback + no keys configured: the pre-existing suite's world.

    An ADMIN-classified route (``GET /api/settings``) must still be
    reachable with zero credentials -- this is the whole point of the
    dev-open posture default, and what keeps the other ~700 existing
    tests green without modification.
    """
    resp = auth_client.get("/api/settings")
    assert resp.status_code not in (401, 403), resp.text
