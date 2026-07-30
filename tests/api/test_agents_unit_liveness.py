"""GET /api/agents must report systemd unit liveness, not just install state (#1459).

``AgentManager.list()`` is a filesystem read: an agent whose bundle is on disk
reports ``status="installed"`` whether or not ``hal0-agent@<name>.service`` is
running. The dashboard mapped that install-state straight onto liveness, so an
inactive Hermes rendered as ready/serving.

The route now probes the unit through the EXISTING systemctl seam
(:mod:`hal0.api.agents.restart`) and adds ``unit_active`` to every record:

    True   — ``systemctl is-active`` said ``active``
    False  — the unit exists but is not active
    None   — the probe could not run (no systemd, timeout, unknown agent).

``None`` is "unknown"; it must never be rendered as healthy.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.agents.manager import AgentRecord
from hal0.api.agents import restart as agent_restart
from hal0.api.middleware import error_codes
from hal0.api.routes import agents as agents_routes


class _FakeManager:
    def __init__(self, records: list[AgentRecord]) -> None:
        self._records = records

    def list(self) -> list[AgentRecord]:
        return self._records


def _hermes() -> AgentRecord:
    return AgentRecord(
        name="hermes",
        installed_at="2026-07-30T00:00:00Z",
        status="installed",
        data_dir="/var/lib/hal0/agents/hermes",
        config_path="/etc/hal0/agents/hermes.toml",
    )


@pytest.fixture
def agents_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    app = FastAPI()
    error_codes.install(app)
    app.include_router(agents_routes.router, prefix="/api/agents", tags=["agents"])
    monkeypatch.setattr(agents_routes, "_manager", lambda: _FakeManager([_hermes()]))
    with TestClient(app) as c:
        yield c


def _stub_probe(monkeypatch: pytest.MonkeyPatch, result: bool | None) -> None:
    async def _probe(agent_id: str) -> bool | None:
        assert agent_id == "hermes"
        return result

    monkeypatch.setattr(agent_restart, "unit_is_active", _probe)


def test_active_unit_reports_unit_active_true(
    agents_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_probe(monkeypatch, True)
    body = agents_client.get("/api/agents").json()
    assert body["count"] == 1
    rec = body["agents"][0]
    assert rec["status"] == "installed"
    assert rec["unit_active"] is True


def test_inactive_unit_reports_unit_active_false(
    agents_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The live-box case: bundle installed on disk, unit inactive."""
    _stub_probe(monkeypatch, False)
    rec = agents_client.get("/api/agents").json()["agents"][0]
    assert rec["status"] == "installed"
    assert rec["unit_active"] is False


def test_probe_unavailable_reports_unit_active_null(
    agents_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No systemd (container / dev box) degrades to unknown, never to healthy."""
    _stub_probe(monkeypatch, None)
    rec = agents_client.get("/api/agents").json()["agents"][0]
    assert rec["status"] == "installed"
    assert rec["unit_active"] is None


def test_probe_returns_none_when_systemctl_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The seam itself degrades rather than raising when systemctl is absent."""
    monkeypatch.setattr(agent_restart, "_systemctl_path", lambda: None)
    import asyncio

    assert asyncio.run(agent_restart.unit_is_active("hermes")) is None
