"""Tests for pre-load eviction (§O26): freeing memory synchronously before a
load, instead of waiting for the reactive pressure sweeper to react.

Two layers, matching hal0.slots.preload_evict's split:

  - ``select_eviction_order()`` — the pure planner. No I/O, no SlotManager;
    exercises the full policy matrix (fits-without-eviction,
    fits-after-evicting-N in LRU order, cannot-fit, never-evicts-protected)
    directly against plain dataclasses.
  - A couple of ``SlotManager.load()`` integration tests (mirroring
    tests/slots/test_pressure_eviction.py's fixture style) proving the
    policy is actually wired into the real load path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hal0.slots import reaper
from hal0.slots.manager import SlotManager
from hal0.slots.preload_evict import (
    CandidateSlot,
    PreloadEvictionFailed,
    _gather_candidates,
    select_eviction_order,
)
from hal0.slots.state import SlotState
from tests.slots.conftest import FakeContainerProvider


@pytest.fixture(autouse=True)
def _reset_lru_deprecation_state() -> None:
    """``reaper._lru_flag_warned`` is module-level, one-shot-per-process.

    Shared with ``hal0.slots.reaper`` (see
    ``tests/slots/test_pressure_eviction.py``'s identical fixture) — this
    file writes slots with a bare/omitted ``lru`` key too, so without a
    reset whichever test runs first (in this file OR the reaper test file,
    same pytest process) would "use up" the one-shot warning for everyone
    after it.
    """
    reaper._lru_flag_warned.clear()
    yield
    reaper._lru_flag_warned.clear()


# ── pure planner: select_eviction_order() ────────────────────────────────────


def test_select_no_eviction_when_already_fits() -> None:
    """Free memory already covers needed + headroom: nothing is selected."""
    candidates = [
        CandidateSlot(name="rerank", last_used=100.0, footprint_mb=4000.0, eligible=True),
    ]
    plan = select_eviction_order(candidates, needed_mb=2000.0, headroom_mb=500.0, free_mb=4000.0)
    assert plan.selected == ()
    assert plan.fits is True
    assert plan.projected_free_mb == 4000.0


def test_select_evicts_in_lru_order_until_it_fits() -> None:
    """Oldest last_used is evicted first; stops the moment it fits."""
    candidates = [
        CandidateSlot(name="rerank", last_used=500.0, footprint_mb=2000.0, eligible=True),
        CandidateSlot(name="embed", last_used=100.0, footprint_mb=2000.0, eligible=True),  # oldest
        CandidateSlot(name="stt", last_used=300.0, footprint_mb=2000.0, eligible=True),
    ]
    # free=500 vs target=3500 -> one eviction (2500) isn't enough,
    # two evictions (4500) clears it.
    plan = select_eviction_order(candidates, needed_mb=3000.0, headroom_mb=500.0, free_mb=500.0)
    assert [c.name for c in plan.selected] == ["embed", "stt"]
    assert plan.fits is True
    assert plan.projected_free_mb == 500.0 + 2000.0 + 2000.0


def test_select_cannot_fit_reports_shortfall() -> None:
    """Evicting every eligible candidate still isn't enough: fits=False."""
    candidates = [
        CandidateSlot(name="embed", last_used=100.0, footprint_mb=500.0, eligible=True),
    ]
    plan = select_eviction_order(candidates, needed_mb=8000.0, headroom_mb=1000.0, free_mb=100.0)
    assert [c.name for c in plan.selected] == ["embed"]
    assert plan.fits is False
    assert plan.projected_free_mb == 600.0


def test_select_never_evicts_ineligible_candidates() -> None:
    """A pinned/serving/not-resident candidate is never selected — even when
    it is the oldest AND the largest — because the planner itself enforces
    eligibility, not just whatever gathered the candidate list."""
    candidates = [
        CandidateSlot(
            name="agent", last_used=1.0, footprint_mb=50_000.0, eligible=False, reason="pinned"
        ),
        CandidateSlot(name="embed", last_used=200.0, footprint_mb=1000.0, eligible=True),
    ]
    plan = select_eviction_order(candidates, needed_mb=500.0, headroom_mb=100.0, free_mb=0.0)
    assert [c.name for c in plan.selected] == ["embed"]
    assert plan.fits is True


def test_select_insufficient_when_only_ineligible_candidates_exist() -> None:
    """No eligible candidates at all -> nothing selected, never fits."""
    candidates = [
        CandidateSlot(name="agent", last_used=1.0, footprint_mb=50_000.0, eligible=False),
    ]
    plan = select_eviction_order(candidates, needed_mb=500.0, headroom_mb=100.0, free_mb=0.0)
    assert plan.selected == ()
    assert plan.fits is False


def test_select_orders_by_priority_then_lru() -> None:
    """priority beats recency: an old high-priority slot survives while a
    newer low-priority one is selected first."""
    cands = [
        CandidateSlot(name="hi", last_used=100.0, footprint_mb=4000.0, eligible=True, priority=90),
        CandidateSlot(
            name="lo-new", last_used=900.0, footprint_mb=4000.0, eligible=True, priority=10
        ),
        CandidateSlot(
            name="lo-old", last_used=100.0, footprint_mb=4000.0, eligible=True, priority=10
        ),
    ]
    plan = select_eviction_order(cands, needed_mb=7000.0, headroom_mb=1000.0, free_mb=0.0)
    assert [c.name for c in plan.selected] == ["lo-old", "lo-new"]
    assert plan.fits is True


def test_select_ineligible_never_selected_regardless_of_priority() -> None:
    cands = [
        CandidateSlot(
            name="pinned",
            last_used=1.0,
            footprint_mb=9000.0,
            eligible=False,
            reason="pinned",
            priority=0,
        ),
        CandidateSlot(name="ok", last_used=2.0, footprint_mb=9000.0, eligible=True, priority=100),
    ]
    plan = select_eviction_order(cands, needed_mb=8000.0, headroom_mb=0.0, free_mb=0.0)
    assert [c.name for c in plan.selected] == ["ok"]


# ── _gather_candidates() ──────────────────────────────────────────────────────


async def test_gather_candidates_no_lru_key_is_eligible_with_priority(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """A slot with no ``lru`` key at all is eligible, priority read from
    cfg (was ``eligible=False, reason="not_lru"`` before the priority
    rework — the retired key no longer gates eligibility)."""
    _write_slot(slot_root, "rerank", port=8090, priority=30)
    sm = SlotManager()
    await sm.load("rerank")

    candidates = await _gather_candidates(sm, exclude="chat")

    assert len(candidates) == 1
    cand = candidates[0]
    assert cand.name == "rerank"
    assert cand.eligible is True
    assert cand.reason == ""
    assert cand.priority == 30


# ── SlotManager.load() integration ───────────────────────────────────────────


def _write_slot(
    root: Path,
    name: str,
    *,
    port: int,
    lru: bool | None = None,
    priority: int | None = None,
    pinned: bool | None = None,
) -> None:
    """Write a minimal slot TOML.

    ``lru`` is the retired opt-in key — pass it to prove it's
    read-but-ignored; omit it (default) to match a freshly authored TOML
    that never had it. ``priority``/``pinned`` map directly to the current
    eviction-order fields (mirrors ``test_pressure_eviction.py``'s helper).
    """
    lines = [
        f'name = "{name}"',
        f"port = {port}",
        'backend = "vulkan"',
        'provider = "llama-server"',
    ]
    if lru is not None:
        lines.append(f"lru = {'true' if lru else 'false'}")
    if priority is not None:
        lines.append(f"priority = {priority}")
    if pinned is not None:
        lines.append(f"pinned = {'true' if pinned else 'false'}")
    lines += ["[model]", 'default = "qwen3-4b-q4_k_m"', ""]
    (root / f"{name}.toml").write_text("\n".join(lines), encoding="utf-8")


def _stub_model_info(size_mb: float) -> Any:
    """Fake SlotManager._resolve_model_info returning a fixed file size."""

    async def _info(model_id: str | None) -> dict[str, Any]:
        return {"size_bytes": int(size_mb * 1024 * 1024)}

    return _info


async def test_load_evicts_idle_lru_slot_to_make_room(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A load that wouldn't otherwise fit evicts the idle, non-pinned
    resident slot first, then proceeds — no reactive-only wait for the
    pressure sweeper. `lru=True` is set on both slots to confirm the
    retired key is tolerated (and ignored) rather than required."""
    _write_slot(slot_root, "rerank", port=8090, lru=True)
    _write_slot(slot_root, "embed", port=8091, lru=True)
    sm = SlotManager(preload_evict_headroom_mb=0.0)
    await sm.load("rerank")
    sm._last_used[sm._key("rerank")] = 0.0  # idle long enough to be evicted

    monkeypatch.setattr(sm, "_resolve_model_info", _stub_model_info(4000.0))
    free_readings = [1000.0, 6000.0]  # short before eviction, plenty after
    monkeypatch.setattr(
        sm, "_probe_host_free_mb", lambda: free_readings.pop(0) if free_readings else 6000.0
    )

    await sm.load("embed")

    assert (await sm.status("embed")).state in (SlotState.READY, SlotState.IDLE)
    assert (await sm.status("rerank")).state == SlotState.OFFLINE
    assert any(c.get("name") == "rerank" for c in container_stub.unload_calls)


async def test_load_fails_clearly_when_nothing_fits(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No eligible candidate exists: the load fails with a clear,
    actionable error — nothing half-loaded, and the resident slot (pinned,
    so never a candidate) is left completely untouched."""
    _write_slot(slot_root, "rerank", port=8090, pinned=True)
    sm = SlotManager(preload_evict_headroom_mb=0.0)
    await sm.load("rerank")

    monkeypatch.setattr(sm, "_resolve_model_info", _stub_model_info(4000.0))
    monkeypatch.setattr(sm, "_probe_host_free_mb", lambda: 100.0)

    with pytest.raises(PreloadEvictionFailed) as excinfo:
        await sm.load("chat")

    assert excinfo.value.details["slot"] == "chat"
    assert excinfo.value.details["evicted"] == []
    assert (await sm.status("chat")).state == SlotState.ERROR
    assert (await sm.status("rerank")).state == SlotState.READY
    assert not container_stub.unload_calls


async def test_load_skips_gate_when_disabled(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """preload_evict_enabled=False: the gate never runs, even when the
    (stubbed) footprint estimate would otherwise be far short."""
    _write_slot(slot_root, "rerank", port=8090, lru=True)
    sm = SlotManager(preload_evict_enabled=False)
    await sm.load("rerank")
    sm._last_used[sm._key("rerank")] = 0.0

    monkeypatch.setattr(sm, "_resolve_model_info", _stub_model_info(999_999.0))
    monkeypatch.setattr(sm, "_probe_host_free_mb", lambda: 1.0)

    await sm.load("chat")

    assert (await sm.status("chat")).state in (SlotState.READY, SlotState.IDLE)
    # rerank was never touched by pre-load eviction (gate disabled).
    assert (await sm.status("rerank")).state in (SlotState.READY, SlotState.IDLE)
    assert not any(c.get("name") == "rerank" for c in container_stub.unload_calls)
