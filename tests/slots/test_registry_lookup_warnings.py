"""Registry lookup warnings distinguish expected staged-provider misses."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from hal0.registry.store import ModelNotFound
from hal0.slots.manager import SlotManager
from hal0.slots.routing import _model_info_for


class _Registry:
    def __init__(self, model=None):
        self.model = model

    def get(self, model_id):
        if self.model is None:
            raise ModelNotFound(model_id)
        return self.model


@pytest.fixture
def registry_factory(monkeypatch):
    import hal0.registry.store

    registry = _Registry()
    monkeypatch.setattr(hal0.registry.store, "ModelRegistry", lambda: registry)
    return registry


def _manager() -> SlotManager:
    return SlotManager.__new__(SlotManager)


@pytest.mark.parametrize("provider", ["qwen3tts", "moonshine"])
async def test_self_managed_registry_miss_is_not_warned(
    provider, registry_factory, monkeypatch, caplog
):
    monkeypatch.setattr("hal0.providers.flm.flm_id_to_tag_async", AsyncMock(return_value=None))

    with caplog.at_level(logging.WARNING):
        result = await _manager()._resolve_model_info("staged-voice", provider=provider)

    assert result["_model_key"] == "staged-voice"
    assert "slot.model_not_in_registry" not in caplog.messages


async def test_unexpected_registry_miss_keeps_warning(registry_factory, caplog):
    with caplog.at_level(logging.WARNING):
        await _manager()._resolve_model_info("missing-llm", provider="llama-server")

    assert "slot.model_not_in_registry" in caplog.messages


async def test_kokoro_registry_hit_keeps_metadata_enrichment(registry_factory, caplog):
    registry_factory.model = {"id": "kokoro-v1", "provider": "kokoro", "quant": "ONNX"}

    with caplog.at_level(logging.WARNING):
        result = await _manager()._resolve_model_info("kokoro-v1", provider="kokoro")

    assert result["id"] == "kokoro-v1"
    assert result["quant"] == "ONNX"
    assert "slot.model_not_in_registry" not in caplog.messages


async def test_kokoro_registry_miss_keeps_warning(registry_factory, caplog):
    with caplog.at_level(logging.WARNING):
        await _manager()._resolve_model_info("kokoro-v1", provider="kokoro")

    assert "slot.model_not_in_registry" in caplog.messages


async def test_routing_lookup_passes_effective_provider(tmp_hal0_home):
    class Host:
        provider = None

        async def _resolve_model_info(self, model_id, *, provider=None):
            self.provider = provider
            return {"_model_key": model_id}

    host = Host()
    cfg = {
        "name": "qwen3tts",
        "type": "tts",
        "device": "gpu-rocm",
        "profile": "qwen3-tts",
        "model": {"default": "qwen3-tts"},
    }

    await _model_info_for(host, cfg)

    assert host.provider == "qwen3tts"


async def test_active_self_managed_reload_and_status_drift_stay_quiet(
    slot_root, container_stub, registry_factory, monkeypatch, caplog
):
    monkeypatch.setattr("hal0.providers.flm.flm_id_to_tag_async", AsyncMock(return_value=None))
    monkeypatch.setattr(SlotManager, "_resolve_servable_model", lambda self, mid, cfg: mid)
    (slot_root / "qwen3tts.toml").write_text(
        "\n".join(
            [
                'name = "qwen3tts"',
                'type = "tts"',
                'device = "gpu-rocm"',
                'profile = "qwen3-tts"',
                "port = 8095",
                "[model]",
                'default = "qwen3-tts"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    argv = ["--model", "qwen3-tts"]
    container_stub.expected_argv_by_slot["qwen3tts"] = argv
    container_stub.running_argv_by_slot["qwen3tts"] = argv

    manager = SlotManager()
    with caplog.at_level(logging.WARNING):
        # The first load resolves the model through manager._load's
        # _resolve_model_info — the call site the fix threads the provider
        # through. Nothing clears the log between the legs, so a warning
        # raised by any of them fails the assertion below.
        await manager.load("qwen3tts")
        # An ACTIVE-slot reload: a second load() on an already-READY slot
        # short-circuits before _load, so it never reaches the resolver.
        # Unload first, so the reload really re-enters the load path.
        await manager.unload("qwen3tts")
        await manager.load("qwen3tts")
        # The status drift check resolves the model again, through
        # drift.compute_config_drift.
        await manager.status("qwen3tts", include_config_drift=True)

    assert "slot.model_not_in_registry" not in caplog.messages
