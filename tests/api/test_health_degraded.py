"""B2: /api/health/system must report degraded when a slot is in ERROR.

Previously the slot_manager check reported ok=True regardless of slot state,
so a systemd-FAILED slot still rendered the whole system "ok" — the health
endpoint lied.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from hal0.slots.manager import Slot
from hal0.slots.state import SlotState


class _FakeSM:
    def __init__(self, slots: list[Slot]) -> None:
        self._slots = slots

    async def list(self) -> list[Slot]:
        return self._slots


def _slot(name: str, state: SlotState) -> Slot:
    return Slot(name=name, state=state)


def test_health_system_ok_when_no_errored_slots(client: TestClient) -> None:
    client.app.state.slot_manager = _FakeSM(
        [
            _slot("chat", SlotState.READY),
            _slot("embed", SlotState.OFFLINE),
        ]
    )
    body = client.get("/api/health/system").json()
    assert body["status"] == "ok"
    assert body["checks"]["slot_manager"]["ok"] is True
    assert body["checks"]["slot_manager"]["errored"] == []


def test_health_system_degraded_when_slot_errored(client: TestClient) -> None:
    client.app.state.slot_manager = _FakeSM(
        [
            _slot("chat", SlotState.READY),
            _slot("npu", SlotState.ERROR),
        ]
    )
    body = client.get("/api/health/system").json()
    assert body["status"] == "degraded"
    assert body["checks"]["slot_manager"]["ok"] is False
    assert body["checks"]["slot_manager"]["errored"] == ["npu"]
