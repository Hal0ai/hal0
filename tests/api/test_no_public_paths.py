"""ADR-0001 Child B — PUBLIC_PATHS deletion regression tests.

Two invariants:

  1. The ``PUBLIC_PATHS`` symbol does NOT exist on
     ``hal0.api.middleware.auth``. The frozenset's deletion is the
     architectural point of the ADR; if it comes back, the dual-source
     drift class of bugs (#28, #51) comes back with it.

  2. Routes that USED to be allowlisted in ``PUBLIC_PATHS`` are still
     reachable without credentials when ``HAL0_AUTH_ENABLED=1`` — but
     publicness is now declared by NOT attaching an auth dep, not by
     consulting a frozenset. The test asserts behaviour: hit each one
     and assert the response is not a 401.

  3. A writer endpoint (``POST /api/slots/``) still 401s without auth,
     proving the auth gate is intact on the protected surface.

Pattern mirrors tests/api/test_auth_middleware.py: build a fresh app
per test with HAL0_AUTH_ENABLED=1 + HAL0_HOME pointed at a tmp dir.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.api.middleware import auth as auth_middleware


# ── Invariant 1: the symbol is gone ─────────────────────────────────────────


def test_public_paths_symbol_no_longer_exists() -> None:
    """``hal0.api.middleware.auth`` must not export a PUBLIC_PATHS attribute.

    Deletion is the architectural point of ADR-0001 Child B. A
    re-introduction would re-open the dual-source-of-truth drift
    class of bugs (#28 / #36 / #51).
    """
    assert not hasattr(auth_middleware, "PUBLIC_PATHS"), (
        "PUBLIC_PATHS reappeared on hal0.api.middleware.auth — ADR-0001 Child B "
        "explicitly deletes it; route publicness is now declared by NOT attaching "
        "an auth dependency, not by allowlisting paths."
    )


def test_require_token_unless_public_no_longer_exists() -> None:
    """The path-aware dependency went away with the allowlist that backed it."""
    assert not hasattr(auth_middleware, "require_token_unless_public"), (
        "require_token_unless_public reappeared — Child B removed it along with "
        "PUBLIC_PATHS. Use plain require_token at include_router(...) time and "
        "mount public endpoints on a separate auth-free router."
    )


# ── Invariant 2: previously-public routes still public ──────────────────────


@pytest.fixture
def auth_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Fresh app with HAL0_AUTH_ENABLED=1; token store rooted at tmp_path."""
    monkeypatch.setenv("HAL0_AUTH_ENABLED", "1")
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    app = create_app()
    with TestClient(app) as c:
        yield c


# Every path that used to live in the deleted PUBLIC_PATHS frozenset.
# The wizard, monitoring tools, and OpenAI clients all depend on these
# being reachable without credentials when no password is yet set.
_FORMERLY_PUBLIC_PATHS = [
    # Liveness / monitoring.
    "/api/health/system",
    "/api/status",
    "/api/metrics",
    "/api/features",
    # First-run wizard gating.
    "/api/install/state",
    # Host-aware URL hints (wizard uses to render the OpenWebUI link).
    "/api/config/urls",
    # Auth surface itself.
    "/api/auth/status",
    "/api/auth/login",  # GET legacy hint shim
    # OpenAI compat — clients probe before sending Authorization.
    "/v1/models",
]


@pytest.mark.parametrize("path", _FORMERLY_PUBLIC_PATHS)
def test_formerly_public_paths_stay_open(auth_app: TestClient, path: str) -> None:
    """Endpoints that were in PUBLIC_PATHS must remain reachable post-deletion.

    A 401 here means the route was implicitly protected after the
    PUBLIC_PATHS removal — that breaks the first-run wizard's
    bootstrap, monitoring scrapers, and OpenAI SDKs that pre-flight
    /v1/models before authenticating.
    """
    response = auth_app.get(path)
    assert response.status_code != 401, (
        f"route {path} 401'd under HAL0_AUTH_ENABLED=1 — it must remain "
        f"reachable without credentials (was in the now-deleted "
        f"PUBLIC_PATHS frozenset). Body: {response.text}"
    )


def test_install_complete_post_stays_open(auth_app: TestClient) -> None:
    """The wizard's sentinel-write call is a POST — auth-free."""
    response = auth_app.post("/api/install/complete")
    assert response.status_code != 401, response.text


def test_auth_logout_post_stays_open(auth_app: TestClient) -> None:
    """POST /api/auth/logout is the cookie-clear path; must be auth-free.

    A logged-out user shouldn't need to authenticate to log out, and the
    cookie-clear is idempotent — calling it on an already-empty session
    is a 204 no-op.
    """
    response = auth_app.post("/api/auth/logout")
    assert response.status_code != 401, response.text


# ── Invariant 3: writer routes still gated ──────────────────────────────────


def test_writer_route_still_requires_auth(auth_app: TestClient) -> None:
    """POST /api/slots/ (writer-scope) must still 401 without credentials.

    Proves the auth gate is intact on the protected surface — Child B
    moved publicness into the FastAPI dependency graph, but admin
    routers still carry ``Depends(require_token)`` at include_router
    time. A 200 here would mean Child B accidentally opened the writer
    surface as collateral damage.
    """
    response = auth_app.post(
        "/api/slots",
        json={"name": "scratch", "port": 8099, "backend": "vulkan", "provider": "llama-server"},
    )
    assert response.status_code == 401, (
        f"writer route POST /api/slots returned {response.status_code} without "
        f"credentials — auth gate broken. Body: {response.text}"
    )
    body = response.json()
    assert body["error"]["code"] == "auth.required"


def test_admin_reader_route_still_requires_auth(auth_app: TestClient) -> None:
    """GET /api/slots (reader on an admin router) must also still 401."""
    response = auth_app.get("/api/slots")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth.required"
