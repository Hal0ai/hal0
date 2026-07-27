"""Issues #1024 / #1302 (1): the memory delete surface is ADMIN, provably.

#1024's incident was ``DELETE /api/memory/banks/{bank_id}`` reached with one
unauthenticated curl, cascade-deleting ~632 live records. Two of that
issue's three hardening asks landed already — the echoed-``?confirm=`` guard
(#1028) and the ``record_action`` audit row (#1030) — and both have their own
coverage in ``tests/api/test_memory_admin_routes.py``. The third, "reintroduce
write-auth on mutating ``/api/memory/*`` routes", was left open pending a
posture decision, and #1302 restates it.

The posture, encoded here: **the whole ``/api/memory`` surface is ADMIN, and
its irreversible subset is pinned by name so it cannot be widened by
accident.** ``hal0.security.exposure`` already classified the prefix ADMIN,
but nothing anywhere asserted it for the destructive routes, and #1024's own
follow-up comment proposes "keep reads open if desired" — a widening whose
natural expression (``_prefix("/api/memory")`` for reads) silently takes the
bank wipe with it unless the destructive rows are pinned first.

So this file covers three distinct claims:

1. ``DESTRUCTIVE_MEMORY_ROUTES`` is exactly the destructive memory surface
   the app really serves (adding one forces a diff to the constant).
2. Each of those routes classifies ADMIN, is absent from ``OPEN_ALLOWLIST``,
   and *stays* ADMIN under a simulated memory-reads-are-CLIENT widening.
3. The real ``AuthEnforcementMiddleware``, armed, 401s an anonymous delete
   and lets an admin bearer through — golden path plus negative control,
   the shape ``tests/agents/test_hermes_provision_mcp_auth.py`` established.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.security import exposure
from hal0.security.exposure import (
    DESTRUCTIVE_MEMORY_ROUTES,
    OPEN_ALLOWLIST,
    AuthClass,
    classify,
    match_rule,
)
from tests.security.test_exposure import _enumerate_routes

ADMIN_KEY = "memory-delete-admin-key"
CLIENT_KEY = "memory-delete-client-key"

#: Concrete, grammar-valid substitutions for every ``{...}`` path arg that
#: appears in a DESTRUCTIVE_MEMORY_ROUTES template. They must satisfy
#: ``memory_admin._BANK_RE`` / ``_SEG_RE`` so a request that clears the auth
#: gate reaches a real handler — otherwise a "not 401" assertion could be
#: satisfied by a 400 from path validation instead.
_PATH_ARGS = {
    "bank_id": "shared",
    "document_id": "doc-1",
    "directive_id": "dir-1",
    "model_id": "mm-1",
    "operation_id": "op-1",
}


def _url(path: str) -> str:
    return path.format(**_PATH_ARGS)


# ── 1. the constant tracks the live surface ──────────────────────────────────


@pytest.fixture(scope="module")
def memory_routes(tmp_path_factory: pytest.TempPathFactory) -> set[tuple[str, str]]:
    """Every ``(method, template)`` the live app serves under /api/memory."""
    hal0_home = tmp_path_factory.mktemp("memory-delete-auth-home")
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("HAL0_HOME", str(hal0_home))
        monkeypatch.setattr("hal0.api._mount_dashboard", lambda _app: None)
        app = create_app()
    routes = {(m, p) for m, p in _enumerate_routes(app) if p.startswith("/api/memory")}
    assert routes, "no /api/memory routes mounted — the walker or the router moved"
    return routes


def test_destructive_memory_routes_tracks_the_live_route_table(
    memory_routes: set[tuple[str, str]],
) -> None:
    """The constant is the destructive memory surface — exactly.

    "Destructive" = any DELETE under /api/memory, plus the one POST that
    hides a bulk delete behind a create-shaped verb (``POST
    /api/memory/delete``, body ``{"ids": [...]}``). A new memory delete
    route that nobody pinned trips this, which is the point: the ratchet
    forces the classification question to be answered in the same diff.
    """
    live_destructive = {
        (method, path)
        for method, path in memory_routes
        if method == "DELETE" or (method == "POST" and path.endswith("/delete"))
    }
    assert live_destructive == DESTRUCTIVE_MEMORY_ROUTES, (
        "the destructive /api/memory surface drifted from "
        "exposure.DESTRUCTIVE_MEMORY_ROUTES.\n"
        f"Live but unpinned: {sorted(live_destructive - DESTRUCTIVE_MEMORY_ROUTES)}\n"
        f"Pinned but gone:   {sorted(DESTRUCTIVE_MEMORY_ROUTES - live_destructive)}"
    )


# ── 2. classification ────────────────────────────────────────────────────────


@pytest.mark.parametrize(("method", "path"), sorted(DESTRUCTIVE_MEMORY_ROUTES))
def test_destructive_memory_route_is_admin(method: str, path: str) -> None:
    assert classify(method, path) is AuthClass.ADMIN
    assert (method, path) not in OPEN_ALLOWLIST


@pytest.mark.parametrize(("method", "path"), sorted(DESTRUCTIVE_MEMORY_ROUTES))
def test_destructive_memory_route_matches_a_pinned_rule(method: str, path: str) -> None:
    """Not ADMIN-by-generic-prefix, and not ADMIN-by-silent-fallback.

    ``match_rule`` (rather than ``classify``) distinguishes the three ways a
    route can end up ADMIN. These must resolve to one of the two rows added
    for this lane, because that ordering is what makes the widening test
    below hold.
    """
    rule = match_rule(method, path)
    assert rule is not None, f"{method} {path} falls through to the ADMIN fallback"
    assert rule.label in {
        "memory destructive (any DELETE)",
        "memory namespace bulk delete",
    }, f"{method} {path} resolved via {rule.label!r}, not a pinned destructive rule"


def test_destructive_routes_survive_a_memory_reads_are_client_widening(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The threat the pinning exists for, simulated.

    #1024's follow-up comment proposes keeping memory reads open. Encode
    that proposal the way a developer editing the memory group actually
    would — a broad ``/api/memory`` CLIENT rule dropped in next to the
    generic ``memory`` row — and every destructive route must STILL
    classify ADMIN, because the pinned rows sit above that whole group.
    Before this lane there was nothing above it: the same edit downgraded
    ``DELETE /api/memory/banks/{bank_id}`` to CLIENT, which is #1024's
    incident minus the "unauthenticated" adjective.

    (Rules are first-match-wins, so this only holds while the pinned rows
    stay at the TOP of the memory group. ``exposure.py``'s ordering comment
    says as much; this test is what enforces it.)
    """
    generic = next(i for i, rule in enumerate(exposure.RULES) if rule.label == "memory")
    widened = (
        *exposure.RULES[:generic],
        exposure._Rule(
            "HYPOTHETICAL memory reads are client",
            exposure._prefix("/api/memory"),
            AuthClass.CLIENT,
            None,
        ),
        *exposure.RULES[generic:],
    )
    monkeypatch.setattr(exposure, "RULES", widened)

    # Sanity: the hypothetical rule really is in force for a plain read.
    assert classify("GET", "/api/memory/banks") is AuthClass.CLIENT

    for method, path in sorted(DESTRUCTIVE_MEMORY_ROUTES):
        assert classify(method, path) is AuthClass.ADMIN, (
            f"{method} {path} was downgraded to "
            f"{classify(method, path).value} by a memory-read widening"
        )


# ── 3. the real gate, armed ──────────────────────────────────────────────────


@pytest.fixture
def armed_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> TestClient:
    """A real ``create_app()`` with ``AuthEnforcementMiddleware`` enforcing.

    Not a hand-rolled mini-app: the classification decision under test is
    only meaningful against the route table and middleware ``create_app``
    actually ships.
    """
    hal0_home = tmp_path_factory.mktemp("memory-delete-armed-home")
    (hal0_home / "etc" / "hal0").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HAL0_HOME", str(hal0_home))
    monkeypatch.setenv("HAL0_REQUIRE_AUTH", "1")
    monkeypatch.setenv("HAL0_ADMIN_KEY", ADMIN_KEY)
    monkeypatch.setenv("HAL0_CLIENT_KEY", CLIENT_KEY)
    monkeypatch.setattr("hal0.api._mount_dashboard", lambda _app: None)
    return TestClient(create_app())


@pytest.mark.parametrize(("method", "path"), sorted(DESTRUCTIVE_MEMORY_ROUTES))
def test_destructive_memory_route_401s_without_credentials(
    armed_client: TestClient, method: str, path: str
) -> None:
    """NEGATIVE CONTROL — the #1024 one-curl wipe, refused at the perimeter.

    Every path arg is filled with a syntactically valid id so the request
    would reach a real handler if the gate let it: a 401 here can only come
    from the auth middleware, never from routing or validation.
    """
    url = _url(path)
    resp = armed_client.request(method, url, json={"ids": ["a", "b"], "confirm": "shared"})
    assert resp.status_code == 401, f"{method} {url} -> {resp.status_code}: {resp.text}"
    assert resp.json()["error"]["code"] == "auth.required"


@pytest.mark.parametrize(("method", "path"), sorted(DESTRUCTIVE_MEMORY_ROUTES))
def test_destructive_memory_route_clears_the_gate_with_the_admin_key(
    armed_client: TestClient, method: str, path: str
) -> None:
    """GOLDEN PATH — the operator's own key still reaches the handler.

    The handler then fails for an unrelated reason (no memory engine in this
    sandbox → 503 ``memory.unavailable``, or 400 for the missing echoed
    confirm); what matters is that the response is not an auth denial.
    """
    url = _url(path)
    resp = armed_client.request(
        method,
        url,
        headers={"Authorization": f"Bearer {ADMIN_KEY}"},
        json={"ids": ["a", "b"], "confirm": "shared"},
    )
    assert resp.status_code not in (401, 403), f"{method} {url} -> {resp.text}"


@pytest.mark.parametrize(("method", "path"), sorted(DESTRUCTIVE_MEMORY_ROUTES))
def test_destructive_memory_route_403s_for_a_client_key(
    armed_client: TestClient, method: str, path: str
) -> None:
    """ADMIN tier, not merely "any valid key".

    The inference/client key is the one an agent or an OpenAI-SDK caller on
    the LAN is most likely to hold; it must not be able to delete memory.
    """
    url = _url(path)
    resp = armed_client.request(
        method,
        url,
        headers={"Authorization": f"Bearer {CLIENT_KEY}"},
        json={"ids": ["a", "b"], "confirm": "shared"},
    )
    assert resp.status_code == 403, f"{method} {url} -> {resp.status_code}: {resp.text}"
    assert resp.json()["error"]["code"] == "auth.forbidden"


def test_mcp_memory_mount_401s_without_credentials(armed_client: TestClient) -> None:
    """#1302's other delete path — the ``/mcp/memory`` JSON-RPC mount.

    The bulk-delete approval gate on that mount (landed separately for
    #1302 — ``hal0.mcp.memory.make_dispatcher``'s ``approval_queue``, covered
    by ``tests/mcp/test_memory_delete_gate_mount.py``) is the second line; the
    first is that an anonymous caller never gets to speak MCP at all.
    """
    resp = armed_client.post(
        "/mcp/memory/mcp",
        headers={"Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    assert resp.status_code == 401, resp.text


def test_memory_reads_are_admin_too(armed_client: TestClient) -> None:
    """Records the posture decision itself, so a future reader can't guess.

    Reads are ADMIN as well — the "keep reads open" option from #1024 was
    NOT taken. If that changes, this test is the place the change is argued.
    """
    assert classify("GET", "/api/memory/banks") is AuthClass.ADMIN
    resp = armed_client.get("/api/memory/banks")
    assert resp.status_code == 401, resp.text
