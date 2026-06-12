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

Per ADR-0008 §5, only one NPU LLM slot may be enabled at a time.
:meth:`hal0.slots.manager.SlotManager._check_npu_exclusivity` guards the
*write* path; this module observes the *runtime* state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from hal0.dispatcher._npu_common import is_container_npu_cfg
from hal0.slots.state import SlotState

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class NpuSwapStatus:
    """Snapshot of the NPU trio swap state.

    Attributes:
        in_progress: True iff the enabled NPU LLM container slot is in a
            transitional lifecycle state (model swap = container restart).
        from_model: Always ``None`` — a restarting container exposes no
            "previously loaded" signal; the dashboard shows the banner
            without naming the outgoing model.
        to_model: The model_name configured on the enabled NPU LLM
            slot (the "to" side of the swap). ``None`` when no NPU
            LLM slot is enabled, or when its ``model.default`` is empty.
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


def _enabled_npu_llm_slot(slot_configs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the (at most one) enabled NPU LLM slot config, or None.

    The NPU-exclusivity validation guarantees at most one such slot
    exists on disk; we walk the list defensively anyway and return the
    first match. A multi-match would itself be a bug, surfaced upstream
    by the validator the next time the operator saves.
    """
    for cfg in slot_configs:
        if cfg.get("device") != "npu":
            continue
        if cfg.get("type") != "llm":
            continue
        if cfg.get("enabled") is False:
            continue
        return cfg
    return None


def _slot_model_default(slot_cfg: dict[str, Any]) -> str:
    """Pull ``model.default`` out of a slot config dict."""
    model_section = slot_cfg.get("model")
    if isinstance(model_section, dict):
        default = model_section.get("default")
        if isinstance(default, str):
            return default
    return ""


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
    Missing slot manager, no enabled NPU LLM slot, a non-container NPU
    slot, or any accessor error all degrade to ``in_progress=False``.
    """
    npu_slot_cfg = _enabled_npu_llm_slot(slot_configs)
    if npu_slot_cfg is None or slot_manager is None:
        return NpuSwapStatus(in_progress=False, from_model=None, to_model=None)
    if not is_container_npu_cfg(npu_slot_cfg):
        # Legacy/unmigrated record — no live container to observe.
        return NpuSwapStatus(
            in_progress=False,
            from_model=None,
            to_model=_slot_model_default(npu_slot_cfg) or None,
        )

    to_model = _slot_model_default(npu_slot_cfg) or None

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
