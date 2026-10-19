"""voice.tts selection provider is the engine's, never the SlotConfig default (#2749)."""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.capabilities.config import CapabilityConfig, CapabilitySelection
from hal0.capabilities.orchestrator import CapabilityOrchestrator, _engine_provider_for_tts


def _orch(tmp_path: Path) -> CapabilityOrchestrator:
    return CapabilityOrchestrator(
        slot_manager=object(),  # type: ignore[arg-type]
        registry=None,  # type: ignore[arg-type]
        config_path=tmp_path / "capabilities.toml",
    )


def test_engine_provider_derivation() -> None:
    assert _engine_provider_for_tts("cpu", "llama-server") == "kokoro"
    assert _engine_provider_for_tts("cpu", "") == "kokoro"
    assert _engine_provider_for_tts("gpu-rocm", "llama-server") == "qwen3tts"
    # An explicit non-deprecated provider is left alone.
    assert _engine_provider_for_tts("cpu", "kokoro") == "kokoro"


@pytest.mark.asyncio
async def test_seed_from_providerless_tts_toml(tmp_hal0_home: str, tmp_path: Path) -> None:
    from hal0.config.loader import load_slot_config

    shipped = Path(__file__).parents[2] / "installer/etc-hal0/slots/tts.toml"
    slots = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    slots.mkdir(parents=True, exist_ok=True)
    (slots / "tts.toml").write_text(shipped.read_text())
    assert load_slot_config("tts").provider == "llama-server"  # the default being seeded

    orch = _orch(tmp_path)
    await orch.initialize_if_missing()
    assert orch._load().selections["voice"]["tts"].provider == "kokoro"


def test_heal_already_seeded_selection(tmp_path: Path) -> None:
    orch = _orch(tmp_path)
    cfg = CapabilityConfig()
    cfg.selections["voice"] = {"tts": CapabilitySelection(device="cpu", provider="llama-server")}
    assert orch._selection_with_defaults(cfg, "voice", "tts").provider == "kokoro"
    # Other children are untouched.
    cfg.selections["embed"] = {"embed": CapabilitySelection(provider="llama-server")}
    assert orch._selection_with_defaults(cfg, "embed", "embed").provider == "llama-server"
