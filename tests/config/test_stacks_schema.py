"""Unit tests for the Stack schema models.

Targeted file run:
    ~/dev/hal0/.venv/bin/python -m pytest tests/config/test_stacks_schema.py -q
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hal0.config.schema import (
    SEED_STACKS,
    STACK_SCHEMA_VERSION_CURRENT,
    StackCapabilityRow,
    StackConfig,
    StackModelMeta,
    StacksConfig,
    StackSlotEntry,
)


class TestStackModelMeta:
    def test_minimal_requires_id(self) -> None:
        m = StackModelMeta(id="chadrock-35b-ace-saber")
        assert m.id == "chadrock-35b-ace-saber"
        assert m.size_bytes == 0
        assert m.capabilities == []
        assert m.mmproj is None

    def test_empty_id_raises(self) -> None:
        with pytest.raises(ValidationError):
            StackModelMeta(id="   ")

    def test_extra_field_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            StackModelMeta(
                id="m1", path="/mnt/ai-models/x.gguf"
            )  # path is machine-specific, excluded

    def test_id_is_stripped(self) -> None:
        assert StackModelMeta(id="  chadrock-35b-ace-saber  ").id == "chadrock-35b-ace-saber"


class TestStackCapabilityRow:
    def test_valid_row(self) -> None:
        r = StackCapabilityRow(child="embed", device="npu", provider="flm", model="bge-m3")
        assert r.enabled is True

    def test_bad_device_raises(self) -> None:
        with pytest.raises(ValidationError):
            StackCapabilityRow(child="embed", device="quantum", provider="flm", model="bge-m3")


class TestStackSlotEntry:
    def test_minimal_requires_slot(self) -> None:
        e = StackSlotEntry(slot="agent")
        assert e.slot == "agent"
        assert e.profile is None
        assert e.capabilities == []

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("vision", True),
            ("mtp", True),
            ("enable_thinking", False),
            ("server_extra_args", "-ctk q8_0"),
        ],
    )
    def test_tuning_keys_are_refused(self, key: str, value: object) -> None:
        """ADR-0029 §6: a stack row carries no tuning — it lives on the profile."""
        with pytest.raises(ValidationError):
            StackSlotEntry.model_validate({"slot": "agent", key: value})

    def test_bad_slot_name_raises(self) -> None:
        with pytest.raises(ValidationError):
            StackSlotEntry(slot="Agent Slot!")

    def test_stack_entry_tune_round_trip(self) -> None:
        e = StackSlotEntry.model_validate(
            {"slot": "agent", "tune": {"vision": "off", "ctx_size": 8192}}
        )
        assert e.tune is not None and e.tune.vision == "off" and e.tune.ctx_size == 8192
        back = StackSlotEntry.model_validate(e.model_dump(mode="json", exclude_none=True))
        assert back == e
        assert StackSlotEntry(slot="agent").tune is None

    @pytest.mark.parametrize("tune", [{"mtp": "off"}, {"vision": "maybe"}, {"ctx_size": 5}])
    def test_stack_entry_tune_is_validated(self, tune: dict) -> None:
        with pytest.raises(ValidationError):
            StackSlotEntry.model_validate({"slot": "agent", "tune": tune})

    def test_bad_device_raises(self) -> None:
        with pytest.raises(ValidationError):
            StackSlotEntry(slot="agent", device="gpu-quantum")


class TestStackConfig:
    def test_defaults(self) -> None:
        s = StackConfig()
        assert s.name == ""
        assert s.schema_version == STACK_SCHEMA_VERSION_CURRENT
        assert s.slots == []
        assert s.profiles == {}
        assert s.models == {}

    def test_full_round_trip_through_dict(self) -> None:
        s = StackConfig(
            name="Saber",
            description="high-speed agentic MoE",
            slots=[StackSlotEntry(slot="agent", model="chadrock-35b-ace-saber")],
            models={"chadrock-35b-ace-saber": StackModelMeta(id="chadrock-35b-ace-saber")},
        )
        dumped = s.model_dump(mode="python", exclude_none=True)
        again = StackConfig.model_validate(dumped)
        assert again.slots[0].slot == "agent"
        assert again.models["chadrock-35b-ace-saber"].id == "chadrock-35b-ace-saber"

    def test_extra_field_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            StackConfig(surprise="nope")


class TestStacksConfig:
    def test_empty_default(self) -> None:
        c = StacksConfig()
        assert c.stack == {}

    def test_keyed_by_slug(self) -> None:
        c = StacksConfig(stack={"saber": StackConfig(name="Saber")})
        assert c.stack["saber"].name == "Saber"


class TestSeedStacks:
    def test_seed_stacks_shipped(self) -> None:
        # PR-6 ships saber/forge/pi as immutable, clone-only seeds.
        assert set(SEED_STACKS) == {"saber", "forge", "pi"}

    def test_seed_stacks_well_formed(self) -> None:
        for slug, stack in SEED_STACKS.items():
            assert isinstance(stack, StackConfig)
            assert stack.name, f"{slug}: seed needs a display name"
            assert stack.slots, f"{slug}: seed needs at least one slot"
            # Seeds use the canonical agent/utility slots (ADR-0023).
            assert {e.slot for e in stack.slots} <= {"agent", "utility"}
            # Every slot carries a model and a valid device.
            for entry in stack.slots:
                assert entry.model, f"{slug}/{entry.slot}: seed slot needs a model"
                assert entry.device in {"gpu-rocm", "gpu-vulkan", "cpu", "npu"}
