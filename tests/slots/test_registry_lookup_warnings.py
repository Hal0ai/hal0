"""Registry lookup warnings distinguish expected staged-provider misses."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from hal0.registry.store import ModelNotFound
from hal0.slots.manager import SlotManager


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
    monkeypatch.setattr(
        "hal0.providers.flm.flm_id_to_tag_async", AsyncMock(return_value=None)
    )

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
