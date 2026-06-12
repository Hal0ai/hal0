"""NPU FLM trio *shadow* handling in SlotManager (shape-consolidation Unit 0).

The NPU runs a single FLM process (the chat anchor, ``device=npu type=llm``)
that also serves transcription/embedding via FLM's ``--asr/--embed`` flags.
The ``stt``/``embed`` slots are therefore **shadows** of that anchor, NOT
independently loadable: a standalone ``/v1/load`` for whisper/embed on the
busy single-tenant NPU returns HTTP 500.

These tests pin:
  - ``is_npu_trio_shadow`` predicate (device=npu AND type in stt/embed;
    the anchor is excluded).
  - ``load()`` short-circuits a shadow without spawning a child or probing
    its port (no 500), while the anchor and GPU/CPU slots still spawn.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.slots.manager import SlotManager, is_npu_trio_shadow
from hal0.slots.state import SlotState


def _write_slot_toml(home: str, name: str, lines: list[str]) -> Path:
    root = Path(home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _anchor(home: str, name: str = "npu") -> None:
    _write_slot_toml(
        home,
        name,
        [
            f'name = "{name}"',
            "port = 8082",
            'device = "npu"',
            'type = "llm"',
            "enabled = true",
            "[model]",
            'default = "gemma3-4b-FLM"',
        ],
    )


def _stt_shadow(home: str, name: str = "stt") -> None:
    _write_slot_toml(
        home,
        name,
        [
            f'name = "{name}"',
            "port = 8084",
            'device = "npu"',
            'type = "transcription"',
            "enabled = true",
            "[model]",
            'default = "whisper-v3"',
        ],
    )


def _gpu_slot(home: str, name: str = "chat") -> None:
    _write_slot_toml(
        home,
        name,
        [
            f'name = "{name}"',
            "port = 8090",
            'device = "gpu-rocm"',
            'type = "llm"',
            "enabled = true",
            "[model]",
            'default = "qwen3-9b"',
        ],
    )


# ── predicate ───────────────────────────────────────────────────────────────


def test_is_npu_trio_shadow_predicate() -> None:
    assert is_npu_trio_shadow({"device": "npu", "type": "transcription"}) is True
    assert is_npu_trio_shadow({"device": "npu", "type": "embedding"}) is True
    # The chat anchor is NOT a shadow — it owns the FLM process.
    assert is_npu_trio_shadow({"device": "npu", "type": "llm"}) is False
    # Non-NPU slots are never shadows.
    assert is_npu_trio_shadow({"device": "gpu-rocm", "type": "transcription"}) is False
    assert is_npu_trio_shadow({"device": "cpu", "type": "embedding"}) is False
    assert is_npu_trio_shadow({"device": "npu"}) is False


# ── load() short-circuit ─────────────────────────────────────────────────────


@pytest.fixture
def patched_spawn(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record _spawn_locked calls and stub _await_ready so load() needs no I/O.

    The behaviour under test is load()'s *routing decision* (does it spawn
    for this slot?), so the spawn/HTTP seams are the things to stub.
    """
    spawned: list[str] = []

    async def fake_spawn(self: SlotManager, name: str, cfg: object, model: object) -> None:
        spawned.append(name)

    async def fake_await_ready(self: SlotManager, *a: object, **k: object) -> SlotState:
        return SlotState.READY

    monkeypatch.setattr(SlotManager, "_spawn_locked", fake_spawn)
    monkeypatch.setattr(SlotManager, "_await_ready", fake_await_ready)
    return spawned


async def test_load_npu_shadow_does_not_spawn(tmp_hal0_home: str, patched_spawn: list[str]) -> None:
    """Loading an stt/embed shadow must NOT spawn a child (would 500 on NPU)."""
    _anchor(tmp_hal0_home)
    _stt_shadow(tmp_hal0_home)
    sm = SlotManager()

    slot = await sm.load("stt")

    assert patched_spawn == [], "shadow slot must not call _spawn_locked"
    assert slot.state != SlotState.ERROR


async def test_load_npu_anchor_still_spawns(tmp_hal0_home: str, patched_spawn: list[str]) -> None:
    """The chat anchor (device=npu type=llm) is NOT a shadow — it must spawn."""
    _anchor(tmp_hal0_home)
    sm = SlotManager()

    await sm.load("npu")

    assert patched_spawn == ["npu"]


async def test_load_gpu_slot_still_spawns(tmp_hal0_home: str, patched_spawn: list[str]) -> None:
    """GPU/CPU slots are untouched by the trio-shadow guard."""
    _gpu_slot(tmp_hal0_home)
    sm = SlotManager()

    await sm.load("chat")

    assert patched_spawn == ["chat"]
