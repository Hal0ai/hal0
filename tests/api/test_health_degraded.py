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


# ── #1461: per-check contract pin ────────────────────────────────────────────
#
# The footer's degraded tooltip (ui/src/api/hooks/useRuntime.ts failingChecks)
# decides what is broken from this payload. It once filtered on a per-check
# `status` string that this route has never emitted, so `undefined !== 'ok'`
# listed EVERY check as failing. Pin the shape the client reads so a future
# change here has to change the client too.


def test_health_system_per_check_shape_is_ok_boolean(client: TestClient) -> None:
    """Every check reports a boolean ``ok`` and NO per-check ``status``."""
    client.app.state.slot_manager = _FakeSM([_slot("chat", SlotState.READY)])
    body = client.get("/api/health/system").json()

    assert body["status"] in ("ok", "degraded")
    checks = body["checks"]
    # The full check set the route composes.
    assert {"disk_state", "disk_config", "slot_manager", "event_bus", "mcp_mount"} <= set(checks)

    for name, check in checks.items():
        assert isinstance(check, dict), name
        assert isinstance(check.get("ok"), bool), f"{name} must carry a boolean 'ok'"
        # No per-check 'status' — the client must not look for one.
        assert "status" not in check, f"{name} unexpectedly grew a 'status' field"
        # `detail`, when present, is a string the tooltip can render verbatim.
        if "detail" in check:
            assert isinstance(check["detail"], str), name


def test_health_system_failure_reasons_are_machine_readable(client: TestClient) -> None:
    """A failing check carries its reason in ``detail`` and/or ``errored``.

    slot_manager's live failure reports through ``errored`` (a list of slot
    names) and never sets ``detail`` — the tooltip has to read both.
    """
    client.app.state.slot_manager = _FakeSM([_slot("flm", SlotState.ERROR)])
    checks = client.get("/api/health/system").json()["checks"]

    sm = checks["slot_manager"]
    assert sm["ok"] is False
    assert sm["errored"] == ["flm"]
    assert "detail" not in sm

    # mcp_mount is the `detail`-bearing failure path.
    mcp = checks["mcp_mount"]
    if mcp["ok"] is False:
        assert isinstance(mcp["detail"], str) and mcp["detail"]
