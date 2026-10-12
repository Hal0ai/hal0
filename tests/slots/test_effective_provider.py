"""A slot's reported provider is the one the launch dispatch picks (ADR-0029).

The runtime comes from the slot profile's runtime family, else the dispatch's
device/type fallbacks — never a hard-coded ``llama-server`` stamped over a
Kokoro / Moonshine / Qwen3-TTS / ComfyUI slot. A wrong ``llama-server``
provider makes ``provider_requires_model`` treat a model-less self-managed
slot as broken.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

from hal0.slots.config_write import _cfg_effective_provider
from hal0.slots.manager import SlotManager
from hal0.slots.state import SlotState

_SHIPPED = Path(__file__).resolve().parents[2] / "installer" / "etc-hal0" / "slots"

#: Shipped slot file → the provider its profile launches.
SHIPPED_PROVIDERS = {
    "tts": "kokoro",
    "qwen3tts": "qwen3tts",
    "img": "comfyui",
    "flm": "flm",
    "agent": "llama-server",
    "embed": "llama-server",
    "rerank": "llama-server",
}

#: The STT engine: no slot ships it; ``hal0 slot create --type
#: transcription`` writes this shape (the type-implied ``moonshine`` seed).
STT_CFG: dict[str, Any] = {
    "name": "stt",
    "port": 8087,
    "type": "transcription",
    "device": "cpu",
    "profile": "moonshine",
    "model": {"default": ""},
}


def _shipped(name: str) -> dict[str, Any]:
    return tomllib.loads((_SHIPPED / f"{name}.toml").read_text(encoding="utf-8"))


@pytest.mark.parametrize(("name", "expected"), sorted(SHIPPED_PROVIDERS.items()))
def test_shipped_slot_provider(tmp_hal0_home: str, name: str, expected: str) -> None:
    assert _cfg_effective_provider(_shipped(name)) == expected


def test_stt_slot_provider(tmp_hal0_home: str) -> None:
    assert _cfg_effective_provider(STT_CFG) == "moonshine"


def test_an_explicit_llama_server_provider_does_not_override_the_profile(
    tmp_hal0_home: str,
) -> None:
    """``hal0 slot create`` always sends ``provider = "llama-server"``; the
    dispatch ignores it, so the report must too."""
    cfg = {**_shipped("tts"), "provider": "llama-server"}
    assert _cfg_effective_provider(cfg) == "kokoro"


@pytest.mark.parametrize(
    ("cfg", "expected"),
    [
        # Profile-less slots fall back to the dispatch's device/type rules.
        ({"name": "t", "type": "tts", "device": "cpu"}, "kokoro"),
        ({"name": "s", "type": "transcription", "device": "cpu"}, "moonshine"),
        ({"name": "n", "type": "llm", "device": "npu"}, "flm"),
        ({"name": "i", "type": "image", "device": "gpu-rocm"}, "comfyui"),
        ({"name": "c", "type": "llm", "device": "gpu-rocm"}, "llama-server"),
        # Nested on-disk shape: [slot] carries the scalars.
        ({"slot": {"name": "t", "type": "tts", "profile": "kokoro"}}, "kokoro"),
    ],
)
def test_dispatch_fallbacks(tmp_hal0_home: str, cfg: dict[str, Any], expected: str) -> None:
    assert _cfg_effective_provider(cfg) == expected


def test_a_missing_profile_still_reports_the_dispatch_answer(tmp_hal0_home: str) -> None:
    """A slot naming a profile that does not exist refuses to launch, but the
    dispatch still knows its engine from the type."""
    cfg = {"name": "t", "type": "tts", "device": "cpu", "profile": "no-such-profile"}
    assert _cfg_effective_provider(cfg) == "kokoro"


def _creatable(cfg: dict[str, Any]) -> dict[str, Any]:
    out = dict(cfg)
    out.setdefault("model", {"default": ""})
    return out


@pytest.mark.parametrize(
    ("name", "expected"),
    sorted(SHIPPED_PROVIDERS.items()),
)
async def test_status_reports_the_profile_provider(
    tmp_hal0_home: str, name: str, expected: str
) -> None:
    sm = SlotManager()
    await sm.create(name, _creatable(_shipped(name)))
    slot = await sm.status(name)
    assert slot.metadata["provider"] == expected


async def test_status_reports_moonshine_for_an_stt_slot(tmp_hal0_home: str) -> None:
    sm = SlotManager()
    await sm.create("stt", STT_CFG)
    assert (await sm.status("stt")).metadata["provider"] == "moonshine"


async def test_status_heals_a_stale_seeded_provider(tmp_hal0_home: str) -> None:
    """A state record seeded with ``llama-server`` before this fix (every
    existing box's Kokoro slot) reads back as ``kokoro``: status re-derives
    the provider from the TOML on every read, like the backend."""
    sm = SlotManager()
    await sm.create("tts", _creatable(_shipped("tts")))
    await sm._transition("tts", SlotState.OFFLINE, extra={"provider": "llama-server"}, force=True)
    assert sm._states[sm._key("tts")].extra["provider"] == "llama-server"  # the stale record
    assert (await sm.status("tts")).metadata["provider"] == "kokoro"
