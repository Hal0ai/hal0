"""HTTP tests for the agent restart routes.

Pins the route contract the dashboard's SidebarAgentBlock + ServiceStatus
chip consume:

* ``POST /api/agents/{agent_id}/restart`` -> 200 ``{status: "restarted"}``
* ``POST /api/agents/{agent_id}/gateway/restart`` -> 200, same shape, for
  the ``hermes-gateway.service`` companion unit
* a failed restart -> 5xx envelope w/ ``code="agent.restart_failed"``
* a timed-out restart -> 5xx envelope w/ ``code="agent.restart_timeout"``
* unknown agent id -> 404 ``code="agent.unknown"``
* the gateway route on a box without Hermes -> 404 ``code="agent.not_installed"``

The restart goes through :func:`hal0.services.systemd.unit_action`, which
runs through the systemctl seam (#2913: hal0-api is unprivileged, and a bare
``systemctl restart`` is denied by polkit on every provisioned box). The tests
patch ``unit_action`` itself, so they never invoke systemd.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from hal0.api.agents import restart as restart_route
from hal0.api.routes import agents as agents_routes
from hal0.errors import Hal0Error
from hal0.services.systemd import valid_unit

AGENT_UNIT = "hal0-agent@hermes.service"
GATEWAY_UNIT = "hermes-gateway.service"


def _patch_unit_action(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ok: bool = True,
    message: str = "restarted",
    timed_out: bool = False,
) -> AsyncMock:
    ua = AsyncMock(return_value={"ok": ok, "timed_out": timed_out, "message": message})
    monkeypatch.setattr(restart_route, "unit_action", ua)
    return ua


def _capture_audit(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    """Capture every ``hal0.agents.audit`` info/warning emit as (event, kwargs)."""
    calls: list[tuple[str, dict[str, Any]]] = []

    def _capture(event: str, **kwargs: Any) -> None:
        calls.append((event, kwargs))

    monkeypatch.setattr(restart_route.audit_log, "info", _capture)
    monkeypatch.setattr(restart_route.audit_log, "warning", _capture)
    return calls


def _install(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    """The agent manager lists exactly these agents as installed."""
    records = [SimpleNamespace(name=name) for name in names]
    monkeypatch.setattr(agents_routes, "_manager", lambda: SimpleNamespace(list=lambda: records))


@pytest.fixture(autouse=True)
def hermes_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hermes is installed unless a test says otherwise."""
    _install(monkeypatch, "hermes")


def test_both_units_are_valid_unit_names() -> None:
    """``unit_action`` raises ValueError on a unit ``valid_unit`` rejects."""
    assert valid_unit(AGENT_UNIT)
    assert valid_unit(GATEWAY_UNIT)


async def test_restart_routes_through_unit_action(monkeypatch: pytest.MonkeyPatch) -> None:
    ua = _patch_unit_action(monkeypatch)
    out = await restart_route.restart_agent_unit("hermes", actor="t")
    ua.assert_awaited_once_with(AGENT_UNIT, "restart")
    assert out["status"] == "restarted"
    assert out["agent_id"] == "hermes"
    assert out["unit"] == AGENT_UNIT


async def test_restart_failure_raises_hal0_error(monkeypatch: pytest.MonkeyPatch) -> None:
    ua = _patch_unit_action(monkeypatch, ok=False, message="denied")
    with pytest.raises(Hal0Error) as e:
        await restart_route.restart_agent_unit("hermes", actor="t")
    ua.assert_awaited_once_with(AGENT_UNIT, "restart")
    assert e.value.code == "agent.restart_failed"
    assert "denied" in str(e.value)


async def test_restart_timeout_keeps_its_own_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """``unit_action`` flags a timed-out verb with ``timed_out``. The
    command-approvals PUT tells a timeout (outcome unknown) from a hard failure
    by code, so the restart seam must raise ``agent.restart_timeout``."""
    ua = AsyncMock(
        return_value={"ok": False, "timed_out": True, "message": "did not return within 90s"}
    )
    monkeypatch.setattr(restart_route, "unit_action", ua)
    with pytest.raises(Hal0Error) as e:
        await restart_route.restart_agent_unit("hermes", actor="t")
    ua.assert_awaited_once_with(AGENT_UNIT, "restart")
    assert e.value.code == "agent.restart_timeout"


async def test_restart_timeout_is_decided_by_the_flag_not_the_wording(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure whose stderr merely says ``timed out`` (a D-Bus connection
    error, say) is a hard failure: only the structural ``timed_out`` flag makes
    a timeout."""
    _patch_unit_action(monkeypatch, ok=False, message="Connection timed out", timed_out=False)
    with pytest.raises(Hal0Error) as e:
        await restart_route.restart_agent_unit("hermes", actor="t")
    assert e.value.code == "agent.restart_failed"


async def test_restart_failure_message_is_trimmed(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_unit_action(monkeypatch, ok=False, message="x" * 500)
    with pytest.raises(Hal0Error) as e:
        await restart_route.restart_agent_unit("hermes", actor="t")
    assert str(e.value) == "x" * 200


def test_restart_ok_returns_restarted_status(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    ua = _patch_unit_action(monkeypatch)
    r = client.post("/api/agents/hermes/restart")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["agent_id"] == "hermes"
    assert body["unit"] == AGENT_UNIT
    assert body["status"] == "restarted"
    assert "detail" in body
    ua.assert_awaited_once_with(AGENT_UNIT, "restart")


def test_restart_unknown_agent_returns_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    ua = _patch_unit_action(monkeypatch)
    r = client.post("/api/agents/pi-coder/restart")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "agent.unknown"
    ua.assert_not_awaited()


def test_restart_failure_surfaces_envelope(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    ua = _patch_unit_action(monkeypatch, ok=False, message="restart failed: Unit not found.")
    r = client.post("/api/agents/hermes/restart")
    ua.assert_awaited_once_with(AGENT_UNIT, "restart")
    assert r.status_code >= 500
    body = r.json()
    assert body["error"]["code"] == "agent.restart_failed"
    assert "Unit not found" in body["error"]["message"]


def test_restart_emits_audit_log_event(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Audit row goes to the ``hal0.agents.audit`` logger.

    We patch the logger's ``info`` method to capture every audit emit
    rather than rely on structlog capture (which has its own configuration
    surface).
    """
    _patch_unit_action(monkeypatch)
    audit = _capture_audit(monkeypatch)

    r = client.post(
        "/api/agents/hermes/restart",
        headers={"X-hal0-Agent": "test-runner"},
    )
    assert r.status_code == 200

    event_names = [name for name, _ in audit]
    assert "agent.restart.invoked" in event_names
    assert "agent.restart.ok" in event_names
    # Actor identity from X-hal0-Agent is captured.
    invoked = next(kw for name, kw in audit if name == "agent.restart.invoked")
    assert invoked["actor"] == "test-runner"
    assert invoked["agent_id"] == "hermes"
    assert invoked["unit"] == AGENT_UNIT


def test_restart_failure_emits_audit_log_event(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    ua = _patch_unit_action(monkeypatch, ok=False, message="denied")
    audit = _capture_audit(monkeypatch)

    r = client.post("/api/agents/hermes/restart")
    assert r.status_code >= 500
    ua.assert_awaited_once_with(AGENT_UNIT, "restart")

    event_names = [name for name, _ in audit]
    assert event_names == ["agent.restart.invoked", "agent.restart.failed"]


def test_restart_timeout_emits_timeout_audit_event(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    ua = _patch_unit_action(
        monkeypatch, ok=False, message="did not return within 90s", timed_out=True
    )
    audit = _capture_audit(monkeypatch)

    r = client.post("/api/agents/hermes/restart")
    assert r.status_code >= 500
    assert r.json()["error"]["code"] == "agent.restart_timeout"
    ua.assert_awaited_once_with(AGENT_UNIT, "restart")

    event_names = [name for name, _ in audit]
    assert event_names == ["agent.restart.invoked", "agent.restart.timeout"]


def test_restart_actor_defaults_to_dashboard(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No X-hal0-Agent header -> actor recorded as ``hal0-dashboard``."""
    _patch_unit_action(monkeypatch)
    audit = _capture_audit(monkeypatch)

    r = client.post("/api/agents/hermes/restart")
    assert r.status_code == 200
    invoked = next(kw for name, kw in audit if name == "agent.restart.invoked")
    assert invoked["actor"] == "hal0-dashboard"


def test_gateway_restart_route(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    ua = _patch_unit_action(monkeypatch)
    r = client.post("/api/agents/hermes/gateway/restart")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["unit"] == GATEWAY_UNIT
    assert body["agent_id"] == "hermes"
    assert body["status"] == "restarted"
    assert "detail" in body
    ua.assert_awaited_once_with(GATEWAY_UNIT, "restart")


def test_gateway_restart_unknown_agent_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    ua = _patch_unit_action(monkeypatch)
    r = client.post("/api/agents/pi/gateway/restart")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "agent.unknown"
    ua.assert_not_awaited()


def test_gateway_restart_is_gated_on_gateway_agents_not_restartable_agents(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An agent that joins the restart registry without a messaging gateway
    still 404s on the gateway route."""
    ua = _patch_unit_action(monkeypatch)
    monkeypatch.setattr(restart_route, "_KNOWN_AGENT_IDS", frozenset({"hermes", "pi"}))
    assert client.post("/api/agents/pi/restart").status_code == 200
    r = client.post("/api/agents/pi/gateway/restart")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "agent.unknown"
    ua.assert_awaited_once_with("hal0-agent@pi.service", "restart")


def test_gateway_restart_failure_surfaces_envelope(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    ua = _patch_unit_action(monkeypatch, ok=False, message="denied")
    r = client.post("/api/agents/hermes/gateway/restart")
    assert r.status_code >= 500
    assert r.json()["error"]["code"] == "agent.restart_failed"
    ua.assert_awaited_once_with(GATEWAY_UNIT, "restart")


def test_gateway_restart_timeout_surfaces_timeout_code(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    ua = _patch_unit_action(
        monkeypatch, ok=False, message="did not return within 90s", timed_out=True
    )
    r = client.post("/api/agents/hermes/gateway/restart")
    assert r.status_code >= 500
    assert r.json()["error"]["code"] == "agent.restart_timeout"
    ua.assert_awaited_once_with(GATEWAY_UNIT, "restart")


def test_gateway_restart_emits_audit_log_events(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_unit_action(monkeypatch)
    audit = _capture_audit(monkeypatch)

    r = client.post(
        "/api/agents/hermes/gateway/restart",
        headers={"X-hal0-Agent": "test-runner"},
    )
    assert r.status_code == 200

    assert [name for name, _ in audit] == [
        "agent.gateway_restart.invoked",
        "agent.gateway_restart.finished",
    ]
    invoked = audit[0][1]
    assert invoked["actor"] == "test-runner"
    assert invoked["agent_id"] == "hermes"
    assert invoked["unit"] == GATEWAY_UNIT
    assert audit[1][1]["status"] == "restarted"


def test_gateway_restart_failure_still_emits_finished_event(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    ua = _patch_unit_action(monkeypatch, ok=False, message="denied")
    audit = _capture_audit(monkeypatch)

    r = client.post("/api/agents/hermes/gateway/restart")
    assert r.status_code >= 500
    ua.assert_awaited_once_with(GATEWAY_UNIT, "restart")

    assert [name for name, _ in audit] == [
        "agent.gateway_restart.invoked",
        "agent.gateway_restart.finished",
    ]
    assert audit[1][1]["status"] == "error"


def test_gateway_restart_404_when_hermes_is_not_installed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No install record, no gateway: refused before the seam is touched."""
    _install(monkeypatch)
    ua = _patch_unit_action(monkeypatch)

    r = client.post("/api/agents/hermes/gateway/restart")

    assert r.status_code == 404
    error = r.json()["error"]
    assert error["code"] == "agent.not_installed"
    assert error["details"] == {"agent_id": "hermes"}
    ua.assert_not_awaited()


def test_restart_does_not_ask_whether_the_agent_is_installed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the gateway route does: the agent unit's restart starts a stopped unit."""
    _install(monkeypatch)
    ua = _patch_unit_action(monkeypatch)

    assert client.post("/api/agents/hermes/restart").status_code == 200
    ua.assert_awaited_once_with(AGENT_UNIT, "restart")
