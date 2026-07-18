"""SlotSampler.tick() -- fleet row + per-slot rows + missing-sensor grace."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from hal0.metrics.sampler import SlotSampler


class _FakeWriter:
    def __init__(self) -> None:
        self.rows: list[tuple[str, dict[str, Any]]] = []

    def enqueue(self, table: str, row: dict[str, Any]) -> None:
        self.rows.append((table, row))


@dataclass
class _FakeState:
    value: str


@dataclass
class _FakeSlot:
    name: str
    state: _FakeState
    port: int = 8081


class _FakeSlotManager:
    def __init__(self, slots: list[_FakeSlot]) -> None:
        self._slots = slots

    async def list(self) -> list[_FakeSlot]:
        return self._slots


@pytest.fixture
def writer() -> _FakeWriter:
    return _FakeWriter()


class TestSlotSamplerTick:
    @pytest.mark.asyncio
    async def test_writes_fleet_row_even_with_no_slots(self, writer: _FakeWriter) -> None:
        sm = _FakeSlotManager([])
        sampler = SlotSampler(slot_manager=sm, writer=writer, interval_s=5.0)

        with (
            patch(
                "hal0.metrics.sampler.gpu_view.sample",
                return_value=type(
                    "S",
                    (),
                    {
                        "vram_used_mb": None,
                        "gtt_used_mb": None,
                        "gpu_busy": None,
                    },
                )(),
            ),
            patch(
                "hal0.metrics.sampler._probe_power_snapshot",
                new=AsyncMock(return_value={"gpu_power_w": None, "gpu_temp_c": None}),
            ),
        ):
            await sampler.tick()

        assert len(writer.rows) == 1
        table, row = writer.rows[0]
        assert table == "slot_sample"
        assert row["slot_id"] == "__fleet__"
        assert row["vram_bytes"] is None  # missing sensor -> NULL, never 0

    @pytest.mark.asyncio
    async def test_writes_per_slot_rows_and_fleet_row(self, writer: _FakeWriter) -> None:
        slots = [_FakeSlot(name="primary", state=_FakeState("serving"))]
        sm = _FakeSlotManager(slots)
        sampler = SlotSampler(slot_manager=sm, writer=writer, interval_s=5.0)

        gpu_sample = type(
            "S", (), {"vram_used_mb": 1024.0, "gtt_used_mb": 2048.0, "gpu_busy": 0.42}
        )()

        with (
            patch("hal0.metrics.sampler.gpu_view.sample", return_value=gpu_sample),
            patch(
                "hal0.metrics.sampler._probe_power_snapshot",
                new=AsyncMock(return_value={"gpu_power_w": 45.0, "gpu_temp_c": 60.0}),
            ),
            patch(
                "hal0.metrics.sampler._scrape_llama",
                new=AsyncMock(return_value={"requests_processing": 2, "kv_cache_usage": 0.5}),
            ),
            patch(
                "hal0.slots.capacity.build_per_slot",
                new=AsyncMock(return_value={"primary": {"vram_mb": 8000.0, "ram_mb": 500.0}}),
            ),
        ):
            await sampler.tick()

        tables = [t for t, _ in writer.rows]
        assert tables.count("slot_sample") == 2  # fleet + primary
        slot_row = next(
            r for t, r in writer.rows if t == "slot_sample" and r["slot_id"] == "primary"
        )
        assert slot_row["state"] == "serving"
        assert slot_row["vram_bytes"] == int(8000.0 * 1024 * 1024)
        assert slot_row["inflight"] == 2
        assert slot_row["kv_used"] == 500_000

        fleet_row = next(
            r for t, r in writer.rows if t == "slot_sample" and r["slot_id"] == "__fleet__"
        )
        assert fleet_row["gpu_util"] == 0.42
        assert fleet_row["power_w"] == 45.0

    @pytest.mark.asyncio
    async def test_state_transition_emits_slot_event(self, writer: _FakeWriter) -> None:
        slot = _FakeSlot(name="primary", state=_FakeState("offline"))
        sm = _FakeSlotManager([slot])
        sampler = SlotSampler(slot_manager=sm, writer=writer, interval_s=5.0)

        gpu_sample = type("S", (), {"vram_used_mb": None, "gtt_used_mb": None, "gpu_busy": None})()

        with (
            patch("hal0.metrics.sampler.gpu_view.sample", return_value=gpu_sample),
            patch(
                "hal0.metrics.sampler._probe_power_snapshot",
                new=AsyncMock(return_value={"gpu_power_w": None, "gpu_temp_c": None}),
            ),
            patch("hal0.metrics.sampler._scrape_llama", new=AsyncMock(return_value={})),
            patch("hal0.slots.capacity.build_per_slot", new=AsyncMock(return_value={})),
        ):
            await sampler.tick()  # first tick: no prior state, no event
            slot.state = _FakeState("ready")
            await sampler.tick()  # second tick: offline -> ready transition

        events = [r for t, r in writer.rows if t == "slot_event"]
        assert len(events) == 1
        assert events[0]["from_state"] == "offline"
        assert events[0]["to_state"] == "ready"
        assert events[0]["duration_ms"] is None
