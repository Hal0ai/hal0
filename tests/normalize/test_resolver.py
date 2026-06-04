import pytest

from hal0.normalize.resolver import (
    SlotView,
    DEFAULT_CHAINS,
    is_npu_or_flm,
    resolve_chain,
)


def _slots():
    return [
        SlotView(name="primary", role=None, device="gpu-vulkan", model_id="big-35b", context_length=65536),
        SlotView(name="utility", role=None, device="gpu-vulkan", model_id="tiny-0.8b", context_length=65536),
        SlotView(name="agent",   role=None, device="npu",        model_id="qwen3-4b-FLM", context_length=32768),
    ]


def test_primary_prefers_igpu_when_loaded():
    r = resolve_chain("hal0/primary", _slots(), loaded={"big-35b", "qwen3-4b-FLM"})
    assert r.model_id == "big-35b"
    assert r.context_length == 65536
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


def test_role_tag_overrides_name():
    slots = [
        SlotView(name="coder-mini", role="utility", device="gpu-vulkan", model_id="cm", context_length=8192),
        SlotView(name="primary", role=None, device="gpu-vulkan", model_id="big", context_length=65536),
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


def test_is_npu_or_flm_name_heuristic():
    assert is_npu_or_flm("qwen3-4b-FLM") is True
    assert is_npu_or_flm("big-35b") is False


def test_unknown_virtual_name_returns_none():
    assert resolve_chain("hal0/nope", _slots(), loaded={"big-35b"}) is None


def test_default_chains_shape():
    assert DEFAULT_CHAINS["hal0/primary"] == ("primary",)
    assert DEFAULT_CHAINS["hal0/npu"] == ("npu", "utility", "primary")
    assert DEFAULT_CHAINS["hal0/utility"] == ("utility", "npu", "primary")
