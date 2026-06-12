"""NPU exclusivity validation in SlotManager (PR-11, plan §5.3, ADR-0008 §5).

The AMDXDNA hardware context admits exactly one ``device=npu, type=llm,
enabled=true`` slot at a time. SlotManager.create() and update_config()
both gate on the helper :meth:`_check_npu_exclusivity` — these tests
pin the contract.

Conventions:
  - Tests don't spawn containers (the validation runs before any I/O).
  - ``tmp_hal0_home`` isolates the writer's TOML to a tmp directory.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.slots.manager import SlotManager
from hal0.slots.state import NpuExclusivityViolation


def _write_slot_toml(home: str, name: str, lines: list[str]) -> Path:
    """Write a minimal slot TOML under HAL0_HOME without going through SlotManager.

    Tests use this to seed the "peer slot already exists" precondition
    so the validator under test has something to find.
    """
    root = Path(home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ── Negative paths: NPU LLM is being created/enabled ────────────────────────


async def test_create_rejects_second_enabled_npu_llm(tmp_hal0_home: str) -> None:
    """A second device=npu, type=llm, enabled=true slot must be rejected."""
    _write_slot_toml(
        tmp_hal0_home,
        "agent",
        [
            'name = "agent"',
            "port = 8082",
            'device = "npu"',
            'type = "llm"',
            "enabled = true",
            "[model]",
            'default = "gemma3-1b"',
        ],
    )
    sm = SlotManager()
    with pytest.raises(NpuExclusivityViolation) as exc:
        await sm.create(
            "agent-2",
            {
                "name": "agent-2",
                "port": 8083,
                "device": "npu",
                "type": "llm",
                "enabled": True,
                "model": {"default": "qwen3-1b"},
            },
        )
    assert "agent" in exc.value.details["conflicting_slots"]
    assert exc.value.status == 409
    # The new slot must not have been written to disk.
    assert not (Path(tmp_hal0_home) / "etc" / "hal0" / "slots" / "agent-2.toml").exists()


async def test_update_config_rejects_enabling_second_npu_llm(tmp_hal0_home: str) -> None:
    """Flipping ``enabled=false → true`` on a sibling NPU LLM is blocked."""
    _write_slot_toml(
        tmp_hal0_home,
        "agent",
        [
            'name = "agent"',
            "port = 8082",
            'device = "npu"',
            'type = "llm"',
            "enabled = true",
            "[model]",
            'default = "gemma3-1b"',
        ],
    )
    # Seed agent-2 with enabled=false; allowed because it doesn't claim the HW.
    _write_slot_toml(
        tmp_hal0_home,
        "agent-2",
        [
            'name = "agent-2"',
            "port = 8083",
            'device = "npu"',
            'type = "llm"',
            "enabled = false",
            "[model]",
            'default = "qwen3-1b"',
        ],
    )
    sm = SlotManager()
    with pytest.raises(NpuExclusivityViolation):
        await sm.update_config("agent-2", {"enabled": True})


# ── Positive paths: changes that DON'T violate the constraint ───────────────


async def test_create_allows_disabled_second_npu_llm(tmp_hal0_home: str) -> None:
    """A disabled second NPU LLM slot may coexist with an enabled one."""
    _write_slot_toml(
        tmp_hal0_home,
        "agent",
        [
            'name = "agent"',
            "port = 8082",
            'device = "npu"',
            'type = "llm"',
            "enabled = true",
            "[model]",
            'default = "gemma3-1b"',
        ],
    )
    sm = SlotManager()
    snap = await sm.create(
        "agent-spare",
        {
            "name": "agent-spare",
            "port": 8083,
            "device": "npu",
            "type": "llm",
            "enabled": False,
            "model": {"default": "qwen3-1b"},
        },
    )
    assert snap is not None
    assert (Path(tmp_hal0_home) / "etc" / "hal0" / "slots" / "agent-spare.toml").exists()


async def test_create_allows_non_npu_slot_alongside_npu_llm(tmp_hal0_home: str) -> None:
    """device=gpu-rocm slots are unaffected by NPU exclusivity."""
    _write_slot_toml(
        tmp_hal0_home,
        "agent",
        [
            'name = "agent"',
            "port = 8082",
            'device = "npu"',
            'type = "llm"',
            "enabled = true",
            "[model]",
            'default = "gemma3-1b"',
        ],
    )
    sm = SlotManager()
    await sm.create(
        "primary-2",
        {
            "name": "primary-2",
            "port": 8083,
            "device": "gpu-rocm",
            "type": "llm",
            "enabled": True,
            "model": {"default": "qwen3-9b"},
        },
    )


async def test_create_allows_npu_embedding_or_transcription_alongside_npu_llm(
    tmp_hal0_home: str,
) -> None:
    """Only ``type=llm`` slots claim the AMDXDNA chat context.

    The FLM trio (stt-npu + embed-npu) is the canonical example — they
    DO run on the NPU but coresident with the chat anchor, not as
    additional anchors.
    """
    _write_slot_toml(
        tmp_hal0_home,
        "agent",
        [
            'name = "agent"',
            "port = 8082",
            'device = "npu"',
            'type = "llm"',
            "enabled = true",
            "[model]",
            'default = "gemma3-1b"',
        ],
    )
    sm = SlotManager()
    await sm.create(
        "stt-npu",
        {
            "name": "stt-npu",
            "port": 8084,
            "device": "npu",
            "type": "transcription",
            "enabled": True,
            "model": {"default": "whisper-v3"},
        },
    )
    await sm.create(
        "embed-npu",
        {
            "name": "embed-npu",
            "port": 8085,
            "device": "npu",
            "type": "embedding",
            "enabled": True,
            "model": {"default": "embed-gemma"},
        },
    )


async def test_update_config_self_idempotent_when_no_conflict(tmp_hal0_home: str) -> None:
    """Updating the lone NPU LLM slot's own fields does NOT trip the guard.

    The guard skips the writer's own slot — without that, a routine
    ``swap()`` on the lone NPU LLM would fail every time.
    """
    _write_slot_toml(
        tmp_hal0_home,
        "agent",
        [
            'name = "agent"',
            "port = 8082",
            'device = "npu"',
            'type = "llm"',
            "enabled = true",
            "[model]",
            'default = "gemma3-1b"',
        ],
    )
    sm = SlotManager()
    await sm.update_config("agent", {"model": {"default": "qwen3-1b"}})


async def test_create_allows_first_npu_llm_in_clean_home(tmp_hal0_home: str) -> None:
    """The very first NPU LLM slot must succeed."""
    sm = SlotManager()
    snap = await sm.create(
        "agent",
        {
            "name": "agent",
            "port": 8082,
            "device": "npu",
            "type": "llm",
            "enabled": True,
            "model": {"default": "gemma3-1b"},
        },
    )
    assert snap is not None
