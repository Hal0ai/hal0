"""Tests for host-memory-pressure eviction (#903, priority-order spec 2026-08-02).

Covers _pressure_evict_once() and its integration with the idle monitor:

  - Under-floor free RAM triggers priority-ordered eviction (lowest
    ``priority`` first, LRU tie-break within a tier) until RAM ≥ floor.
  - Every non-pinned resident slot is a candidate — the retired ``lru =
    true`` opt-in gate no longer restricts eligibility; the key is
    ignored (deprecation-warned once per slot per process).
  - The canonical ``agent`` slot, and any ``pinned = true`` slot
    regardless of priority, are never evicted even under pressure.
  - A slot serving a request is never evicted even under pressure.
  - Above-floor free RAM is a no-op (no eviction).
  - evict_pressure_mb = 0 disables pressure eviction entirely.

The host-free-RAM probe (_probe_host_free_mb) is monkeypatched in every
test — no real /proc/meminfo reads occur.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from hal0.slots import reaper
from hal0.slots.manager import SlotManager
from hal0.slots.state import SlotState
from tests.slots.conftest import FakeContainerProvider

# ── helpers ───────────────────────────────────────────────────────────────────


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

    ``lru`` is the retired opt-in key — pass it (True or False) to prove
    it's read-but-ignored; leave it ``None`` to omit it entirely, matching
    a freshly authored TOML that never had it. ``priority``/``pinned`` map
    directly to the current eviction-order fields.
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


def _patch_free_mb(monkeypatch: pytest.MonkeyPatch, sm: SlotManager, value: float) -> None:
    """Monkeypatch _probe_host_free_mb to return a fixed value."""
    monkeypatch.setattr(sm, "_probe_host_free_mb", lambda: value)


@pytest.fixture(autouse=True)
def _reset_lru_deprecation_state() -> None:
    """``reaper._lru_flag_warned`` is module-level, one-shot-per-process.

    Several tests in this file reuse slot names (``rerank``, etc.) and
    assert on the deprecation warning's fire-once behavior — without a
    reset, whichever test runs first would "use up" the warning for every
    test after it in the same pytest process.
    """
    reaper._lru_flag_warned.clear()
    yield
    reaper._lru_flag_warned.clear()


# ── above-floor: no-op ────────────────────────────────────────────────────────


async def test_pressure_evict_noop_when_above_floor(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When free RAM ≥ evict_pressure_mb, pressure eviction does nothing."""
    _write_slot(slot_root, "rerank", port=8090, lru=True)
    sm = SlotManager(evict_pressure_mb=4096.0)
    await sm.load("rerank")
    sm._last_used[sm._key("rerank")] = 0.0  # ancient — would be evicted if pressure fired

    # Free RAM is above the floor → no eviction.
    _patch_free_mb(monkeypatch, sm, 8192.0)
    await sm._pressure_evict_once()

    assert (await sm.status("rerank")).state == SlotState.READY
    assert not container_stub.unload_calls


# ── disabled: evict_pressure_mb = 0 ──────────────────────────────────────────


async def test_pressure_evict_disabled_when_floor_is_zero(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """evict_pressure_mb = 0 disables pressure eviction entirely."""
    _write_slot(slot_root, "rerank", port=8090, lru=True)
    sm = SlotManager(evict_pressure_mb=0.0)
    await sm.load("rerank")
    sm._last_used[sm._key("rerank")] = 0.0

    # Even with zero free RAM, pressure eviction is disabled.
    _patch_free_mb(monkeypatch, sm, 0.0)
    await sm._pressure_evict_once()

    assert (await sm.status("rerank")).state == SlotState.READY
    assert not container_stub.unload_calls


# ── lru gate retired: every non-pinned slot is now evictable ────────────────


async def test_non_lru_slot_now_evictable_priority_order(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slot with no ``lru`` key at all is evictable under pressure (spec
    2026-08-02): eligibility is priority-ordered, not lru-opt-in gated."""
    _write_slot(slot_root, "rerank", port=8090)  # no lru key, no priority override
    sm = SlotManager(evict_pressure_mb=8192.0)
    await sm.load("rerank")
    sm._last_used[sm._key("rerank")] = 0.0

    # Free RAM well below floor → pressure fires and rerank is now eligible.
    _patch_free_mb(monkeypatch, sm, 512.0)
    await sm._pressure_evict_once()

    assert (await sm.status("rerank")).state == SlotState.OFFLINE
    assert any(c.get("name") == "rerank" for c in container_stub.unload_calls)


# ── agent never evicted ───────────────────────────────────────────────────────


async def test_pressure_evict_never_evicts_agent(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The canonical ``agent`` slot is never evicted under pressure (#903)."""
    # Write agent with lru=true — the guard must reject it anyway.
    _write_slot(slot_root, "agent", port=8095, lru=True)
    sm = SlotManager(evict_pressure_mb=8192.0)
    await sm.load("agent")
    sm._last_used[sm._key("agent")] = 0.0  # ancient

    _patch_free_mb(monkeypatch, sm, 512.0)  # well below floor
    await sm._pressure_evict_once()

    # agent must remain loaded.
    assert (await sm.status("agent")).state in (SlotState.READY, SlotState.IDLE)
    assert not container_stub.unload_calls


# ── serving slot never evicted ────────────────────────────────────────────────


async def test_pressure_evict_skips_serving_slot(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slot mid-request is never evicted even when free RAM is critically low."""
    _write_slot(slot_root, "rerank", port=8090, lru=True)
    sm = SlotManager(evict_pressure_mb=8192.0)
    await sm.load("rerank")

    _patch_free_mb(monkeypatch, sm, 256.0)  # far below floor

    async with sm.serving("rerank"):
        sm._last_used[sm._key("rerank")] = 0.0  # ancient — but serving_count > 0
        await sm._pressure_evict_once()
        assert (await sm.status("rerank")).state == SlotState.SERVING

    assert not container_stub.unload_calls


# ── under-floor: lru-eligible slot evicted ────────────────────────────────────


async def test_pressure_evict_unloads_lru_slot_under_floor(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When free RAM < floor, an idle lru-eligible slot is unloaded."""
    _write_slot(slot_root, "rerank", port=8090, lru=True)
    sm = SlotManager(evict_pressure_mb=8192.0)
    await sm.load("rerank")
    sm._last_used[sm._key("rerank")] = 0.0

    _patch_free_mb(monkeypatch, sm, 1024.0)  # below floor
    await sm._pressure_evict_once()

    assert (await sm.status("rerank")).state == SlotState.OFFLINE
    assert any(c.get("name") == "rerank" for c in container_stub.unload_calls)


async def test_pressure_evict_noop_when_probe_fails(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed host-RAM probe is fail-SAFE: it reports ``inf`` free RAM so the
    floor check short-circuits and nothing is evicted — rather than evicting
    blindly on a bad reading (regression guard for the 0.0 fail-open bug)."""
    _write_slot(slot_root, "rerank", port=8090, lru=True)
    sm = SlotManager(evict_pressure_mb=8192.0)
    await sm.load("rerank")
    sm._last_used[sm._key("rerank")] = 0.0

    def _boom() -> tuple[float, float]:
        raise OSError("cannot read /proc/meminfo")

    # Local import inside _probe_host_free_mb resolves this name at call time.
    monkeypatch.setattr("hal0.slots.capacity._read_meminfo", _boom)
    assert sm._probe_host_free_mb() == float("inf")

    await sm._pressure_evict_once()

    # Slot survives — a probe failure must never trigger eviction.
    assert (await sm.status("rerank")).state != SlotState.OFFLINE
    assert not any(c.get("name") == "rerank" for c in container_stub.unload_calls)


# ── LRU ordering ─────────────────────────────────────────────────────────────


async def test_pressure_evict_lru_order(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When multiple lru-eligible slots exist, oldest last_used is evicted first.

    The probe returns above-floor after the FIRST eviction so only one slot
    is evicted, letting us verify the order.
    """
    _write_slot(slot_root, "rerank", port=8090, lru=True)
    _write_slot(slot_root, "embed", port=8091, lru=True)
    sm = SlotManager(evict_pressure_mb=4096.0)
    await sm.load("rerank")
    await sm.load("embed")

    # embed is older (LRU) → must be evicted first.
    sm._last_used[sm._key("rerank")] = 1000.0  # newer
    sm._last_used[sm._key("embed")] = 100.0  # older → evicted first

    evicted: list[str] = []

    # Probe returns below-floor the first time, above-floor after first eviction.
    call_count = 0

    def _probe() -> float:
        nonlocal call_count
        call_count += 1
        # First call: under pressure; subsequent calls: relieved.
        return 512.0 if call_count == 1 else 8192.0

    original_unload = sm.unload

    async def _tracking_unload(name: str) -> object:
        evicted.append(name)
        return await original_unload(name)

    monkeypatch.setattr(sm, "_probe_host_free_mb", _probe)
    monkeypatch.setattr(sm, "unload", _tracking_unload)

    await sm._pressure_evict_once()

    assert evicted == ["embed"], f"oldest slot (embed) must be evicted first; got {evicted}"


# ── stops evicting once floor is met ─────────────────────────────────────────


async def test_pressure_evict_stops_when_floor_met(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Eviction stops as soon as free RAM recovers above the floor.

    Three lru-eligible slots; the probe returns above-floor after the
    first eviction, so exactly one eviction occurs.
    """
    for name, port in [("rerank", 8090), ("embed", 8091), ("stt", 8092)]:
        _write_slot(slot_root, name, port=port, lru=True)

    sm = SlotManager(evict_pressure_mb=4096.0)
    for name in ("rerank", "embed", "stt"):
        await sm.load(name)

    sm._last_used[sm._key("stt")] = 50.0  # oldest
    sm._last_used[sm._key("embed")] = 200.0
    sm._last_used[sm._key("rerank")] = 500.0  # newest

    call_count = 0

    def _probe() -> float:
        nonlocal call_count
        call_count += 1
        return 512.0 if call_count <= 1 else 9999.0

    monkeypatch.setattr(sm, "_probe_host_free_mb", _probe)
    await sm._pressure_evict_once()

    # Only the oldest slot (stt) should have been evicted.
    states = {n: (await sm.status(n)).state for n in ("rerank", "embed", "stt")}
    assert states["stt"] == SlotState.OFFLINE
    assert states["embed"] in (SlotState.READY, SlotState.IDLE)
    assert states["rerank"] in (SlotState.READY, SlotState.IDLE)


# ── priority ordering (spec 2026-08-02) ──────────────────────────────────────


async def test_pressure_evicts_lowest_priority_first(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three resident slots, equal idle age, priorities 10/50/90 → eviction
    order is the priority order, not LRU."""
    _write_slot(slot_root, "cheap", port=8090, priority=10)
    _write_slot(slot_root, "mid", port=8091, priority=50)
    _write_slot(slot_root, "keeper", port=8092, priority=90)
    sm = SlotManager(evict_pressure_mb=4096.0)
    for name in ("cheap", "mid", "keeper"):
        await sm.load(name)
        sm._last_used[sm._key(name)] = 1000.0  # identical idle age

    evicted: list[str] = []
    original_unload = sm.unload

    async def _tracking_unload(name: str) -> object:
        evicted.append(name)
        return await original_unload(name)

    call_count = 0

    def _probe() -> float:
        nonlocal call_count
        call_count += 1
        # Under floor until two evictions have happened, then relieved.
        return 512.0 if call_count <= 2 else 8192.0

    monkeypatch.setattr(sm, "_probe_host_free_mb", _probe)
    monkeypatch.setattr(sm, "unload", _tracking_unload)

    await sm._pressure_evict_once()

    assert evicted == ["cheap", "mid"], f"expected priority order cheap,mid; got {evicted}"
    assert (await sm.status("keeper")).state == SlotState.READY
    assert not any(c.get("name") == "keeper" for c in container_stub.unload_calls)


async def test_pressure_priority_tie_breaks_lru(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Equal priority → oldest last_used evicted first (LRU tie-break kept
    within a priority tier)."""
    _write_slot(slot_root, "rerank", port=8090, priority=50)
    _write_slot(slot_root, "embed", port=8091, priority=50)
    sm = SlotManager(evict_pressure_mb=4096.0)
    await sm.load("rerank")
    await sm.load("embed")
    sm._last_used[sm._key("rerank")] = 1000.0  # newer
    sm._last_used[sm._key("embed")] = 100.0  # older → evicted first

    evicted: list[str] = []
    original_unload = sm.unload

    async def _tracking_unload(name: str) -> object:
        evicted.append(name)
        return await original_unload(name)

    call_count = 0

    def _probe() -> float:
        nonlocal call_count
        call_count += 1
        return 512.0 if call_count == 1 else 8192.0

    monkeypatch.setattr(sm, "_probe_host_free_mb", _probe)
    monkeypatch.setattr(sm, "unload", _tracking_unload)

    await sm._pressure_evict_once()

    assert evicted == ["embed"], f"oldest within the tied priority tier evicts first; got {evicted}"


async def test_pressure_skips_pinned_regardless_of_priority(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pinned=true + priority=0 is still never evicted."""
    _write_slot(slot_root, "rerank", port=8090, priority=0, pinned=True)
    sm = SlotManager(evict_pressure_mb=8192.0)
    await sm.load("rerank")
    sm._last_used[sm._key("rerank")] = 0.0  # ancient AND lowest priority

    _patch_free_mb(monkeypatch, sm, 512.0)  # well below floor
    await sm._pressure_evict_once()

    assert (await sm.status("rerank")).state in (SlotState.READY, SlotState.IDLE)
    assert not container_stub.unload_calls


async def test_lru_key_ignored_and_warned_once(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A cfg carrying lru=false is evictable anyway; slot.lru_flag_deprecated
    is logged exactly once across two sweeps (one-shot per slot per process)."""
    _write_slot(slot_root, "rerank", port=8090, lru=False)
    sm = SlotManager(evict_pressure_mb=8192.0)
    await sm.load("rerank")
    sm._last_used[sm._key("rerank")] = 0.0

    _patch_free_mb(monkeypatch, sm, 512.0)
    with caplog.at_level(logging.WARNING, logger="hal0.slots.reaper"):
        await sm._pressure_evict_once()
        assert (await sm.status("rerank")).state == SlotState.OFFLINE

        # Reload and sweep again — the warning must not fire a second time.
        await sm.load("rerank")
        sm._last_used[sm._key("rerank")] = 0.0
        await sm._pressure_evict_once()
        assert (await sm.status("rerank")).state == SlotState.OFFLINE

    warnings = [r for r in caplog.records if r.message == "slot.lru_flag_deprecated"]
    assert len(warnings) == 1, f"expected exactly one deprecation warning; got {len(warnings)}"
    assert len(container_stub.unload_calls) == 2  # evicted both sweeps despite lru=false
