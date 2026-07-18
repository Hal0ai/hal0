"""Tests for host-memory-pressure LRU eviction (#903).

Covers _pressure_evict_once() and its integration with the idle monitor:

  - Under-floor free RAM triggers LRU-ordered eviction until RAM ≥ floor.
  - Non-lru slots are skipped (allow-list gate).
  - The canonical ``agent`` slot is never evicted even under pressure.
  - A slot serving a request is never evicted even under pressure.
  - Above-floor free RAM is a no-op (no eviction).
  - evict_pressure_mb = 0 disables pressure eviction entirely.
  - The LRU ordering evicts oldest last_used first.

The host-free-RAM probe (_probe_host_free_mb) is monkeypatched in every
test — no real /proc/meminfo reads occur.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.slots.manager import SlotManager
from hal0.slots.state import SlotState
from tests.slots.conftest import FakeContainerProvider

# ── helpers ───────────────────────────────────────────────────────────────────


def _write_slot(
    root: Path,
    name: str,
    *,
    port: int,
    lru: bool = False,
) -> None:
    """Write a minimal slot TOML, optionally marking it lru-eligible."""
    lines = [
        f'name = "{name}"',
        f"port = {port}",
        'backend = "vulkan"',
        'provider = "llama-server"',
        "enabled = true",
    ]
    if lru:
        lines.append("lru = true")
    lines += ["[model]", 'default = "qwen3-4b-q4_k_m"', ""]
    (root / f"{name}.toml").write_text("\n".join(lines), encoding="utf-8")


def _patch_free_mb(monkeypatch: pytest.MonkeyPatch, sm: SlotManager, value: float) -> None:
    """Monkeypatch _probe_host_free_mb to return a fixed value."""
    monkeypatch.setattr(sm, "_probe_host_free_mb", lambda: value)


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


# ── allow-list: non-lru slots skipped ────────────────────────────────────────


async def test_pressure_evict_skips_non_lru_slot(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slot without lru=true in its TOML is never evicted under pressure."""
    _write_slot(slot_root, "rerank", port=8090, lru=False)  # NOT lru-eligible
    sm = SlotManager(evict_pressure_mb=8192.0)
    await sm.load("rerank")
    sm._last_used[sm._key("rerank")] = 0.0

    # Free RAM well below floor → pressure fires, but rerank lacks lru=true.
    _patch_free_mb(monkeypatch, sm, 512.0)
    await sm._pressure_evict_once()

    assert (await sm.status("rerank")).state in (SlotState.READY, SlotState.IDLE)
    assert not container_stub.unload_calls


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
