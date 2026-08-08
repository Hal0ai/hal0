"""NPU exclusivity validation in SlotManager (PR-11, plan §5.3, ADR-0008 §5).

The AMDXDNA hardware context admits exactly one ``device=npu, type=llm``
slot **with a model configured** at a time. Since #1369 removed
``SlotConfig.enabled``, model-presence is the anchor-claim discriminator:
a model-less NPU LLM slot is inert config and may coexist, while binding a
second model is the write that gets refused. SlotManager.create() and
update_config() both gate on the helper :meth:`_check_npu_exclusivity` —
these tests pin the contract.

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


def _seed_npu_anchor(home: str, name: str = "agent", *, port: int = 8082) -> Path:
    """Seed the incumbent NPU LLM anchor: device=npu, type=llm, model bound."""
    return _write_slot_toml(
        home,
        name,
        [
            f'name = "{name}"',
            f"port = {port}",
            'device = "npu"',
            'type = "llm"',
            "[model]",
            'default = "gemma3-1b"',
        ],
    )


# ── Negative paths: a second NPU LLM anchor claims a model ──────────────────


async def test_create_rejects_second_npu_llm_with_a_model(tmp_hal0_home: str) -> None:
    """A second device=npu, type=llm slot with a model bound must be rejected."""
    _seed_npu_anchor(tmp_hal0_home)
    sm = SlotManager()
    with pytest.raises(NpuExclusivityViolation) as exc:
        await sm.create(
            "agent-2",
            {
                "name": "agent-2",
                "port": 8083,
                "device": "npu",
                "type": "llm",
                "model": {"default": "qwen3-1b"},
            },
        )
    assert "agent" in exc.value.details["conflicting_slots"]
    assert exc.value.status == 409
    # The new slot must not have been written to disk.
    assert not (Path(tmp_hal0_home) / "etc" / "hal0" / "slots" / "agent-2.toml").exists()


async def test_update_config_rejects_binding_a_model_to_a_second_npu_llm(
    tmp_hal0_home: str,
) -> None:
    """Binding a model to a model-less sibling NPU LLM is blocked.

    This is the #1369 replacement for the old ``enabled=false → true`` flip:
    the model write IS the activation, so it is the write that must 409.
    """
    _seed_npu_anchor(tmp_hal0_home)
    # Seed agent-2 model-less; allowed because it doesn't claim the HW.
    _write_slot_toml(
        tmp_hal0_home,
        "agent-2",
        [
            'name = "agent-2"',
            "port = 8083",
            'device = "npu"',
            'type = "llm"',
            "[model]",
            'default = ""',
        ],
    )
    sm = SlotManager()
    with pytest.raises(NpuExclusivityViolation):
        await sm.update_config("agent-2", {"model": {"default": "qwen3-1b"}})


async def test_violation_message_names_the_model_not_a_toggle(tmp_hal0_home: str) -> None:
    """The operator-facing text must describe the real remedy: clear the model."""
    _seed_npu_anchor(tmp_hal0_home)
    sm = SlotManager()
    with pytest.raises(NpuExclusivityViolation) as exc:
        await sm.create(
            "agent-2",
            {
                "name": "agent-2",
                "port": 8083,
                "device": "npu",
                "type": "llm",
                "model": {"default": "qwen3-1b"},
            },
        )
    assert "model configured" in str(exc.value)
    assert "model" in exc.value.details["hint"]


# ── Positive paths: changes that DON'T violate the constraint ───────────────


async def test_create_allows_model_less_second_npu_llm(tmp_hal0_home: str) -> None:
    """A model-less second NPU LLM slot may coexist with the bound anchor."""
    _seed_npu_anchor(tmp_hal0_home)
    sm = SlotManager()
    snap = await sm.create(
        "agent-spare",
        {
            "name": "agent-spare",
            "port": 8083,
            "device": "npu",
            "type": "llm",
            "model": {"default": ""},
        },
    )
    assert snap is not None
    assert (Path(tmp_hal0_home) / "etc" / "hal0" / "slots" / "agent-spare.toml").exists()


async def test_model_less_incumbent_does_not_block_a_new_anchor(
    tmp_hal0_home: str,
) -> None:
    """A model-less peer isn't an anchor, so it can't be the conflicting slot.

    The seeded ``flm`` slot ships model-less; before #1369 it also shipped
    ``enabled = false``, and this is the assertion that the two signals were
    interchangeable all along.
    """
    _write_slot_toml(
        tmp_hal0_home,
        "flm",
        [
            'name = "flm"',
            "port = 8088",
            'device = "npu"',
            'type = "llm"',
            "[model]",
            'default = ""',
        ],
    )
    sm = SlotManager()
    snap = await sm.create(
        "agent",
        {
            "name": "agent",
            "port": 8082,
            "device": "npu",
            "type": "llm",
            "model": {"default": "gemma3-1b"},
        },
    )
    assert snap is not None


async def test_create_allows_non_npu_slot_alongside_npu_llm(tmp_hal0_home: str) -> None:
    """device=gpu-rocm slots are unaffected by NPU exclusivity."""
    _seed_npu_anchor(tmp_hal0_home)
    sm = SlotManager()
    await sm.create(
        "primary-2",
        {
            "name": "primary-2",
            "port": 8083,
            "device": "gpu-rocm",
            "type": "llm",
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
    _seed_npu_anchor(tmp_hal0_home)
    sm = SlotManager()
    await sm.create(
        "stt-npu",
        {
            "name": "stt-npu",
            "port": 8084,
            "device": "npu",
            "type": "transcription",
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
            "model": {"default": "embed-gemma"},
        },
    )


async def test_update_config_self_idempotent_when_no_conflict(tmp_hal0_home: str) -> None:
    """Updating the lone NPU LLM slot's own fields does NOT trip the guard.

    The guard skips the writer's own slot — without that, a routine
    ``swap()`` on the lone NPU LLM would fail every time.
    """
    _seed_npu_anchor(tmp_hal0_home)
    sm = SlotManager()
    await sm.update_config("agent", {"model": {"default": "qwen3-1b"}})


async def test_clearing_the_incumbent_model_frees_the_anchor(tmp_hal0_home: str) -> None:
    """Clearing ``model.default`` releases the anchor for another slot.

    This is the documented remedy in the violation hint, so it has to work.
    """
    _seed_npu_anchor(tmp_hal0_home)
    _write_slot_toml(
        tmp_hal0_home,
        "agent-2",
        [
            'name = "agent-2"',
            "port = 8083",
            'device = "npu"',
            'type = "llm"',
            "[model]",
            'default = ""',
        ],
    )
    sm = SlotManager()
    await sm.update_config("agent", {"model": {"default": ""}})
    await sm.update_config("agent-2", {"model": {"default": "qwen3-1b"}})


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
            "model": {"default": "gemma3-1b"},
        },
    )
    assert snap is not None


# ── Id-keyed boxes (#1569): stems are numeric ids, names live in the body ──


def test_id_keyed_own_file_is_not_a_peer(tmp_hal0_home: str) -> None:
    """A slot must not conflict with its OWN file stored under an id stem.

    On an id-keyed box ``flm``'s config lives at ``8.toml`` (stem = numeric
    id, display name in the body). The peer walk excluded only ``flm.toml``,
    so the slot's own file counted as an offender and EVERY config write on
    the anchor 409'd against itself ("slot 'flm' would conflict with '8'").
    """
    from hal0.slots.config_write import check_npu_exclusivity

    cfg = {
        "name": "flm",
        "device": "npu",
        "type": "llm",
        "model": {"default": "gemma4-it:e4b"},
    }
    path = _write_slot_toml(
        tmp_hal0_home,
        "8",
        [
            'name = "flm"',
            "port = 8088",
            'device = "npu"',
            'type = "llm"',
            "id = 8",
            "[model]",
            'default = "gemma4-it:e4b"',
        ],
    )
    # Must not raise: 8.toml IS slot "flm".
    check_npu_exclusivity("flm", cfg, slots_dir=path.parent)


def test_id_keyed_real_peer_still_conflicts_and_is_named(tmp_hal0_home: str) -> None:
    """A genuine second anchor under an id stem still 409s, reported by name."""
    from hal0.slots.config_write import check_npu_exclusivity

    path = _write_slot_toml(
        tmp_hal0_home,
        "9",
        [
            'name = "npu-b"',
            "port = 8089",
            'device = "npu"',
            'type = "llm"',
            "id = 9",
            "[model]",
            'default = "qwen3-1b"',
        ],
    )
    cfg = {
        "name": "flm",
        "device": "npu",
        "type": "llm",
        "model": {"default": "gemma4-it:e4b"},
    }
    with pytest.raises(NpuExclusivityViolation) as exc:
        check_npu_exclusivity("flm", cfg, slots_dir=path.parent)
    # The offender is reported by its display name, not its opaque id stem.
    assert exc.value.details["conflicting_slots"] == ["npu-b"]
