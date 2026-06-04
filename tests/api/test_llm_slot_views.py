import pytest

from hal0.api import hal0_llm_slot_views


class _FakeSlotManager:
    def __init__(self, cfgs):
        self._cfgs = cfgs

    async def iter_configs(self):
        return self._cfgs


@pytest.mark.asyncio
async def test_llm_slot_views_filters_and_projects():
    cfgs = [
        {
            "name": "primary",
            "type": "llm",
            "enabled": True,
            "device": "gpu-vulkan",
            "role": None,
            "model": {"default": "big", "context_size": 65536},
        },
        {
            "name": "utility",
            "type": "llm",
            "enabled": True,
            "device": "gpu-vulkan",
            "role": "utility",
            "model": {"default": "tiny", "context_size": 8192},
        },
        {
            "name": "agent",
            "type": "llm",
            "enabled": True,
            "device": "npu",
            "role": None,
            "model": {"default": "flm", "ctx_size": 32768},
        },
        {"name": "embed", "type": "embedding", "enabled": True, "model": {"default": "e5"}},
        {"name": "off", "type": "llm", "enabled": False, "model": {"default": "x"}},
        {"name": "nomodel", "type": "llm", "enabled": True, "model": {}},
    ]
    views = await hal0_llm_slot_views(_FakeSlotManager(cfgs))
    by_name = {v["name"]: v for v in views}
    assert set(by_name) == {"primary", "utility", "agent"}
    assert by_name["primary"]["device"] == "gpu-vulkan"
    assert by_name["primary"]["context_length"] == 65536
    assert by_name["utility"]["role"] == "utility"
    assert by_name["utility"]["context_length"] == 8192
    # ctx_size key (not context_size) must also be read correctly
    assert by_name["agent"]["context_length"] == 32768
