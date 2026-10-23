"""HTTP tests for the agent restart routes.

Pins the route contract the dashboard's SidebarAgentBlock + ServiceStatus
chip consume:

* ``POST /api/agents/{agent_id}/restart`` -> 200 ``{status: "restarted"}``
* ``POST /api/agents/{agent_id}/gateway/restart`` -> 200, same shape, for
  the ``hermes-gateway.service`` companion unit
* a failed restart -> 5xx envelope w/ ``code="agent.restart_failed"``
* a timed-out restart -> 5xx envelope w/ ``code="agent.restart_timeout"``
* unknown agent id -> 404 ``code="agent.unknown"``

The restart goes through :func:`hal0.services.systemd.unit_action`, which
runs through the systemctl seam (#2913: hal0-api is unprivileged, and a bare
``systemctl restart`` is denied by polkit on every provisioned box). The tests
patch ``unit_action`` itself, so they never invoke systemd.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from hal0.api.agents import restart as restart_route
from hal0.errors import Hal0Error
from hal0.services.systemd import valid_unit

AGENT_UNIT = "hal0-agent@hermes.service"
GATEWAY_UNIT = "hermes-gateway.service"


def _patch_unit_action(
    monkeypatch: pytest.MonkeyPatch, *, ok: bool = True, message: str = "restarted"
) -> AsyncMock:
    ua = AsyncMock(return_value={"ok": ok, "message": message})
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
    _patch_unit_action(monkeypatch, ok=False, message="denied")
    with pytest.raises(Hal0Error) as e:
        await restart_route.restart_agent_unit("hermes", actor="t")
    assert e.value.code == "agent.restart_failed"
    assert "denied" in str(e.value)


async def test_restart_timeout_keeps_its_own_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """``unit_action`` reports a timeout as a failed result with a ``timed out``
    message. The command-approvals PUT tells a timeout (outcome unknown) from a
    hard failure by code, so the restart seam must keep ``agent.restart_timeout``.
    """
    _patch_unit_action(
        monkeypatch,
        ok=False,
        message=f"restart {AGENT_UNIT} failed: timed out after 90s",
    )
    with pytest.raises(Hal0Error) as e:
        await restart_route.restart_agent_unit("hermes", actor="t")
    assert e.value.code == "agent.restart_timeout"


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
    _patch_unit_action(monkeypatch, ok=False, message="restart failed: Unit not found.")
    r = client.post("/api/agents/hermes/restart")
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
    _patch_unit_action(monkeypatch, ok=False, message="denied")
    audit = _capture_audit(monkeypatch)

    r = client.post("/api/agents/hermes/restart")
    assert r.status_code >= 500

    event_names = [name for name, _ in audit]
    assert event_names == ["agent.restart.invoked", "agent.restart.failed"]


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


def test_gateway_restart_failure_surfaces_envelope(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_unit_action(monkeypatch, ok=False, message="denied")
    r = client.post("/api/agents/hermes/gateway/restart")
    assert r.status_code >= 500
    assert r.json()["error"]["code"] == "agent.restart_failed"


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
    _patch_unit_action(monkeypatch, ok=False, message="denied")
    audit = _capture_audit(monkeypatch)

    r = client.post("/api/agents/hermes/gateway/restart")
    assert r.status_code >= 500

    assert [name for name, _ in audit] == [
        "agent.gateway_restart.invoked",
        "agent.gateway_restart.finished",
    ]
    assert audit[1][1]["status"] == "error"
