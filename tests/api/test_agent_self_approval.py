"""ADR-0024 item 2 / ADR-0048: a key holder cannot decide an approval over REST.

Agents are provisioned with ``service_key(prefer="admin")``
(``hal0.agents.hermes_provision``, ``hal0.agents.pi_coder.driver``), i.e. the
box admin key, and ``POST /api/agent/approvals/{id}/approve`` is an ADMIN route
the auth layer opens to that key. So the tier check alone cannot tell an agent
from its owner; the route's own check has to.

The ``test_known_gap_*`` tests document what this does NOT close (ADR-0048 says
why: while an agent holds the admin key, or can read the session secret, it can
obtain a session and decide with that). They are records, not tripwires: they
log in with the test's literal ``ADMIN_KEY``, mint a cookie from the test
process, or send no key, so none of them fails when ADR-0024 items 1 and 4 land.
Only ``test_agent_credential_is_the_admin_key_and_the_tier_alone_would_admit_it``
trips, and only on item 1 (the agent stops being given the admin key); rewrite
this docstring and the gap tests then.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0 import service_identity
from hal0.api import auth as auth_mod
from hal0.api.agents import _auth as session_mod
from hal0.api.middleware import error_codes
from hal0.api.routes import approvals as approvals_routes
from hal0.api.routes import auth as auth_routes
from hal0.mcp.approval_queue import ApprovalQueue
from hal0.security.exposure import AuthClass, classify

APPROVE_PATH = "/api/agent/approvals/abc123/approve"
ADMIN_KEY = "box-admin-key"
CLIENT_KEY = "box-client-key"
LOOPBACK = ("127.0.0.1", 50000)
LAN = ("192.0.2.7", 50000)


@pytest.fixture(autouse=True)
def _isolated_box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("HAL0_AGENT_SECRET_PATH", str(tmp_path / "secret.bin"))
    monkeypatch.setenv("HAL0_HOME", str(tmp_path / "hal0_home"))
    monkeypatch.setenv("HAL0_ADMIN_KEY", ADMIN_KEY)
    monkeypatch.setenv("HAL0_CLIENT_KEY", CLIENT_KEY)
    monkeypatch.delenv("HAL0_REQUIRE_AUTH", raising=False)
    auth_mod._require_auth_cache = None
    yield


def _app() -> FastAPI:
    """The real gate, the real routes, the real queue; no lifespan."""
    app = FastAPI()
    error_codes.install(app)
    app.add_middleware(auth_mod.AuthEnforcementMiddleware)
    app.include_router(approvals_routes.router, prefix="/api/agent/approvals")
    app.include_router(auth_routes.router, prefix="/api/auth")
    app.state.approval_queue = ApprovalQueue()
    return app


class _Box:
    def __init__(self, peer: tuple[str, int] = LOOPBACK) -> None:
        self.app = _app()
        self.client = TestClient(self.app, client=peer)
        self.calls: list[dict[str, Any]] = []

    def enqueue(self) -> str:
        async def _exec(args: dict[str, Any]) -> dict[str, Any]:
            self.calls.append(args)
            return {"ok": True}

        with TestClient(self.app) as portal_client:
            portal = portal_client.portal
            assert portal is not None
            aid: str = portal.call(
                lambda: self.app.state.approval_queue.enqueue(
                    tool="slot_delete", args={"name": "x"}, client_id="hermes", executor=_exec
                )
            )
        return aid

    def pending(self) -> list[str]:
        return [e["id"] for e in self.app.state.approval_queue.list_pending()]


def _session_cookie() -> dict[str, str]:
    return {session_mod.SESSION_COOKIE_NAME: session_mod.mint_session_cookie()}


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _assert_refused(response: Any, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code


def test_agent_credential_is_the_admin_key_and_the_tier_alone_would_admit_it() -> None:
    """The fact behind ADR-0024: what an agent holds, and why the tier cannot judge it."""
    agent_key = service_identity.service_key(prefer="admin")
    assert agent_key == ADMIN_KEY
    assert classify("POST", APPROVE_PATH) is AuthClass.ADMIN
    assert classify("POST", APPROVE_PATH.replace("/approve", "/deny")) is AuthClass.ADMIN
    scope = {"type": "http", "headers": [(b"authorization", f"Bearer {agent_key}".encode())]}
    principal = auth_mod.resolve_principal(scope)
    assert auth_mod._decide(AuthClass.ADMIN, principal)[0] is True
    assert auth_mod.approval_decision_refusal(scope) == (403, "auth.forbidden")


@pytest.mark.parametrize("verb", ["approve", "deny"])
@pytest.mark.parametrize("require_auth", ["1", "0"])
def test_bearer_admin_key_gets_403_and_decides_nothing(
    monkeypatch: pytest.MonkeyPatch, verb: str, require_auth: str
) -> None:
    monkeypatch.setenv("HAL0_REQUIRE_AUTH", require_auth)
    box = _Box()
    aid = box.enqueue()
    response = box.client.post(f"/api/agent/approvals/{aid}/{verb}", headers=_bearer(ADMIN_KEY))
    _assert_refused(response, 403, "auth.forbidden")
    assert box.calls == []
    assert box.pending() == [aid]


@pytest.mark.parametrize("verb", ["approve", "deny"])
@pytest.mark.parametrize(
    ("key", "why"),
    [(CLIENT_KEY, "client tier"), ("hal0bk_nobody.nope", "bridge-shaped"), ("wrong", "unknown")],
)
def test_any_other_bearer_gets_403_at_the_open_posture(verb: str, key: str, why: str) -> None:
    box = _Box()
    aid = box.enqueue()
    response = box.client.post(f"/api/agent/approvals/{aid}/{verb}", headers=_bearer(key))
    assert response.status_code == 403, why
    assert box.pending() == [aid]


@pytest.mark.parametrize("key", [ADMIN_KEY, CLIENT_KEY, "wrong", ""])
def test_a_key_in_the_query_string_gets_403(key: str) -> None:
    box = _Box()
    aid = box.enqueue()
    response = box.client.post(f"/api/agent/approvals/{aid}/approve", params={"api_key": key})
    _assert_refused(response, 403, "auth.forbidden")
    assert box.pending() == [aid]


@pytest.mark.parametrize(("verb", "state"), [("approve", "executed"), ("deny", "denied")])
@pytest.mark.parametrize("require_auth", ["1", "0"])
def test_a_session_cookie_decides(
    monkeypatch: pytest.MonkeyPatch, verb: str, state: str, require_auth: str
) -> None:
    monkeypatch.setenv("HAL0_REQUIRE_AUTH", require_auth)
    box = _Box()
    aid = box.enqueue()
    box.client.cookies.update(_session_cookie())
    response = box.client.post(f"/api/agent/approvals/{aid}/{verb}")
    assert response.status_code == 200, response.text
    assert response.json()["approval"]["state"] == state


def test_a_forged_cookie_does_not_count_as_a_session() -> None:
    box = _Box()
    aid = box.enqueue()
    box.client.cookies.set(session_mod.SESSION_COOKIE_NAME, "e30.AAAA")
    response = box.client.post(f"/api/agent/approvals/{aid}/approve")
    _assert_refused(response, 401, "auth.required")
    assert box.pending() == [aid]


@pytest.mark.parametrize("peer", [LOOPBACK, LAN])
def test_auth_off_with_an_admin_key_anonymous_gets_401_even_on_loopback(
    peer: tuple[str, int],
) -> None:
    """The agent is on loopback too, so loopback origin proves nothing.

    The 401 is what the dashboard's mutation handler turns into its sign-in
    drawer, and the rest of the ADMIN surface stays open to the same caller.
    """
    box = _Box(peer)
    aid = box.enqueue()
    response = box.client.post(f"/api/agent/approvals/{aid}/approve")
    _assert_refused(response, 401, "auth.required")
    assert box.calls == []


def test_auth_off_with_an_admin_key_the_operator_signs_in_then_decides() -> None:
    """The dashboard's path on such a box: refused, drawer, login, retry."""
    box = _Box()
    aid = box.enqueue()
    assert box.client.post(f"/api/agent/approvals/{aid}/approve").status_code == 401
    assert box.client.post("/api/auth/login", json={"key": ADMIN_KEY}).status_code == 200
    response = box.client.post(f"/api/agent/approvals/{aid}/approve")
    assert response.status_code == 200, response.text
    assert box.calls == [{"name": "x"}]


def test_a_refused_attempt_leaves_an_audit_row(tmp_path: Path) -> None:
    from hal0.activity import AuditStore

    box = _Box()
    box.app.state.audit = AuditStore(tmp_path / "audit.db")
    box.app.state.audit.init_schema()
    aid = box.enqueue()
    response = box.client.post(f"/api/agent/approvals/{aid}/approve", headers=_bearer(ADMIN_KEY))
    assert response.status_code == 403
    rows = box.app.state.audit.query(action="approval.approve")
    assert [(r["target"], r["outcome"]) for r in rows] == [(aid, "error")]


def test_reading_the_inbox_keeps_its_ordinary_posture() -> None:
    box = _Box()
    assert box.client.get("/api/agent/approvals").status_code == 200
    assert box.client.get("/api/agent/approvals", headers=_bearer(ADMIN_KEY)).status_code == 200


class TestKeylessBox:
    """No admin key: nothing can sign in, so the conflict ADR-0048 names is live here."""

    @pytest.fixture(autouse=True)
    def _no_admin_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("HAL0_ADMIN_KEY", raising=False)

    @pytest.mark.parametrize("peer", [LOOPBACK, LAN])
    def test_the_dashboard_still_decides(self, peer: tuple[str, int]) -> None:
        box = _Box(peer)
        aid = box.enqueue()
        assert box.client.post(f"/api/agent/approvals/{aid}/approve").status_code == 200
        assert box.calls == [{"name": "x"}]

    def test_the_client_key_the_agent_holds_is_refused(self) -> None:
        """What the agent is given on such a box, sent as provisioned."""
        assert service_identity.service_key(prefer="admin") == CLIENT_KEY
        box = _Box()
        aid = box.enqueue()
        response = box.client.post(
            f"/api/agent/approvals/{aid}/approve", headers=_bearer(CLIENT_KEY)
        )
        _assert_refused(response, 403, "auth.forbidden")
        assert box.pending() == [aid]

    def test_known_gap_a_caller_that_drops_its_key_can_decide(self) -> None:
        """Pinned, not endorsed: an anonymous agent cannot be told from the dashboard here."""
        box = _Box()
        aid = box.enqueue()
        assert box.client.post(f"/api/agent/approvals/{aid}/deny").status_code == 200

    def test_a_proxy_basic_auth_header_is_not_a_key(self) -> None:
        """A reverse proxy's own Basic auth reaches hal0 and must not read as a key."""
        box = _Box()
        aid = box.enqueue()
        response = box.client.post(
            f"/api/agent/approvals/{aid}/deny", headers={"Authorization": "Basic b3A6cHc="}
        )
        assert response.status_code == 200


def test_known_gap_the_admin_key_still_buys_a_session() -> None:
    """Pinned, not endorsed: ADR-0048's main limit. Closed by ADR-0024 items 1 and 4."""
    box = _Box()
    aid = box.enqueue()
    agent = box.client
    assert agent.post("/api/auth/login", json={"key": ADMIN_KEY}).status_code == 200
    assert agent.post(f"/api/agent/approvals/{aid}/approve").status_code == 200


def test_known_gap_the_session_secret_signs_a_session_with_no_key_at_all(tmp_path: Path) -> None:
    """Pinned, not endorsed: the secret is a file ``hal0`` owns, so reading it is enough."""
    box = _Box()
    aid = box.enqueue()
    box.client.cookies.update(_session_cookie())  # minted from the file, as an agent could
    assert (tmp_path / "secret.bin").exists()
    assert box.client.post(f"/api/agent/approvals/{aid}/deny").status_code == 200
