from unittest.mock import patch

import pytest

from hal0.api import hal0_llm_slot_views


class _FakeSlotManager:
    def __init__(self, cfgs):
        self._cfgs = cfgs

    async def iter_configs(self):
        return self._cfgs


class _FakeDefaults:
    def __init__(self, context_size):
        self.context_size = context_size


class _FakeModel:
    def __init__(self, context_size=None):
        self.defaults = _FakeDefaults(context_size)

    def model_dump(self):
        return {"defaults": {"context_size": self.defaults.context_size}, "metadata": {}}


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
            # slot ceiling (65536) is larger than what the model declares —
            # #1788: the model wins, so the stale larger ceiling must not
            # be echoed back.
            "model": {"default": "big", "context_size": 65536},
        },
        {
            "name": "utility",
            "type": "llm",
            "device": "gpu-vulkan",
            # slot ceiling (4096) is smaller than the model's declared
            # window — a genuine hardware clamp, so it wins.
            "model": {"default": "tiny", "context_size": 4096},
        },
        {
            "name": "agent",
            "type": "llm",
            "device": "npu",
            # ctx_size key (not context_size) must also be read correctly;
            # model isn't registered, so resolution falls to the safe floor.
            "model": {"default": "flm", "ctx_size": 32768},
        },
        {"name": "embed", "type": "embedding", "model": {"default": "e5"}},
        # #1369: model-presence is the sole filter, so BOTH "off" shapes are
        # the same shape now — an empty [model] table and an empty default.
        {"name": "nomodel", "type": "llm", "model": {}},
        {"name": "off", "type": "llm", "model": {"default": ""}},
    ]
    registry = _FakeModelRegistry(
        {
            "big": _FakeModel(context_size=8000),
            "tiny": _FakeModel(context_size=16384),
            # "flm" intentionally absent — unregistered model.
        }
    )
    views = await hal0_llm_slot_views(_FakeSlotManager(cfgs), registry)
    by_name = {v["name"]: v for v in views}
    assert set(by_name) == {"primary", "utility", "agent"}
    assert by_name["primary"]["device"] == "gpu-vulkan"
    # #1788: EFFECTIVE context, not the raw slot ceiling.
    assert by_name["primary"]["context_length"] == 8000
    assert by_name["utility"]["context_length"] == 4096
    # unregistered model + slot ceiling above the safe floor → safe floor.
    assert by_name["agent"]["context_length"] == 8192


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
            "model": {"default": "gemma4-it-e2b-FLM", "context_size": 18000},
        },
    ]
    fake_catalog = [{"tag": "gemma4-it:e2b", "installed": True, "capabilities": ["chat"]}]
    with patch("hal0.providers.flm.flm_served_models", lambda: fake_catalog):
        views = await hal0_llm_slot_views(_FakeSlotManager(cfgs))
    assert views[0]["model_id"] == "gemma4-it:e2b"
