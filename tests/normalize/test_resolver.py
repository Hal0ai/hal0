import pytest
from structlog.testing import capture_logs

from hal0.normalize.resolver import (
    DEFAULT_CHAINS,
    SlotView,
    resolve_chain,
)


def _slots():
    # ADR-0023: the anchor slot is `agent` (replaces the old `chat` anchor).
    return [
        SlotView(name="agent", device="gpu-vulkan", model_id="big-35b", context_length=65536),
        SlotView(
            name="utility",
            device="gpu-vulkan",
            model_id="tiny-0.8b",
            context_length=65536,
        ),
        SlotView(name="npu-slot", device="npu", model_id="qwen3-4b-FLM", context_length=32768),
    ]


def test_agent_prefers_igpu_when_loaded():
    r = resolve_chain("hal0/agent", _slots(), loaded={"big-35b", "qwen3-4b-FLM"})
    assert r.model_id == "big-35b"
    assert r.context_length == 65536
    assert r.fallback is False


def test_removed_aliases_are_unknown():
    """The hal0/primary and hal0/flm aliases were removed — they are no longer
    known virtual names, so resolve_chain returns None (callers leave the body
    alone). Use the canonical hal0/agent and hal0/npu instead."""
    assert resolve_chain("hal0/primary", _slots(), loaded={"big-35b"}) is None
    assert resolve_chain("hal0/flm", _slots(), loaded={"qwen3-4b-FLM"}) is None


def test_brain_falls_back_to_agent_when_no_brain_slot():
    """hal0/brain is the dashboard agent-chat default. No installer seeds a
    `brain` slot, so the canonical chain must degrade to the agent anchor on a
    stock box instead of passing the name through unresolved (404)."""
    r = resolve_chain("hal0/brain", _slots(), loaded={"big-35b"})
    assert r is not None
    assert r.model_id == "big-35b"
    assert r.matched_name == "agent"


def test_brain_prefers_brain_slot_when_present():
    slots = [
        *_slots(),
        SlotView(name="brain", device="gpu-vulkan", model_id="brainy-8b", context_length=32768),
    ]
    r = resolve_chain("hal0/brain", slots, loaded={"brainy-8b", "big-35b"})
    assert r.model_id == "brainy-8b"
    assert r.matched_name == "brain"


def test_npu_picks_npu_first_never_commandeers_agent():
    r = resolve_chain("hal0/npu", _slots(), loaded={"qwen3-4b-FLM", "big-35b"})
    assert r.model_id == "qwen3-4b-FLM"
    assert r.matched_name == "npu"


def test_npu_falls_to_utility_before_agent():
    """With no npu-device slot at all, hal0/npu walks on to utility, not agent."""
    slots = [s for s in _slots() if s.device != "npu"]
    r = resolve_chain("hal0/npu", slots, loaded={"tiny-0.8b", "big-35b"})
    assert r.model_id == "tiny-0.8b"
    assert r.matched_name == "utility"


def test_npu_cold_bound_npu_slot_wins_over_warm_utility():
    """#2741: a model-bound npu slot is loaded on demand; a warm utility further
    down the chain does not capture the alias."""
    r = resolve_chain("hal0/npu", _slots(), loaded={"tiny-0.8b", "big-35b"})
    assert r.model_id == "qwen3-4b-FLM"
    assert r.matched_name == "npu"
    assert r.fallback is False
    assert r.needs_load is True


def test_utility_cold_bound_slot_wins_over_warm_agent():
    """#2741: hal0/utility chain is (utility, agent). A model-bound utility that
    is unloaded is loaded on demand; a warm agent anchor does NOT capture it."""
    r = resolve_chain("hal0/utility", _slots(), loaded={"big-35b"})
    assert r.model_id == "tiny-0.8b"
    assert r.matched_name == "utility"
    assert r.fallback is False
    assert r.needs_load is True


def test_utility_without_bound_slot_falls_to_agent_anchor():
    """No model-bound utility in the views: the chain walks on to agent (NOT npu
    — npu is not in the utility chain)."""
    slots = [s for s in _slots() if s.name != "utility"]
    r = resolve_chain("hal0/utility", slots, loaded={"big-35b"})
    assert r.model_id == "big-35b"
    assert r.matched_name == "agent"
    assert r.fallback is False
    assert r.needs_load is False


def test_utility_prefers_utility_when_loaded():
    r = resolve_chain("hal0/utility", _slots(), loaded={"tiny-0.8b", "big-35b"})
    assert r.model_id == "tiny-0.8b"
    assert r.matched_name == "utility"


def test_npu_name_matches_any_npu_device_slot():
    """The npu virtual name matches ANY slot with device == "npu", regardless
    of slot name. The agent anchor is loaded but the npu device slot wins
    because npu is the head of the chain. (Under the old role-based resolver
    the routing key was the deprecated `role` tag; the new resolver uses
    slot name + a silicon-class escape hatch for npu.)"""
    slots = [
        SlotView(
            name="flm",  # deliberately NOT named "npu"
            device="npu",
            model_id="qwen3-4b-FLM",
            context_length=32768,
        ),
        SlotView(name="agent", device="gpu-vulkan", model_id="big", context_length=65536),
    ]
    r = resolve_chain("hal0/npu", slots, loaded={"qwen3-4b-FLM", "big"})
    assert r.model_id == "qwen3-4b-FLM"
    assert r.matched_name == "npu"  # chain entry, not slot name


def test_utility_chain_does_not_pick_non_utility_named_slot():
    """A slot with name "coder-mini" does NOT match hal0/utility even
    when the agent anchor is loaded — utility chain is (utility, agent),
    name-based. coder-mini is neither, so the chain falls through to
    agent. (Under the old role-based resolverwould
    have matched; name-based routing is stricter.)"""
    slots = [
        SlotView(
            name="coder-mini",
            device="gpu-vulkan",
            model_id="cm",
            context_length=8192,
        ),
        SlotView(name="agent", device="gpu-vulkan", model_id="big", context_length=65536),
    ]
    r = resolve_chain("hal0/utility", slots, loaded={"cm", "big"})
    assert r.model_id == "big"
    assert r.matched_name == "agent"


def _utility_and_agent():
    return [
        SlotView(name="utility", device="gpu-vulkan", model_id="a", context_length=8192),
        SlotView(name="agent", device="gpu-vulkan", model_id="b", context_length=65536),
    ]


def test_cold_utility_resolves_to_utility_not_agent():
    """#2741 acceptance: nothing loaded, utility bound to its own model -> the
    utility slot is the answer (loaded on demand), never the agent anchor."""
    r = resolve_chain("hal0/utility", _utility_and_agent(), loaded=set())
    assert r.model_id == "a"
    assert r.context_length == 8192
    assert r.matched_name == "utility"
    assert r.fallback is False
    assert r.needs_load is True


def test_cold_utility_logs_load_substitution():
    with capture_logs() as logs:
        resolve_chain("hal0/utility", _utility_and_agent(), loaded=set())
    recs = [e for e in logs if e.get("event") == "resolver.fallback"]
    assert len(recs) == 1
    assert recs[0]["log_level"] == "info"
    assert recs[0]["requested"] == "hal0/utility"
    assert recs[0]["chain"] == ["utility", "agent"]
    assert recs[0]["resolved_slot"] == "utility"
    assert recs[0]["reason"] == "not_loaded_loading"


def test_loaded_utility_unchanged():
    """#2741 acceptance: utility loaded -> same answer as before the fix."""
    with capture_logs() as logs:
        r = resolve_chain("hal0/utility", _utility_and_agent(), loaded={"a", "b"})
    assert r.model_id == "a"
    assert r.matched_name == "utility"
    assert r.fallback is False
    assert r.needs_load is False
    assert not [e for e in logs if e.get("event") == "resolver.fallback"]


def test_modelless_utility_falls_back_to_agent_and_logs():
    """#2741 acceptance: a model-less utility is absent from the views
    (hal0_llm_slot_views drops unbound slots), so the request lands on the
    agent's model. Agent is the chain's own second link, so it matches by name
    and carries the agent lane pin."""
    slots = [SlotView(name="agent", device="gpu-vulkan", model_id="b", context_length=65536)]
    with capture_logs() as logs:
        r = resolve_chain("hal0/utility", slots, loaded=set())
    assert r.model_id == "b"
    assert r.matched_name == "agent"
    assert r.needs_load is True
    recs = [e for e in logs if e.get("event") == "resolver.fallback"]
    assert [e["reason"] for e in recs] == ["not_loaded_loading"]
    assert recs[0]["resolved_slot"] == "agent"


def test_full_miss_falls_back_to_configured_primary():
    """No chain entry has a bound model: fall back to the configured primary
    with ``fallback=True`` and log ``no_model_bound_using_anchor``. Fresh
    install (#2716): agent and utility are model-less, only brain is bound."""
    slots = [SlotView(name="brain", device="gpu-vulkan", model_id="lfm", context_length=32768)]
    with capture_logs() as logs:
        r = resolve_chain("hal0/utility", slots, loaded=set())
    assert r.model_id == "lfm"
    assert r.matched_name is None
    assert r.fallback is True
    recs = [e for e in logs if e.get("event") == "resolver.fallback"]
    assert len(recs) == 1
    assert recs[0]["reason"] == "no_model_bound_using_anchor"
    assert recs[0]["resolved_slot"] == "brain"


def test_empty_slots_degrades_to_blank_resolution():
    r = resolve_chain("hal0/agent", [], loaded=set())
    assert r is not None
    assert r.model_id == ""
    assert r.fallback is True


def test_unknown_virtual_name_returns_none():
    assert resolve_chain("hal0/nope", _slots(), loaded={"big-35b"}) is None


def test_generalized_custom_slot_resolves():
    """ADR-0023 §2: any enabled llm slot X is addressable as hal0/X with chain
    (X, agent), even without a DEFAULT_CHAINS entry."""
    slots = [
        SlotView(name="agent", device="gpu-vulkan", model_id="big", context_length=65536),
        SlotView(name="coder", device="gpu-vulkan", model_id="coder-30b", context_length=32768),
    ]
    r = resolve_chain("hal0/coder", slots, loaded={"coder-30b"})
    assert r is not None
    assert r.matched_name == "coder"
    assert r.model_id == "coder-30b"
    assert r.fallback is False


@pytest.mark.parametrize("loaded", [set(), {"big"}])
def test_generalized_cold_custom_slot_loads_that_slot(loaded):
    """#2741: a generalized hal0/<slot> whose slot is model-bound but cold
    resolves to that slot (loaded on demand), whether or not agent is warm."""
    slots = [
        SlotView(name="agent", device="gpu-vulkan", model_id="big", context_length=65536),
        SlotView(name="coder", device="gpu-vulkan", model_id="coder-30b", context_length=32768),
    ]
    r = resolve_chain("hal0/coder", slots, loaded=loaded)
    assert r.model_id == "coder-30b"
    assert r.matched_name == "coder"
    assert r.fallback is False
    assert r.needs_load is True


def test_default_chains_shape():
    assert DEFAULT_CHAINS["hal0/agent"] == ("agent",)
    assert DEFAULT_CHAINS["hal0/npu"] == ("npu", "utility", "agent")
    assert DEFAULT_CHAINS["hal0/utility"] == ("utility", "agent")
    assert "hal0/chat" not in DEFAULT_CHAINS


@pytest.mark.asyncio
async def test_live_resolver_reads_views_and_health():
    from hal0.normalize.resolver import LiveSlotResolver, SlotView

    views = [
        SlotView(name="agent", device="gpu-vulkan", model_id="big", context_length=65536),
        SlotView(name="utility", device="gpu-vulkan", model_id="tiny", context_length=65536),
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
async def test_live_resolver_rejects_removed_alias():
    """hal0/primary is no longer a virtual name — LiveSlotResolver returns None
    (passthrough) so the caller leaves a literal 'hal0/primary' body alone."""
    from hal0.normalize.resolver import LiveSlotResolver, SlotView

    views = [
        SlotView(name="agent", device="gpu-vulkan", model_id="big", context_length=65536),
    ]
    resolver = LiveSlotResolver(
        slot_views_provider=lambda: views,
        loaded_models_provider=lambda: {"big"},
    )
    assert await resolver.resolve("hal0/primary") is None


def test_agent_virtual_name_resolves_to_agent_slot():
    """hal0/agent must resolve to the slot named 'agent' (the GPU MoE anchor)."""
    r = resolve_chain("hal0/agent", _slots(), loaded={"big-35b"})
    assert r is not None
    assert r.matched_name == "agent"
    assert r.model_id == "big-35b"
    assert r.fallback is False
