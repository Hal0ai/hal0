from __future__ import annotations

import asyncio

import pytest

from hal0.slots.arbiter import GpuArbiter


class _FakeSlot:
    def __init__(self, name, group, loaded):
        self.name = name
        self._group = group
        self.loaded = loaded


class _FakeManager:
    """Minimal manager: known llm slots + records unload() calls."""

    def __init__(self):
        self.unloaded: list[str] = []

    async def unload(self, slot_name: str):
        self.unloaded.append(slot_name)
        return slot_name


@pytest.fixture
def arbiter(tmp_path, monkeypatch):
    monkeypatch.setenv("HAL0_GPU_LLM_RESERVE_GB", "33")
    monkeypatch.setenv("HAL0_GPU_GTT_MARGIN_GB", "6")
    monkeypatch.setenv("HAL0_GPU_EVICT_PRIORITY", "utility,chat")
    arb = GpuArbiter(_FakeManager(), state_path=tmp_path / "gpu.json")
    # Force a deterministic budget for the test.
    monkeypatch.setattr(arb, "_gtt_ceil_gb", lambda: 96.0)
    monkeypatch.setattr(arb, "_loaded_llm_footprints", lambda: {"agent": 18.0, "chat": 15.0})
    return arb


def test_plan_admission_coexist(arbiter):
    d = arbiter.plan_admission(8.0)
    assert d.decision == "coexist"


def test_plan_admission_evicts_chat_first(arbiter):
    d = arbiter.plan_admission(65.0)
    assert d.decision == "needs_exclusive"
    assert d.evict_plan == ("chat",)


def test_evict_to_fit_calls_unload_agent_last(arbiter):
    freed = asyncio.run(arbiter.evict_to_fit(("chat", "agent")))
    assert freed == ["chat", "agent"]
    assert arbiter._manager.unloaded == ["chat", "agent"]
