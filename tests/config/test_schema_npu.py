"""Tests for the [npu] slot table and the flm-npu seed profile (Phase A)."""

from hal0.config.schema import SEED_PROFILES, NpuConfig, SlotConfig


def test_npu_config_defaults_off() -> None:
    cfg = NpuConfig()
    assert cfg.asr is False
    assert cfg.embed is False


def test_slot_config_accepts_npu_table() -> None:
    slot = SlotConfig.model_validate(
        {
            "name": "npu",
            "port": 8088,
            "device": "npu",
            "runtime": "container",
            "profile": "flm-npu",
            "model": {"default": "gemma3:4b"},
            "npu": {"asr": True, "embed": True},
        }
    )
    assert slot.npu is not None
    assert slot.npu.asr is True
    assert slot.npu.embed is True


def test_slot_config_npu_table_optional() -> None:
    slot = SlotConfig.model_validate({"name": "chat", "port": 8082})
    assert slot.npu is None


def test_npu_hoisted_from_extra() -> None:
    slot = SlotConfig.model_validate(
        {
            "name": "npu",
            "port": 8088,
            "extra": {"npu": {"asr": True, "embed": False}},
        }
    )
    assert slot.npu is not None and slot.npu.asr is True
    assert "npu" not in slot.extra


def test_npu_tucked_into_extra_on_dump() -> None:
    slot = SlotConfig.model_validate(
        {
            "name": "npu",
            "port": 8088,
            "npu": {"asr": True, "embed": False},
        }
    )
    data = slot.model_dump()
    assert "npu" not in data
    assert data["extra"]["npu"] == {"asr": True, "embed": False}


def test_flm_npu_seed_profile() -> None:
    prof = SEED_PROFILES["flm-npu"]
    assert prof["image"] == "ghcr.io/hal0ai/hal0-toolbox-flm:v1"
    assert prof["flags"] == ""
    assert prof["mtp"] is False
