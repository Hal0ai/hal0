"""Tests for the [npu] slot table and the flm seed profile (Phase A)."""

import tomllib
from pathlib import Path

from hal0.config.schema import SEED_PROFILES, NpuConfig, SlotConfig

_SEEDED_SLOTS_DIR = Path(__file__).resolve().parents[2] / "installer" / "etc-hal0" / "slots"


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
            "profile": "flm",
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
    # chat defaults to True (4d7a5745 feat(schema): add chat field to NpuConfig).
    assert data["extra"]["npu"] == {"asr": True, "embed": False, "chat": True}


def test_npu_chat_default_on() -> None:
    """NpuConfig.chat defaults to True - chat-first FLM container shape."""
    cfg = NpuConfig()
    assert cfg.chat is True
    cfg2 = NpuConfig(asr=True, embed=True)
    assert cfg2.chat is True
    cfg3 = NpuConfig(chat=False)
    assert cfg3.chat is False
    assert cfg3.asr is False
    assert cfg3.embed is False


def test_flm_npu_seed_profile() -> None:
    prof = SEED_PROFILES["flm"]
    assert prof["flags"] == ""
    assert prof["mtp"] is False


def test_seed_flm_toml_validates() -> None:
    """flm.toml is THE seeded NPU slot (install.sh seed loop); the old
    npu.toml near-duplicate was never in the loop and has been removed."""
    raw = tomllib.loads((_SEEDED_SLOTS_DIR / "flm.toml").read_text(encoding="utf-8"))
    slot = SlotConfig.model_validate(raw)
    assert slot.runtime == "container"
    assert slot.profile == "flm"
    assert slot.device == "npu"
    assert slot.port == 8088
    # chat-only seed: no [npu] trio table (chat defaults on; asr/embed are
    # flipped per-slot via the dashboard's NPU drawer)
    assert slot.npu is None
    # Clean seed (WS-E, #1107): no FLM model pin — boots grey, no surprise
    # download, no crash-loop. context_size is a tuning default for later.
    assert slot.model is not None and slot.model.default == ""
    assert slot.model.context_size == 16384


def test_seed_tts_toml_validates() -> None:
    raw = tomllib.loads((_SEEDED_SLOTS_DIR / "tts.toml").read_text(encoding="utf-8"))
    slot = SlotConfig.model_validate(raw)
    assert slot.runtime == "container"
    assert slot.profile == "kokoro"
    assert slot.device == "cpu"
    assert (
        slot.port == 8085
    )  # drifted from 8084 per spec §5.4 (port fix for _SETUP_SLOTS[stt] conflict)
