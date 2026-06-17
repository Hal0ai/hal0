from __future__ import annotations

from hal0.slots.gpu_budget import AdmitDecision, admit_render, order_evictions

_LLM = {"agent": 18.0, "chat": 15.0}


def _admit(fp: float, *, used=0.0, loaded=("agent", "chat")):
    return admit_render(
        fp,
        gtt_ceil_gb=96.0,
        reserve_gb=33.0,
        margin_gb=6.0,
        used_non_llm_gb=used,
        loaded_llm_slots=loaded,
        llm_footprints_gb=_LLM,
        evict_priority=["utility", "chat"],
    )


def test_order_evictions_pins_agent_last() -> None:
    order = order_evictions(["agent", "chat", "utility"], ["utility", "chat"])
    assert order == ["utility", "chat", "agent"]


def test_small_render_coexists() -> None:
    d = _admit(8.0)
    assert d.decision == "coexist"
    assert d.evict_plan == ()
    assert abs(d.envelope_gb - 57.0) < 0.01  # 96 - 33 - 6


def test_render_just_over_envelope_evicts_chat_not_agent() -> None:
    # envelope 57; render 65 doesn't coexist. ceil-margin-remaining must cover it.
    # free chat (+15): available = 96-6-(agent 18) = 72 >= 65 → stop, agent kept.
    d = _admit(65.0)
    assert d.decision == "needs_exclusive"
    assert d.evict_plan == ("chat",)


def test_heavy_render_evicts_chat_then_agent_last() -> None:
    # render 80: after chat → 72 < 80; after agent too → 96-6 = 90 >= 80.
    d = _admit(80.0)
    assert d.decision == "needs_exclusive"
    assert d.evict_plan == ("chat", "agent")


def test_render_exceeding_total_wont_fit() -> None:
    d = _admit(95.0)  # > 96 - 6 margin
    assert d.decision == "wont_fit"
