"""Tests for /api/updates/slot-drift + /api/updates/restart-slots (WS-J, #1111).

``rerender_slot_units`` refreshes each on-disk slot unit after a self-update
but never bounces the running process (a restart could kill a mid-inference
request). These endpoints surface the resulting "post-update drift" — slots
still running the pre-update launch command — and let an operator clear it on
demand. The drift signal itself is ``SlotManager.compute_config_drift`` (the
#1103 reconcile seam); here we stub the manager so the aggregation + restart
routing is exercised without a live container runtime.
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app


class _StubSlotManager:
    """Minimal async SlotManager surface the drift endpoints depend on."""

    def __init__(
        self,
        drift: dict[str, dict[str, Any] | None],
        *,
        restart_error: dict[str, str] | None = None,
    ) -> None:
        # drift maps slot name -> compute_config_drift() return value
        # ({"drifted": bool, "diffs": [...]} or None for inactive slots).
        self._drift = drift
        self._restart_error = restart_error or {}
        self.restarted: list[str] = []

    async def list(self) -> list[Any]:
        return [SimpleNamespace(name=name) for name in self._drift]

    async def compute_config_drift(self, name: str, **_: Any) -> dict[str, Any] | None:
        return self._drift.get(name)

    async def restart(self, name: str) -> Any:
        if name in self._restart_error:
            raise RuntimeError(self._restart_error[name])
        self.restarted.append(name)
        return SimpleNamespace(name=name)


@pytest.fixture
def client(tmp_hal0_home: str) -> Iterator[TestClient]:
    app: FastAPI = create_app()
    with TestClient(app) as c:
        yield c


def _install_sm(client: TestClient, sm: _StubSlotManager) -> None:
    # The lifespan wires a real SlotManager; the route reads it off app.state
    # at request time, so a post-lifespan swap takes effect on the next call.
    client.app.state.slot_manager = sm


def test_slot_drift_reports_only_drifted(client: TestClient) -> None:
    sm = _StubSlotManager(
        {
            "chat": {
                "drifted": True,
                "diffs": [{"key": "--ctx-size", "running": "4096", "rendered": "131072"}],
            },
            "code": {"drifted": False, "diffs": []},
            "voice": None,  # inactive slot — cannot run a stale process
        }
    )
    _install_sm(client, sm)
    r = client.get("/api/updates/slot-drift")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 1
    assert [s["slot"] for s in body["slots"]] == ["chat"]
    assert body["slots"][0]["diffs"][0]["key"] == "--ctx-size"


def test_slot_drift_clean_when_nothing_drifted(client: TestClient) -> None:
    sm = _StubSlotManager({"chat": {"drifted": False, "diffs": []}, "voice": None})
    _install_sm(client, sm)
    r = client.get("/api/updates/slot-drift")
    assert r.status_code == 200, r.text
    assert r.json() == {"count": 0, "slots": []}


def test_restart_slots_bounces_only_drifted(client: TestClient) -> None:
    sm = _StubSlotManager(
        {
            "chat": {"drifted": True, "diffs": []},
            "code": {"drifted": False, "diffs": []},
        }
    )
    _install_sm(client, sm)
    r = client.post("/api/updates/restart-slots")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["restarted"] == ["chat"]
    assert body["failed"] == []
    assert body["count"] == 1
    # The non-drifted slot must never be bounced.
    assert sm.restarted == ["chat"]


def test_restart_slots_subset_filter(client: TestClient) -> None:
    sm = _StubSlotManager(
        {
            "chat": {"drifted": True, "diffs": []},
            "code": {"drifted": True, "diffs": []},
        }
    )
    _install_sm(client, sm)
    r = client.post("/api/updates/restart-slots", json={"slots": ["code"]})
    assert r.status_code == 200, r.text
    assert r.json()["restarted"] == ["code"]
    assert sm.restarted == ["code"]


def test_restart_slots_records_per_slot_failure(client: TestClient) -> None:
    sm = _StubSlotManager(
        {
            "chat": {"drifted": True, "diffs": []},
            "code": {"drifted": True, "diffs": []},
        },
        restart_error={"chat": "boom"},
    )
    _install_sm(client, sm)
    r = client.post("/api/updates/restart-slots")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["restarted"] == ["code"]
    assert body["failed"] == [{"slot": "chat", "error": "boom"}]
    assert body["count"] == 1
