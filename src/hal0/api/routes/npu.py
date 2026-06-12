"""NPU-specific dashboard endpoints — PR-20.

Mounted under ``/api/npu`` (see :mod:`hal0.api.__init__`):

  - ``GET /api/npu/swap-status`` — returns whether the FLM trio's chat
    model is currently mid-swap, plus the ``from_model`` / ``to_model``
    pair when the swap is observable. Polled by the dashboard's NPU
    block to surface a "Swap incoming" banner + spinner.

Why a dedicated route (not the slot-state SSE stream): the swap-status
signal derives from slot TOML + slot lifecycle state; a simple poll
endpoint keeps the dashboard's swap banner re-polling every few seconds
without holding a long-lived connection open.

The endpoint is read-only and falls back to ``in_progress=false`` on
every error path (no SlotManager, accessor errors, etc.).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from hal0.dispatcher.npu_swap_status import (
    NpuSwapStatus,
    fetch_npu_swap_status,
)

router = APIRouter()


@router.get("/swap-status")
async def get_swap_status(request: Request) -> dict[str, Any]:
    """Return the current NPU trio chat-model swap-in-progress snapshot.

    Response shape::

        {
          "in_progress": bool,
          "from_model": "gemma3:1b" | null,
          "to_model":   "llama3.2-3b-npu" | null
        }

    Always 200. Returns ``in_progress=false`` whenever:

      - No SlotManager is wired (test bypass).
      - No NPU LLM slot is enabled.
      - The configured NPU LLM model already matches the loaded one.
    """
    sm = getattr(request.app.state, "slot_manager", None)

    if sm is None:
        # Pre-lifespan / test bypass. Return a stable empty payload so
        # the dashboard poll never sees a 500 here.
        return NpuSwapStatus(in_progress=False, from_model=None, to_model=None).to_dict()

    try:
        slot_configs = await sm.iter_configs()
    except Exception:
        # Defensive: a config read error should not propagate to the
        # status endpoint. iter_configs already swallows individual
        # malformed TOMLs (see :meth:`SlotManager.iter_configs`); this
        # catch covers the (unlikely) directory-level failure.
        slot_configs = []

    status = await fetch_npu_swap_status(slot_configs, slot_manager=sm)
    return status.to_dict()


__all__ = ["router"]
