import pytest

from hal0.normalize.resolver import (
    DEFAULT_CHAINS,
    SlotView,
    resolve_chain,
)


def _slots():
    return [
        SlotView(
            name="chat", role=None, device="gpu-vulkan", model_id="big-35b", context_length=65536
        ),
        SlotView(
            name="utility",
            role=None,
            device="gpu-vulkan",
            model_id="tiny-0.8b",
            context_length=65536,
        ),
        SlotView(
            name="agent", role=None, device="npu", model_id="qwen3-4b-FLM", context_length=32768
        ),
    ]


def test_chat_prefers_igpu_when_loaded():
    r = resolve_chain("hal0/chat", _slots(), loaded={"big-35b", "qwen3-4b-FLM"})
    assert r.model_id == "big-35b"
    assert r.context_length == 65536
    assert r.fallback is False


def test_primary_alias_resolves_to_chat():
    """Back-compat: hal0/primary still resolves (via VIRTUAL_ALIASES → hal0/chat)."""
    r = resolve_chain("hal0/primary", _slots(), loaded={"big-35b", "qwen3-4b-FLM"})
    assert r is not None
    assert r.model_id == "big-35b"
    assert r.fallback is False


def test_npu_picks_npu_first_never_commandeers_primary():
    r = resolve_chain("hal0/npu", _slots(), loaded={"qwen3-4b-FLM", "big-35b"})
    assert r.model_id == "qwen3-4b-FLM"
    assert r.matched_role == "npu"


def test_npu_falls_to_utility_before_primary():
    r = resolve_chain("hal0/npu", _slots(), loaded={"tiny-0.8b", "big-35b"})
    assert r.model_id == "tiny-0.8b"
    assert r.matched_role == "utility"


def test_utility_chain_order():
    r = resolve_chain("hal0/utility", _slots(), loaded={"qwen3-4b-FLM", "big-35b"})
    assert r.model_id == "qwen3-4b-FLM"
    assert r.matched_role == "npu"


def test_role_tag_overrides_name():
    slots = [
        SlotView(
            name="coder-mini",
            role="utility",
            device="gpu-vulkan",
            model_id="cm",
            context_length=8192,
        ),
        SlotView(name="chat", role=None, device="gpu-vulkan", model_id="big", context_length=65536),
    ]
    r = resolve_chain("hal0/utility", slots, loaded={"cm"})
    assert r.model_id == "cm"


def test_full_miss_falls_back_to_configured_primary_unloaded():
    r = resolve_chain("hal0/utility", _slots(), loaded=set())
    assert r.model_id == "big-35b"
    assert r.fallback is True


def test_flm_alias_resolves_same_as_npu():
    r = resolve_chain("hal0/flm", _slots(), loaded={"qwen3-4b-FLM"})
    assert r.model_id == "qwen3-4b-FLM"


def test_empty_slots_degrades_to_blank_resolution():
    r = resolve_chain("hal0/chat", [], loaded=set())
    assert r is not None
    assert r.model_id == ""
    assert r.fallback is True


def test_empty_slots_via_primary_alias_degrades():
    """Back-compat: hal0/primary on empty slots still degrades gracefully."""
    r = resolve_chain("hal0/primary", [], loaded=set())
    assert r is not None
    assert r.model_id == ""
    assert r.fallback is True


def test_unknown_virtual_name_returns_none():
    assert resolve_chain("hal0/nope", _slots(), loaded={"big-35b"}) is None


def test_default_chains_shape():
    assert DEFAULT_CHAINS["hal0/chat"] == ("chat",)
    assert DEFAULT_CHAINS["hal0/npu"] == ("npu", "utility", "chat")
    assert DEFAULT_CHAINS["hal0/utility"] == ("utility", "npu", "chat")


@pytest.mark.asyncio
async def test_live_resolver_reads_views_and_health():
    from hal0.normalize.resolver import LiveSlotResolver, SlotView

    views = [
        SlotView(name="chat", role=None, device="gpu-vulkan", model_id="big", context_length=65536),
        SlotView(
            name="utility", role=None, device="gpu-vulkan", model_id="tiny", context_length=65536
        ),
    ]
    resolver = LiveSlotResolver(
        slot_views_provider=lambda: views,
        loaded_models_provider=lambda: {"tiny", "big"},
    )
    res = await resolver.resolve("hal0/utility")
    assert res.model_id == "tiny"
    # passthrough: non-virtual names return None so the caller leaves the body alone
    assert await resolver.resolve("some-physical-model") is None


@pytest.mark.asyncio
async def test_live_resolver_hal0_primary_alias():
    """hal0/primary is accepted by LiveSlotResolver via VIRTUAL_ALIASES."""
    from hal0.normalize.resolver import LiveSlotResolver, SlotView

    views = [
        SlotView(name="chat", role=None, device="gpu-vulkan", model_id="big", context_length=65536),
    ]
    resolver = LiveSlotResolver(
        slot_views_provider=lambda: views,
        loaded_models_provider=lambda: {"big"},
    )
    res = await resolver.resolve("hal0/primary")
    assert res is not None
    assert res.model_id == "big"


def test_agent_virtual_name_resolves_to_agent_slot():
    """hal0/agent must resolve to the slot named 'agent' (cutover #662: the
    GPU MoE agent slot). Without a DEFAULT_CHAINS entry the virtual name is
    unknown → passes through unnormalized → lemonade 404."""
    r = resolve_chain("hal0/agent", _slots(), loaded={"qwen3-4b-FLM"})
    assert r is not None
    assert r.matched_role == "agent"
    assert r.model_id == "qwen3-4b-FLM"  # the 'agent'-named slot in the fixture
    assert r.fallback is False
