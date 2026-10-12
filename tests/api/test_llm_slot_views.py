from unittest.mock import patch

import pytest

from hal0.api import hal0_llm_slot_views


class _FakeSlotManager:
    def __init__(self, cfgs):
        self._cfgs = cfgs

    async def iter_configs(self):
        return self._cfgs


class _FakeModel:
    """A registry row whose file declares ``native_ctx`` tokens of context."""

    def __init__(self, native_ctx=None):
        self.native_ctx = native_ctx

    def model_dump(self):
        return {"metadata": {"context_length": self.native_ctx} if self.native_ctx else {}}


class _FakeModelRegistry:
    def __init__(self, models):
        self._models = models

    def get(self, model_id):
        if model_id not in self._models:
            raise KeyError(model_id)
        return self._models[model_id]


@pytest.mark.asyncio
async def test_llm_slot_views_filters_and_projects():
    cfgs = [
        {
            "name": "primary",
            "type": "llm",
            "device": "gpu-vulkan",
            # No profile → the ``chat`` seed (no window) → the model's
            # native window (ADR-0029).
            "model": {"default": "big"},
        },
        {
            "name": "utility",
            "type": "llm",
            "device": "gpu-vulkan",
            # The profile's ctx_size wins over the model's native window.
            "profile": "agent",
            "model": {"default": "tiny"},
        },
        {
            "name": "agent",
            "type": "llm",
            "device": "npu",
            # FLM: the profile's ctx_size, else 8192 (the ``chat`` seed
            # sets none).
            "model": {"default": "flm"},
        },
        {"name": "embed", "type": "embedding", "model": {"default": "e5"}},
        # #1369: model-presence is the sole filter, so BOTH "off" shapes are
        # the same shape now — an empty [model] table and an empty default.
        {"name": "nomodel", "type": "llm", "model": {}},
        {"name": "off", "type": "llm", "model": {"default": ""}},
    ]
    registry = _FakeModelRegistry(
        {
            "big": _FakeModel(native_ctx=8000),
            "tiny": _FakeModel(native_ctx=16384),
            # "flm" intentionally absent — unregistered model.
        }
    )
    views = await hal0_llm_slot_views(_FakeSlotManager(cfgs), registry)
    by_name = {v["name"]: v for v in views}
    assert set(by_name) == {"primary", "utility", "agent"}
    assert by_name["primary"]["device"] == "gpu-vulkan"
    # The EFFECTIVE window, from the one resolver the launch path uses.
    assert by_name["primary"]["context_length"] == 8000  # native
    assert by_name["utility"]["context_length"] == 65536  # agent seed ctx_size
    assert by_name["agent"]["context_length"] == 8192  # FLM fallback


@pytest.mark.asyncio
async def test_llm_slot_views_translates_flm_id_to_colon_tag():
    """NPU/FLM slot model_id is projected as FLM's native colon tag.

    The resolver matches SlotView.model_id against the loaded set (FLM's
    advertised colon tags) and dispatches it downstream; the hal0 ``-FLM``
    catalog id would never match, so ``hal0/utility``/``hal0/npu`` would fall
    through to the chat slot.
    """
    cfgs = [
        {
            "name": "npu",
            "type": "llm",
            "device": "npu",
            "model": {"default": "gemma4-it-e2b-FLM"},
        },
    ]
    fake_catalog = [{"tag": "gemma4-it:e2b", "installed": True, "capabilities": ["chat"]}]
    with patch("hal0.providers.flm.flm_served_models", lambda: fake_catalog):
        views = await hal0_llm_slot_views(_FakeSlotManager(cfgs))
    assert views[0]["model_id"] == "gemma4-it:e2b"
