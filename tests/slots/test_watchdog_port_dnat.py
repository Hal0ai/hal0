"""#1820 / #3724 — the slot watchdog classifies and repairs DNAT black holes.

At the /health strike threshold on an active unit, the watchdog audits the
slot's published port before parking it:

* missing rule + ``[slots].port_dnat_auto_repair`` on -> one rate-limited
  ``podman network reload --all``, re-probe, stay READY when it answers;
* still unreachable, repair off, or rate-limited -> ERROR with
  ``error_reason = "port_dnat_missing"``;
* stale rule -> ERROR with ``error_reason = "port_dnat_stale"``, never repaired.

The audit and the reload are faked at :mod:`hal0.slots.port_dnat`; the real
watchdog loop and SlotManager transitions run.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from hal0.slots import port_dnat
from hal0.slots import watchdog as wd_mod
from hal0.slots.manager import SlotManager
from hal0.slots.state import SlotState
from hal0.system import netavark
from hal0.system.netavark import (
    BACKEND_IPTABLES,
    DnatRule,
    DnatSnapshot,
    LiveContainer,
    PortVerdict,
    ReloadGate,
    ReloadResult,
)
from tests.slots.conftest import FakeContainerProvider

OWNER = LiveContainer("hal0-slot-chat", frozenset({"10.88.0.70"}), frozenset({8081}))
LIVE = frozenset({"10.88.0.70"})


def _rule(target: str) -> DnatRule:
    return DnatRule(
        chain="NETAVARK-DN-X", dport=8081, target_ip=target, target_port=8081, handle=None
    )


MISSING = PortVerdict(port=8081, rules=(), live_ips=LIVE, owner=OWNER)
STALE = PortVerdict(
    port=8081, rules=(_rule("10.88.0.9"), _rule("10.88.0.70")), live_ips=LIVE, owner=OWNER
)
CLEAN = PortVerdict(port=8081, rules=(_rule("10.88.0.70"),), live_ips=LIVE, owner=OWNER)


@pytest.fixture(autouse=True)
def fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wd_mod, "_FAIL_WATCH_INTERVAL_S", 0.1)
    monkeypatch.setattr(wd_mod, "_DNAT_REPROBE_DELAY_S", 0.05)


@pytest.fixture
def audit(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Script the port audit and record reloads."""
    state: dict[str, Any] = {
        "verdict": None,
        "reloads": [],
        "reload_result": ReloadResult(True, True),
    }
    monkeypatch.setattr(port_dnat, "audit_port", lambda port: state["verdict"])

    def reload(trigger: str) -> ReloadResult:
        state["reloads"].append(trigger)
        hook = state.get("on_reload")
        if hook:
            hook()
        return state["reload_result"]

    monkeypatch.setattr(port_dnat, "try_reload", reload)
    return state


async def _settle(sm: SlotManager, target: SlotState, timeout_s: float = 5.0) -> SlotState:
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        rec = sm._states.get(sm._key("chat"))
        if rec is not None and rec.state == target:
            return rec.state
        await asyncio.sleep(0.05)
    rec = sm._states.get(sm._key("chat"))
    return rec.state if rec is not None else SlotState.OFFLINE


async def test_missing_rule_is_repaired_by_one_reload_and_the_slot_stays_ready(
    slot_root: Any, container_stub: FakeContainerProvider, audit: dict[str, Any]
) -> None:
    sm = SlotManager()
    await sm.load("chat")
    audit["verdict"] = MISSING
    audit["on_reload"] = lambda: setattr(container_stub, "healthy", True)
    container_stub.healthy = False
    for _ in range(60):
        if audit["reloads"]:
            break
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.4)
    assert audit["reloads"] == ["slot chat port 8081"]
    rec = sm._states[sm._key("chat")]
    assert rec.state == SlotState.READY
    assert "error_reason" not in rec.extra


async def test_still_unreachable_after_reload_parks_port_dnat_missing(
    slot_root: Any, container_stub: FakeContainerProvider, audit: dict[str, Any]
) -> None:
    sm = SlotManager()
    await sm.load("chat")
    audit["verdict"] = MISSING
    container_stub.healthy = False
    assert await _settle(sm, SlotState.ERROR) == SlotState.ERROR
    rec = sm._states[sm._key("chat")]
    assert rec.extra["error_reason"] == "port_dnat_missing"
    assert rec.extra["dnat_port"] == 8081
    assert "still unreachable after `podman network reload --all`" in rec.message
    assert len(audit["reloads"]) == 1


async def test_rate_limited_reload_parks_port_dnat_missing_without_reloading(
    slot_root: Any, container_stub: FakeContainerProvider, audit: dict[str, Any]
) -> None:
    sm = SlotManager()
    await sm.load("chat")
    audit["verdict"] = MISSING
    audit["reload_result"] = ReloadResult(attempted=False, ok=False, detail="rate-limited")
    container_stub.healthy = False
    assert await _settle(sm, SlotState.ERROR) == SlotState.ERROR
    rec = sm._states[sm._key("chat")]
    assert rec.extra["error_reason"] == "port_dnat_missing"
    assert "rate-limited" in rec.message
    assert "hal0 doctor ports --fix" in rec.message


async def test_toggle_off_never_reloads(
    slot_root: Any, container_stub: FakeContainerProvider, audit: dict[str, Any]
) -> None:
    sm = SlotManager(port_dnat_auto_repair=False)
    await sm.load("chat")
    audit["verdict"] = MISSING
    container_stub.healthy = False
    assert await _settle(sm, SlotState.ERROR) == SlotState.ERROR
    rec = sm._states[sm._key("chat")]
    assert rec.extra["error_reason"] == "port_dnat_missing"
    assert "auto-repair off" in rec.message
    assert audit["reloads"] == []


async def test_stale_rule_is_surfaced_and_never_repaired(
    slot_root: Any, container_stub: FakeContainerProvider, audit: dict[str, Any]
) -> None:
    sm = SlotManager()
    await sm.load("chat")
    audit["verdict"] = STALE
    container_stub.healthy = False
    assert await _settle(sm, SlotState.ERROR) == SlotState.ERROR
    rec = sm._states[sm._key("chat")]
    assert rec.extra["error_reason"] == "port_dnat_stale"
    assert "stale DNAT rule" in rec.message
    assert audit["reloads"] == []


@pytest.mark.parametrize("verdict", [None, CLEAN])
async def test_unknowable_or_clean_port_keeps_the_generic_error(
    slot_root: Any,
    container_stub: FakeContainerProvider,
    audit: dict[str, Any],
    verdict: PortVerdict | None,
) -> None:
    sm = SlotManager()
    await sm.load("chat")
    audit["verdict"] = verdict
    container_stub.healthy = False
    assert await _settle(sm, SlotState.ERROR) == SlotState.ERROR
    rec = sm._states[sm._key("chat")]
    assert rec.message == "model server failed /health probe"
    assert "error_reason" not in rec.extra
    assert audit["reloads"] == []


async def test_error_reason_is_retired_by_the_next_transition(
    slot_root: Any, container_stub: FakeContainerProvider
) -> None:
    sm = SlotManager()
    await sm.load("chat")
    await sm._transition(
        "chat",
        SlotState.ERROR,
        message="x",
        extra={"error_reason": "port_dnat_missing", "dnat_port": 8081},
        force=True,
    )
    await sm._transition("chat", SlotState.OFFLINE, force=True)
    rec = sm._states[sm._key("chat")]
    assert "error_reason" not in rec.extra and "dnat_port" not in rec.extra


# ── the post-recreate recheck (#3724 item 3) ─────────────────────────────────


async def test_slot_start_schedules_a_box_wide_recheck(
    slot_root: Any, container_stub: FakeContainerProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(port_dnat, "recheck_after_recreate", lambda trigger: calls.append(trigger))
    sm = SlotManager()
    await sm.load("chat")
    for _ in range(40):
        if calls:
            break
        await asyncio.sleep(0.05)
    assert calls == ["slot chat started"]


async def test_toggle_off_skips_the_recheck(
    slot_root: Any, container_stub: FakeContainerProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(port_dnat, "recheck_after_recreate", lambda trigger: calls.append(trigger))
    sm = SlotManager(port_dnat_auto_repair=False)
    await sm.load("chat")
    await asyncio.sleep(0.3)
    assert calls == []


def _snapshot(rules: tuple[DnatRule, ...]) -> DnatSnapshot:
    return DnatSnapshot(BACKEND_IPTABLES, rules, (OWNER,))


class _ReloadSeam:
    def __init__(self) -> None:
        self.reloads = 0

    def network_reload(self) -> None:
        self.reloads += 1


def test_recheck_reloads_only_when_a_rule_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    seam = _ReloadSeam()
    monkeypatch.setattr("hal0.system.seam.SystemCtlSeam", lambda: seam)
    monkeypatch.setattr(netavark, "RELOAD_GATE", ReloadGate(min_interval_s=600, clock=lambda: 0.0))

    monkeypatch.setattr(port_dnat, "_read_snapshot", lambda: _snapshot((_rule("10.88.0.70"),)))
    assert port_dnat.recheck_after_recreate("t") is None
    assert seam.reloads == 0

    monkeypatch.setattr(port_dnat, "_read_snapshot", lambda: _snapshot(()))
    result = port_dnat.recheck_after_recreate("t")
    assert result is not None and result.ok
    assert seam.reloads == 1


def test_automatic_reloads_are_rate_limited_box_wide(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two slots failing (or a slot failure plus a recheck) inside the window
    share ONE reload."""
    seam = _ReloadSeam()
    now = [0.0]
    monkeypatch.setattr("hal0.system.seam.SystemCtlSeam", lambda: seam)
    monkeypatch.setattr(
        netavark, "RELOAD_GATE", ReloadGate(min_interval_s=600, clock=lambda: now[0])
    )
    monkeypatch.setattr(port_dnat, "_read_snapshot", lambda: _snapshot(()))

    assert port_dnat.try_reload("slot a").ok
    assert port_dnat.try_reload("slot b").detail == "rate-limited"
    assert port_dnat.recheck_after_recreate("slot c started").detail == "rate-limited"
    assert seam.reloads == 1
    now[0] = 601.0
    assert port_dnat.try_reload("slot a").ok
    assert seam.reloads == 2


def test_audit_port_is_none_when_the_snapshot_is_unavailable() -> None:
    # The suite-wide stub (tests/conftest.py) makes the snapshot unavailable.
    assert port_dnat.audit_port(8081) is None


def test_openwebui_converge_rechecks_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from hal0.components import runner
    from hal0.config.schema import Hal0Config

    calls: list[str] = []
    monkeypatch.setattr(port_dnat, "recheck_after_recreate", lambda trigger: calls.append(trigger))
    cfg = Hal0Config()
    monkeypatch.setattr("hal0.config.loader.load_hal0_config", lambda: cfg)
    runner._recheck_port_dnat("component openwebui converged")
    assert calls == ["component openwebui converged"]

    cfg.slots.port_dnat_auto_repair = False
    runner._recheck_port_dnat("again")
    assert calls == ["component openwebui converged"]
