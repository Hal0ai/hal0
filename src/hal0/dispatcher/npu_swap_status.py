"""NPU trio chat-model swap-in-progress detection.

When the operator picks a new NPU chat model in the dashboard, the
underlying npu container slot must:

  1. Persist the new model on the ``device=npu, type=llm`` slot's TOML.
  2. Restart ``hal0-slot@npu`` with the new ``flm serve <tag>`` argv
     (a swap = container restart on single-tenant NPU hardware).
  3. The slot transitions through PULLING/STARTING/WARMING back to READY.

The transition takes ~14s on Strix Halo (cold NPU + ASR + embed warm-up
all together). For the dashboard to render a "swap incoming" banner +
spinner, hal0 needs a single signal that says "this is the swap window":
the slot's lifecycle state. Transitional states (PULLING/STARTING/
WARMING/UNLOADING) map to ``in_progress=True``; settled states
(READY/SERVING/IDLE/OFFLINE/ERROR) map to ``in_progress=False``.

By design, only one NPU LLM slot may have a model bound at a time.
:meth:`hal0.slots.manager.SlotManager._check_npu_exclusivity` guards the
*write* path; this module observes the *runtime* state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from hal0.dispatcher._npu_common import is_container_npu_cfg
from hal0.slots.activation import claims_npu_anchor, slot_model_id
from hal0.slots.state import SlotState

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class NpuSwapStatus:
    """Snapshot of the NPU trio swap state.

    Attributes:
        in_progress: True iff the live NPU LLM container slot is in a
            transitional lifecycle state (model swap = container restart).
        from_model: Always ``None`` — a restarting container exposes no
            "previously loaded" signal; the dashboard shows the banner
            without naming the outgoing model.
        to_model: The model_name configured on the NPU LLM anchor (the
            "to" side of the swap). ``None`` when no NPU LLM slot has a
            model bound — which, post-#1369, is the same condition.
    """

    in_progress: bool
    from_model: str | None
    to_model: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "in_progress": self.in_progress,
            "from_model": self.from_model,
            "to_model": self.to_model,
        }


def _npu_llm_anchor(slot_configs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the (at most one) model-bound NPU LLM slot config, or None.

    The NPU-exclusivity validation guarantees at most one such slot exists on
    disk; we walk the list defensively anyway and return the first match. A
    multi-match would itself be a bug, surfaced upstream by the validator the
    next time the operator saves.

    ``claims_npu_anchor`` also covers the "no model bound" case that used to
    be a separate ``enabled is False`` skip (#1369) — a model-less anchor has
    no swap to report either way.
    """
    for cfg in slot_configs:
        if claims_npu_anchor(cfg):
            return cfg
    return None


#: SlotState values that indicate a container NPU slot is mid-transition
#: (model swap in progress: container restarting/loading new model).
_TRANSITIONAL_STATES: frozenset[str] = frozenset(
    {
        SlotState.PULLING.value,
        SlotState.STARTING.value,
        SlotState.WARMING.value,
        SlotState.UNLOADING.value,
    }
)


async def fetch_npu_swap_status(
    slot_configs: list[dict[str, Any]],
    *,
    slot_manager: Any | None = None,
) -> NpuSwapStatus:
    """Return the swap snapshot from the npu container slot's state.

    Transitional states (PULLING/STARTING/WARMING/UNLOADING) map to
    ``in_progress=True`` (a model swap = container restart). Settled
    states (READY/SERVING/IDLE/OFFLINE/ERROR) map to ``in_progress=False``.

    Never raises: the dashboard poll must never see a swap-status 503.
    Missing slot manager, no model-bound NPU LLM slot, a non-container NPU
    slot, or any accessor error all degrade to ``in_progress=False``.
    """
    npu_slot_cfg = _npu_llm_anchor(slot_configs)
    if npu_slot_cfg is None or slot_manager is None:
        return NpuSwapStatus(in_progress=False, from_model=None, to_model=None)
    if not is_container_npu_cfg(npu_slot_cfg):
        # Legacy/unmigrated record — no live container to observe.
        return NpuSwapStatus(
            in_progress=False,
            from_model=None,
            to_model=slot_model_id(npu_slot_cfg) or None,
        )

    to_model = slot_model_id(npu_slot_cfg) or None

    try:
        slot = await slot_manager.status(npu_slot_cfg.get("name") or "npu")
        state_val = slot.state.value
    except Exception as exc:
        log.debug(
            "npu_swap.container_status_failed",
            extra={"error": str(exc), "error_type": type(exc).__name__},
        )
        # Can't read state → treat as settled (no swap).
        return NpuSwapStatus(in_progress=False, from_model=None, to_model=to_model)

    in_progress = state_val in _TRANSITIONAL_STATES
    return NpuSwapStatus(in_progress=in_progress, from_model=None, to_model=to_model)


__all__ = [
    "NpuSwapStatus",
    "fetch_npu_swap_status",
]
