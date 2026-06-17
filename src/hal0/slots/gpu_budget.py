"""Pure GPU-budget admission decision for ComfyUI renders.

Partition the GTT ceiling into [LLM reserve | render envelope | margin].
A render coexists with inference when it fits the envelope; otherwise it needs
exclusive mode, freed by an incremental evict plan that frees the *minimum*
LLM slots needed and pins ``agent`` last so the operator brain survives where
possible.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

#: agent is the operator/Hermes brain — always the last slot to be evicted.
AGENT_SLOT = "agent"


@dataclass(frozen=True)
class AdmitDecision:
    decision: str  # "coexist" | "needs_exclusive" | "wont_fit"
    footprint_gb: float
    envelope_gb: float
    free_gb: float
    evict_plan: tuple[str, ...]


def order_evictions(loaded_llm_slots: Iterable[str], evict_priority: Sequence[str]) -> list[str]:
    """Eviction order for the loaded llm slots, ``agent`` always last."""
    loaded = [s for s in loaded_llm_slots]
    ranked = [s for s in evict_priority if s in loaded and s != AGENT_SLOT]
    # any loaded slot not named in evict_priority falls between the list and agent
    leftover = [s for s in loaded if s not in evict_priority and s != AGENT_SLOT]
    order = ranked + leftover
    if AGENT_SLOT in loaded:
        order.append(AGENT_SLOT)
    return order


def admit_render(
    footprint_gb: float,
    *,
    gtt_ceil_gb: float,
    reserve_gb: float,
    margin_gb: float,
    used_non_llm_gb: float,
    loaded_llm_slots: Sequence[str],
    llm_footprints_gb: Mapping[str, float],
    evict_priority: Sequence[str],
) -> AdmitDecision:
    envelope = gtt_ceil_gb - reserve_gb - margin_gb
    coexist_free = envelope - used_non_llm_gb
    if footprint_gb <= coexist_free:
        return AdmitDecision("coexist", footprint_gb, envelope, coexist_free, ())

    # Need exclusive: free loaded llm slots (agent last) until the render fits
    # the hard ceiling minus margin minus whatever non-llm is already resident.
    order = order_evictions(loaded_llm_slots, evict_priority)
    remaining_llm = sum(llm_footprints_gb.get(s, 0.0) for s in loaded_llm_slots)
    plan: list[str] = []
    for slot in order:
        available = gtt_ceil_gb - margin_gb - used_non_llm_gb - remaining_llm
        if footprint_gb <= available:
            break
        plan.append(slot)
        remaining_llm -= llm_footprints_gb.get(slot, 0.0)
    available = gtt_ceil_gb - margin_gb - used_non_llm_gb - remaining_llm
    if footprint_gb <= available:
        return AdmitDecision("needs_exclusive", footprint_gb, envelope, coexist_free, tuple(plan))
    return AdmitDecision("wont_fit", footprint_gb, envelope, coexist_free, ())
