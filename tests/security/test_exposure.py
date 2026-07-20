"""§21.11 exposure-CI ratchet (KB-1 / §1, seam S9).

Walks the *real* mounted route table of ``create_app()`` and checks two
invariants against ``hal0.security.exposure``:

1. Every currently-mounted route resolves via an EXPLICIT rule — none of
   them silently falls through to the deny-by-default ADMIN fallback.
   (A brand-new, not-yet-classified router *should* trip this the moment
   it's mounted; that's the ratchet doing its job. Classify it in
   ``exposure.py`` before merging.)
2. The set of OPEN-classified routes is *exactly*
   ``exposure.OPEN_ALLOWLIST`` — no more, no less. This is what makes
   "OPEN" mean something: widening it requires a conscious diff to both
   the rule table and this allowlist.

No auth *behavior* is asserted in the first two tests below — the
enforcement middleware itself is covered by ``test_enforcement_wired``
plus ``tests/api/test_auth_core.py``.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from starlette.routing import BaseRoute, Mount

from hal0.api import create_app
from hal0.security.exposure import OPEN_ALLOWLIST, AuthClass, classify, match_rule


def _iter_effective(route: BaseRoute) -> Iterator[BaseRoute]:
    """Recursively resolve FastAPI's lazy included-router wrappers.

    FastAPI >=0.139 keeps ``include_router``'d routes behind a lazy
    ``_IncludedRouter`` wrapper (perf optimisation) instead of eagerly
    flattening them onto ``app.routes``. We duck-type on
    ``effective_candidates`` rather than importing the private class, so
    this keeps working whether or not a given installed FastAPI still
    does the lazy-wrap thing (older/future versions that flatten eagerly
    simply never have this attribute, and we yield the route as-is).
    """
    candidates = getattr(route, "effective_candidates", None)
    if callable(candidates):
        for child in candidates():
            yield from _iter_effective(child)
    else:
        yield route


def _resolve_path(route: BaseRoute) -> str | None:
    """Return the effective path template for ``route``.

    FastAPI's ``_EffectiveRouteContext`` (the lazy include-router
    wrapper's leaf) is a dataclass whose own ``path`` field is only
    populated by the ``from_api_route`` constructor (the common case --
    a regular ``@router.get/post/...`` route). Plain ``Route``,
    ``APIWebSocketRoute``, ``Mount``, and ``Host`` wrap a *separate*
    ``starlette_route`` object instead and leave the dataclass's own
    ``path`` field at its default ``""`` -- so for those we need to read
    ``starlette_route.path`` instead, or every websocket/mount route
    enumerates with an empty path.
    """
    path = getattr(route, "path", None)
    if path:
        return path
    starlette_route = getattr(route, "starlette_route", None)
    if starlette_route is not None:
        sr_path = getattr(starlette_route, "path", None)
        if sr_path:
            return sr_path
    return path


def _enumerate_routes(app: FastAPI) -> set[tuple[str, str]]:
    """Return every concrete ``(method, path)`` pair the app actually serves.

    ``path`` is the FastAPI *template* (e.g.
    ``/v1/models/{model_id:path}``) -- a route's stable identity
    regardless of which concrete id a caller substitutes. HEAD is folded
    into GET (FastAPI auto-adds HEAD for every GET route; it carries
    identical auth semantics). Routes with no HTTP ``methods`` (websocket
    endpoints) are represented with the pseudo method ``"WEBSOCKET"``;
    ``Mount`` sub-apps (the raw MCP JSON-RPC servers) are represented as a
    single representative ``GET`` entry.
    """
    entries: set[tuple[str, str]] = set()
    for top in app.router.routes:
        for route in _iter_effective(top):
            path = _resolve_path(route)
            if not path:
                continue
            if isinstance(route, Mount):
                entries.add(("GET", path))
                continue
            methods = getattr(route, "methods", None)
            if methods:
                for m in methods:
                    if m == "HEAD":
                        continue
                    entries.add((m, path))
            else:
                entries.add(("WEBSOCKET", path))
    return entries


def _classify_method(method: str) -> str:
    """Websocket handshakes are HTTP GETs at the transport level."""
    return "GET" if method == "WEBSOCKET" else method


@pytest.fixture(scope="module")
def app_routes() -> set[tuple[str, str]]:
    app = create_app()
    routes = _enumerate_routes(app)
    assert len(routes) > 100, (
        f"only found {len(routes)} routes -- the route walker probably isn't "
        "resolving FastAPI's lazy _IncludedRouter wrappers correctly; see "
        "_iter_effective's docstring"
    )
    return routes


def test_every_mounted_route_is_explicitly_classified(
    app_routes: set[tuple[str, str]],
) -> None:
    """No currently-mounted route may resolve via the ADMIN fallback.

    This is the ratchet: a brand-new router that nobody has classified
    yet has NO rule matching it, so ``match_rule`` returns ``None`` here
    and this test fails, forcing the author to add an explicit rule to
    ``security/exposure.py`` (even if the answer ends up being ADMIN --
    it must be an *explicit* ADMIN rule, not silence).
    """
    unclassified = sorted(
        (method, path)
        for method, path in app_routes
        if match_rule(_classify_method(method), path) is None
    )
    assert unclassified == [], (
        "route(s) fall through to the deny-by-default ADMIN fallback "
        "without an explicit rule in hal0.security.exposure.RULES -- "
        f"classify them: {unclassified}"
    )


def test_open_allowlist_is_exact(app_routes: set[tuple[str, str]]) -> None:
    """The OPEN set is exactly ``OPEN_ALLOWLIST`` -- neither wider nor narrower.

    Widening OPEN (a route that used to require auth no longer does) is
    exactly the class of regression this ratchet exists to catch.
    Narrowing is also asserted so the allowlist constant doesn't silently
    drift out of sync with the rule table.
    """
    actual_open = {
        (method, path)
        for method, path in app_routes
        if classify(_classify_method(method), path) is AuthClass.OPEN
    }
    assert actual_open == OPEN_ALLOWLIST, (
        f"OPEN set drifted from the expected allowlist.\n"
        f"Newly OPEN (not expected): {sorted(actual_open - OPEN_ALLOWLIST)}\n"
        f"Missing (expected but not OPEN): {sorted(OPEN_ALLOWLIST - actual_open)}"
    )


def test_bootstrap_class_covers_installer(app_routes: set[tuple[str, str]]) -> None:
    """Every ``/api/install/*`` route classifies as BOOTSTRAP, nothing else does."""
    bootstrap_routes = {
        (method, path)
        for method, path in app_routes
        if classify(_classify_method(method), path) is AuthClass.BOOTSTRAP
    }
    assert bootstrap_routes, "expected at least one BOOTSTRAP-classified route"
    assert all(path.startswith("/api/install") for _, path in bootstrap_routes)
    installer_routes = {(m, p) for m, p in app_routes if p.startswith("/api/install")}
    assert bootstrap_routes == installer_routes


def test_representative_admin_routes_are_admin() -> None:
    """Spot-check a handful of unambiguously RCE-class routes stay ADMIN."""
    admin_examples = [
        ("POST", "/api/slots"),
        ("DELETE", "/api/slots/{name}"),
        ("POST", "/api/models/{model_id}/pull"),
        ("POST", "/api/models/{model_id}/duplicate"),
        ("POST", "/api/updates/apply"),
        ("POST", "/api/board/chat"),
        ("PUT", "/api/settings"),
        ("GET", "/api/settings"),
        ("POST", "/api/secrets/{name}"),
        ("GET", "/api/secrets"),
        ("POST", "/api/auth/rotate"),
    ]
    for method, path in admin_examples:
        assert classify(method, path) is AuthClass.ADMIN, f"{method} {path} should be ADMIN"


def test_representative_client_routes_are_client() -> None:
    """Spot-check the read-only introspection + inference surface stays CLIENT."""
    client_examples = [
        ("POST", "/v1/chat/completions"),
        ("GET", "/api/models"),
        ("GET", "/api/slots"),
        ("GET", "/api/hardware"),
        ("GET", "/api/backends"),
    ]
    for method, path in client_examples:
        assert classify(method, path) is AuthClass.CLIENT, f"{method} {path} should be CLIENT"


def test_unclassified_new_route_denies_by_default() -> None:
    """A path nobody has classified must fall back to ADMIN (not OPEN/CLIENT)."""
    assert match_rule("GET", "/api/totally-new-router-nobody-classified-yet") is None
    assert classify("GET", "/api/totally-new-router-nobody-classified-yet") is AuthClass.ADMIN


# ---------------------------------------------------------------------------
# Enforcement wiring (step 3): the classification table is only as good as
# the middleware that actually reads it. This proves the middleware is
# installed in create_app() and gates a real request end-to-end.


def test_enforcement_wired(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    """With auth forced on, an ADMIN route 401s with no creds and 200s with the key."""
    import os

    from fastapi.testclient import TestClient

    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    os.makedirs(str(tmp_path) + "/etc/hal0", exist_ok=True)
    monkeypatch.setenv("HAL0_REQUIRE_AUTH", "1")
    monkeypatch.setenv("HAL0_ADMIN_KEY", "test-admin-key-123")

    app = create_app()
    with TestClient(app) as client:
        # OPEN route: always reachable, no creds.
        resp = client.get("/api/health")
        assert resp.status_code == 200

        # ADMIN route, no creds: denied.
        resp = client.get("/api/settings")
        assert resp.status_code in (401, 403), resp.text

        # ADMIN route, correct bearer key: allowed through the gate (the
        # route itself may still fail for unrelated reasons in this bare
        # sandbox, but it must not be an auth 401/403).
        resp = client.get(
            "/api/settings",
            headers={"Authorization": "Bearer test-admin-key-123"},
        )
        assert resp.status_code not in (401, 403), resp.text
