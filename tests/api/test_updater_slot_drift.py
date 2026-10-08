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

import asyncio
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
        return [SimpleNamespace(name=name, port=i + 1) for i, name in enumerate(self._drift)]

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
    assert r.json() == {"count": 0, "slots": [], "auto_restart": None}


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


def test_restart_slots_skip_busy_leaves_in_flight_slot_alone(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``skip_busy`` (#2096): a slot llama-server reports as processing is not bounced."""
    sm = _StubSlotManager(
        {
            "brain": {"drifted": True, "diffs": [{"key": "image"}]},
            "agent": {"drifted": True, "diffs": [{"key": "image"}]},
        }
    )
    _install_sm(client, sm)

    async def fake_llama_metrics(port: int) -> dict[str, Any]:
        return {"requests_processing": 1 if port == 2 else 0}

    monkeypatch.setattr("hal0.slots.metrics_collect.llama_metrics", fake_llama_metrics)
    r = client.post("/api/updates/restart-slots", json={"skip_busy": True})
    assert r.status_code == 200
    body = r.json()
    assert body["restarted"] == ["brain"]
    assert body["skipped_busy"] == ["agent"]
    assert sm.restarted == ["brain"]


def test_restart_slots_without_skip_busy_restarts_busy_slots(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sm = _StubSlotManager({"agent": {"drifted": True, "diffs": [{"key": "image"}]}})
    _install_sm(client, sm)

    async def fake_llama_metrics(port: int) -> dict[str, Any]:
        return {"requests_processing": 3}

    monkeypatch.setattr("hal0.slots.metrics_collect.llama_metrics", fake_llama_metrics)
    r = client.post("/api/updates/restart-slots", json={})
    assert r.json()["restarted"] == ["agent"]
    assert r.json()["skipped_busy"] == []


# ── server-side image-drift restart (#2096) ───────────────────────────────────

from hal0.api.routes import updater as updater_routes  # noqa: E402

_IMG = {"key": "image", "running": "x:0824", "rendered": "x:0826"}
_ARGV = {"key": "--ctx-size", "running": "4096", "rendered": "131072"}


def _no_busy(monkeypatch: pytest.MonkeyPatch, busy_ports: set[int] | None = None) -> None:
    busy_ports = busy_ports or set()

    async def fake(port: int) -> dict[str, Any]:
        return {"requests_processing": 1 if port in busy_ports else 0}

    monkeypatch.setattr("hal0.slots.metrics_collect.llama_metrics", fake)


@pytest.mark.asyncio
async def test_shared_restart_function_matches_route_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_busy(monkeypatch, {2})
    sm = _StubSlotManager(
        {
            "a": {"drifted": True, "diffs": [_IMG]},
            "b": {"drifted": True, "diffs": [_IMG]},
        },
        restart_error={},
    )
    out = await updater_routes.restart_drifted(sm, only={"a", "b"}, skip_busy=True)
    assert out == {"restarted": ["a"], "failed": [], "skipped_busy": ["b"], "count": 1}


@pytest.mark.asyncio
async def test_post_start_restart_only_image_drift_and_stores_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_busy(monkeypatch, {3})
    sm = _StubSlotManager(
        {
            "img": {"drifted": True, "diffs": [_IMG]},
            "argv": {"drifted": True, "diffs": [_ARGV]},
            "busy": {"drifted": True, "diffs": [_IMG]},
        }
    )
    state = SimpleNamespace(slot_manager=sm)
    await updater_routes.post_start_image_drift_restart(state, enabled=True, settle_s=0)
    assert sm.restarted == ["img"]
    res = state.post_start_image_drift_restart
    assert res["restarted"] == ["img"]
    assert res["skipped_busy"] == ["busy"]
    assert res["failed"] == []
    assert res["at"]


@pytest.mark.asyncio
async def test_post_start_restart_disabled_by_config_does_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_busy(monkeypatch)
    sm = _StubSlotManager({"img": {"drifted": True, "diffs": [_IMG]}})
    state = SimpleNamespace(slot_manager=sm)
    await updater_routes.post_start_image_drift_restart(state, enabled=False, settle_s=0)
    assert sm.restarted == []
    assert getattr(state, "post_start_image_drift_restart", None) is None


@pytest.mark.asyncio
async def test_post_start_restart_nothing_qualifies_stores_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_busy(monkeypatch)
    sm = _StubSlotManager({"argv": {"drifted": True, "diffs": [_ARGV]}})
    state = SimpleNamespace(slot_manager=sm)
    await updater_routes.post_start_image_drift_restart(state, enabled=True, settle_s=0)
    assert sm.restarted == []
    assert getattr(state, "post_start_image_drift_restart", None) is None


def test_slot_drift_route_exposes_auto_restart(client: TestClient) -> None:
    _install_sm(client, _StubSlotManager({}))
    assert client.get("/api/updates/slot-drift").json()["auto_restart"] is None
    stored = {
        "restarted": ["brain"],
        "skipped_busy": [],
        "failed": [],
        "at": "2026-10-08T00:00:00+00:00",
    }
    client.app.state.post_start_image_drift_restart = stored
    assert client.get("/api/updates/slot-drift").json()["auto_restart"] == stored


# ── #2096 review: the two restart passes must not overlap ────────────────────
#
# The CLI polls /slot-drift and POSTs /restart-slots as soon as hal0-api is
# back, while the post-start pass is still settling or restarting. Both go
# through restart_drifted(); a slot restarted by the first must not be bounced
# again by the second, and the two must not interleave unload/load on it.


@pytest.mark.asyncio
async def test_restart_drifted_serialises_concurrent_callers_and_does_not_double_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hal0.api.routes import updater as upd

    drifted_names = {"brain"}
    restarts: list[str] = []
    in_restart = 0
    max_overlap = 0

    class _SM:
        async def restart(self, name: str) -> None:
            nonlocal in_restart, max_overlap
            in_restart += 1
            max_overlap = max(max_overlap, in_restart)
            await asyncio.sleep(0.02)
            restarts.append(name)
            drifted_names.discard(name)  # the restart cleared the drift
            in_restart -= 1

    async def _drift(_sm):
        return [
            {"slot": n, "diffs": [{"key": "image", "running": "a", "rendered": "b"}]}
            for n in sorted(drifted_names)
        ]

    monkeypatch.setattr(upd, "_collect_slot_drift", _drift)
    sm = _SM()

    first, second = await asyncio.gather(
        upd.restart_drifted(sm, only={"brain"}),
        upd.restart_drifted(sm, only={"brain"}),
    )

    assert restarts == ["brain"]  # exactly one bounce
    assert max_overlap == 1
    assert sorted([first["count"], second["count"]]) == [0, 1]
