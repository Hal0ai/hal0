"""#2690: an STT save with no device must not fall back to ``gpu-rocm``.

The dashboard sends ``{model, enabled}`` only and a fresh install seeds
``[selections.voice.stt] device=""``. The empty device used to slip past
every gate and ``_ensure_slot_exists`` wrote a ``gpu-rocm`` stt slot on
hosts with no ``/dev/kfd`` (503 ``capability.apply_failed``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hal0.capabilities.orchestrator import CapabilityOrchestrator
from hal0.errors import BadRequest

from .test_stt_capability_switch import FakeSlotManager

_CPU = {"id": "cpu", "label": "CPU", "short": "CPU", "provider": "llama-server", "multiplex": False}
_VK = {
    "id": "gpu-vulkan",
    "label": "GPU (Vulkan)",
    "short": "GPU/Vk",
    "provider": "llama-server",
    "multiplex": False,
}
_NPU = {"id": "npu", "label": "NPU", "short": "NPU", "provider": "flm", "multiplex": True}


@pytest.fixture(autouse=True)
def _no_flm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("hal0.providers.flm.flm_served_models", lambda: [])
    monkeypatch.setattr("hal0.capabilities.catalog._registry_downloaded", lambda *a, **k: True)


def _advertise(monkeypatch: pytest.MonkeyPatch, *backends: dict[str, Any]) -> None:
    monkeypatch.setattr("hal0.capabilities.catalog.available_backends", lambda: list(backends))


async def test_empty_device_resolves_to_advertised_cpu(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _advertise(monkeypatch, _VK, _CPU)
    orch = CapabilityOrchestrator(slot_manager=FakeSlotManager())
    result = await orch.apply("voice", "stt", {"model": "moonshine-base-en", "enabled": True})
    assert result["device"] == "cpu"
    stt = Path(tmp_hal0_home) / "slots" / "stt.toml"
    if stt.exists():
        assert 'device = "cpu"' in stt.read_text()
        assert "gpu-rocm" not in stt.read_text()


async def test_empty_device_nothing_fits_is_400_and_writes_nothing(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Host advertises only a GPU lane; no STT engine runs there.
    _advertise(monkeypatch, _VK)
    home = Path(tmp_hal0_home)
    orch = CapabilityOrchestrator(slot_manager=FakeSlotManager())
    with pytest.raises(BadRequest) as exc:
        await orch.apply("voice", "stt", {"model": "moonshine-base-en", "enabled": True})
    assert exc.value.code == "capability.no_engine_for_device"
    assert not (home / "slots" / "stt.toml").exists()
    assert not (home / "capabilities.toml").exists()


async def test_explicit_device_unchanged(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _advertise(monkeypatch, _CPU)
    orch = CapabilityOrchestrator(slot_manager=FakeSlotManager())
    result = await orch.apply(
        "voice", "stt", {"model": "moonshine-base-en", "device": "cpu", "enabled": True}
    )
    assert result["device"] == "cpu"
