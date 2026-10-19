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
    # A stored provider is never an input: the device decides.
    assert _engine_provider_for_tts("gpu-rocm", "kokoro") == "qwen3tts"
    assert _engine_provider_for_tts("cpu", "qwen3tts") == "kokoro"


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


# ── apply path: provider follows the device (#2749) ──────────────────────────


class _Slot:
    def __init__(self, state: str = "ready") -> None:
        class _S:
            value = state

        self.state = _S()


class _FakeSlotManager:
    """Lifecycle stand-in: every call succeeds, nothing touches systemd."""

    async def iter_configs(self) -> list[dict[str, object]]:
        return []

    async def status(self, slot_name: str) -> _Slot:
        return _Slot("ready")

    async def load(self, slot_name: str, model_id: str | None = None) -> _Slot:
        return _Slot("ready")

    async def unload(self, slot_name: str) -> _Slot:
        return _Slot("offline")

    async def swap(self, slot_name: str, new_model_id: str) -> _Slot:
        return _Slot("ready")

    async def restart(self, slot_name: str) -> _Slot:
        return _Slot("ready")

    async def create(self, slot_name: str, cfg: dict[str, object]) -> _Slot:
        return _Slot("offline")

    async def update_config(self, slot_name: str, updates: dict[str, object]) -> _Slot:
        return _Slot("ready")


@pytest.fixture
def apply_orch(tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch) -> CapabilityOrchestrator:
    import hal0.agents.hermes_refresh as hr

    monkeypatch.setattr(hr, "spawn_context_refresh", lambda *a, **k: None)
    monkeypatch.setattr(
        CapabilityOrchestrator,
        "_validate_model_in_catalog",
        lambda self, slot, child, model_id, backend_id, **kw: None,
    )
    slots = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    slots.mkdir(parents=True, exist_ok=True)
    shipped = Path(__file__).parents[2] / "installer/etc-hal0/slots/tts.toml"
    (slots / "tts.toml").write_text(shipped.read_text())
    return CapabilityOrchestrator(slot_manager=_FakeSlotManager())  # type: ignore[arg-type]


def _tts_toml(home: str) -> dict[str, object]:
    import tomllib

    with open(Path(home) / "etc" / "hal0" / "slots" / "tts.toml", "rb") as f:
        return tomllib.load(f)


@pytest.mark.asyncio
async def test_device_switch_cpu_to_gpu_and_back_rederives_provider(
    apply_orch: CapabilityOrchestrator, tmp_hal0_home: str
) -> None:
    await apply_orch.initialize_if_missing()
    assert apply_orch._load().selections["voice"]["tts"].provider == "kokoro"

    out = await apply_orch.apply(
        "voice", "tts", {"device": "gpu-rocm", "model": "qwen3-tts", "enabled": True}
    )
    assert out["provider"] == "qwen3tts"
    assert apply_orch._load().selections["voice"]["tts"].provider == "qwen3tts"
    written = _tts_toml(tmp_hal0_home)
    assert written["provider"] == "qwen3tts"
    assert written["profile"] == "qwen3-tts"

    out = await apply_orch.apply(
        "voice", "tts", {"device": "cpu", "model": "kokoro-v1", "enabled": True}
    )
    assert out["provider"] == "kokoro"
    assert apply_orch._load().selections["voice"]["tts"].provider == "kokoro"
    written = _tts_toml(tmp_hal0_home)
    assert written["provider"] == "kokoro"
    assert written["profile"] == "kokoro"


@pytest.mark.asyncio
async def test_partial_apply_heals_llama_server_provider(
    apply_orch: CapabilityOrchestrator, tmp_hal0_home: str
) -> None:
    cfg = CapabilityConfig()
    cfg.selections["voice"] = {"tts": CapabilitySelection(device="cpu", provider="llama-server")}
    apply_orch._save(cfg)

    # Partial carries no provider; the persisted one must still be the engine's.
    out = await apply_orch.apply("voice", "tts", {"model": "kokoro-v1", "enabled": True})
    assert out["provider"] == "kokoro"
    assert apply_orch._load().selections["voice"]["tts"].provider == "kokoro"
    assert _tts_toml(tmp_hal0_home)["provider"] == "kokoro"


@pytest.mark.asyncio
async def test_vulkan_selection_label_and_profile_agree(
    apply_orch: CapabilityOrchestrator, tmp_hal0_home: str
) -> None:
    """gpu-vulkan has no qwen3tts runner (ROCm-only): label and profile are both Kokoro."""
    await apply_orch.initialize_if_missing()
    out = await apply_orch.apply(
        "voice", "tts", {"device": "gpu-vulkan", "model": "kokoro-v1", "enabled": True}
    )
    written = _tts_toml(tmp_hal0_home)
    assert out["provider"] == "kokoro"
    assert written["provider"] == "kokoro"
    assert written["profile"] == "kokoro"
