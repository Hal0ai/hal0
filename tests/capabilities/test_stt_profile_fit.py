"""STT branch of the shared picker/apply profile-fit rule.

The stt capability is device-keyed exactly like tts (two engines on two
different devices): ``npu`` resolves the FLM trio profile, ``cpu`` resolves
the Moonshine profile. GPU devices resolve NOTHING — hal0 ships no GPU STT
engine, and the historical behaviour (falling through to the generic GPU
branch) handed the stt slot a llama chat profile: wrong runtime family, the
slot could never start the STT image. That fall-through is the regression
pinned here.
"""

from __future__ import annotations

from hal0.capabilities.profile_fit import profile_name_for_fit
from hal0.config.schema import DEVICE_DEFAULT_PROFILES


def test_stt_npu_resolves_flm_profile() -> None:
    assert profile_name_for_fit("stt", "npu") == DEVICE_DEFAULT_PROFILES["npu"]


def test_stt_cpu_resolves_moonshine() -> None:
    assert profile_name_for_fit("stt", "cpu") == "moonshine"


def test_stt_gpu_devices_resolve_nothing() -> None:
    """Regression: gpu-vulkan STT used to resolve the llama chat profile.

    ``None`` (not a llama profile) is the contract — the apply path treats
    it as "no engine for this device" instead of silently writing a chat
    profile into the stt slot.
    """
    for device in ("gpu-vulkan", "gpu-rocm", "gpu-cuda"):
        assert profile_name_for_fit("stt", device) is None


def test_stt_unknown_device_resolves_nothing() -> None:
    assert profile_name_for_fit("stt", "") is None
    assert profile_name_for_fit("stt", "bogus") is None


def test_moonshine_profile_serves_transcription_only() -> None:
    """The seeded moonshine profile classifies to the moonshine runtime
    family and supports exactly the transcription slot type."""
    from hal0.profiles import ProfileCatalog

    resolved = ProfileCatalog().resolve("moonshine")
    assert resolved.runtime_family == "moonshine"
    assert resolved.supported_slot_types == ("transcription",)


def test_tts_rule_unchanged() -> None:
    """The tts engine switch must stay byte-identical while stt lands."""
    assert profile_name_for_fit("tts", "cpu") == "kokoro"
    assert profile_name_for_fit("tts", "gpu-rocm") == "qwen3-tts"
    assert profile_name_for_fit("tts", "gpu-vulkan") == "qwen3-tts"
