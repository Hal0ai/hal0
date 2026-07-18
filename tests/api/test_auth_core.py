"""Unit tests for hal0.api.auth (KB-1 / §1): principal resolution + posture.

Covers ``resolve_principal`` (cookie -> bearer -> api_key priority),
``require_auth_enabled`` (the dev-open-by-default posture derivation),
``verify_admin_key``, and ``_decide`` (the OPEN/BOOTSTRAP/CLIENT/ADMIN
enforcement table) -- all pure functions, no app/middleware wiring needed
yet (that lands in the next commit, along with the
``POST /api/auth/login`` / ``GET /api/auth/status`` route-level tests and
``tests/security/test_exposure.py::test_enforcement_wired``).
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
    """Force the HMAC secret onto a per-test path (mirrors test_chat_proxy_auth)."""
    secret_path = tmp_path / "secret.bin"
    monkeypatch.setenv("HAL0_AGENT_SECRET_PATH", str(secret_path))
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


def test_require_auth_disabled_by_default_on_loopback_no_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HAL0_REQUIRE_AUTH", raising=False)
    monkeypatch.delenv("HAL0_ADMIN_KEY", raising=False)
    monkeypatch.delenv("HAL0_CLIENT_KEY", raising=False)
    monkeypatch.delenv("HAL0_BIND_HOST", raising=False)
    assert auth_mod.require_auth_enabled() is False


def test_require_auth_enabled_when_admin_key_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HAL0_REQUIRE_AUTH", raising=False)
    monkeypatch.setenv("HAL0_ADMIN_KEY", "x")
    assert auth_mod.require_auth_enabled() is True


def test_require_auth_enabled_when_client_key_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HAL0_REQUIRE_AUTH", raising=False)
    monkeypatch.setenv("HAL0_CLIENT_KEY", "x")
    assert auth_mod.require_auth_enabled() is True


def test_require_auth_enabled_when_bind_host_non_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HAL0_REQUIRE_AUTH", raising=False)
    monkeypatch.delenv("HAL0_ADMIN_KEY", raising=False)
    monkeypatch.delenv("HAL0_CLIENT_KEY", raising=False)
    monkeypatch.setenv("HAL0_BIND_HOST", "0.0.0.0")
    assert auth_mod.require_auth_enabled() is True


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
