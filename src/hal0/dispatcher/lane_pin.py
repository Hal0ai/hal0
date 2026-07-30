"""Which slot a request actually asked for, and how to choose between slots (#1418).

Two small seams live here, deliberately dependency-free (only
:mod:`hal0.slots.state`) so both the ``/v1`` route layer and the dispatcher can
import them:

* **The lane pin.** ``hal0/<slot>`` is resolved to a bare model id at the route
  layer (:func:`hal0.api.routes.v1._normalize_chat_body`), and a model id says
  nothing about which slot should serve it. When two slots bind the same model
  — ``brain`` and ``nano`` both on ``hal0-brain-sft-fpx8`` on lxc105 — the lane
  silently landed on whichever slot was declared first. The pin carries the
  resolver's matched slot forward on ``request.state`` so downstream selection
  can honour the lane the caller named.

* **Candidate preference.** When several slots can serve one model id, choose by
  health, not by declaration order: an ERROR-parked slot must never be picked
  over a live sibling (that is exactly how a healthy ``brain`` slot turned into
  a ``slot.load_failed`` 502 on ``/api/brain/chat``).
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterable
from typing import Any

from hal0.slots.state import SlotState, slot_selection_rank

#: Attribute the lane pin is stored under on ``request.state``. One name, one
#: module — the producer (route layer) and consumers (backend-aware load,
#: dispatcher) can never drift onto different keys.
LANE_PIN_ATTR = "hal0_lane_slot"

__all__ = [
    "LANE_PIN_ATTR",
    "lane_slot_pin",
    "preferred_slot",
    "rank_slot_name",
    "set_lane_pin",
]


def set_lane_pin(request: Any, slot_name: str) -> None:
    """Record that this request resolved to the ``slot_name`` lane."""
    if not slot_name:
        return
    # A non-Starlette request stand-in (bare-router test, mock) has no mutable
    # ``state``; the pin is an optimisation, never a requirement.
    with contextlib.suppress(Exception):
        setattr(request.state, LANE_PIN_ATTR, str(slot_name))


def lane_slot_pin(request: Any) -> str:
    """The lane-pinned slot name for this request, or ``""``.

    Never raises and never returns a non-string: a bare-router test or a mock
    request simply has no pin.
    """
    try:
        value = getattr(request.state, LANE_PIN_ATTR, "")
    except Exception:
        return ""
    return value if isinstance(value, str) else ""


def rank_slot_name(
    slot_manager: Any,
    slot_name: str,
    *,
    lane_slot: str = "",
) -> tuple[int, int]:
    """Selection key for one slot — lower sorts first.

    ``(lane-miss, health-rank)``: the lane-pinned slot wins outright (the pin is
    only set for a slot the resolver already saw live), then health ordering
    from :func:`hal0.slots.state.slot_selection_rank`. Callers append their own
    final tiebreak (declaration index) to keep the ordering deterministic.
    """
    lane_miss = 0 if (lane_slot and slot_name == lane_slot) else 1
    try:
        state = slot_manager.state(slot_name)
    except Exception:
        # A slot whose state cannot be read is not evidence of health; rank it
        # with the last resort rather than ahead of a slot we know is serving.
        state = SlotState.ERROR
    return (lane_miss, slot_selection_rank(state))


def preferred_slot(
    slot_manager: Any,
    candidates: Iterable[str],
    *,
    lane_slot: str = "",
) -> str | None:
    """Pick the slot most likely to serve now, or ``None`` when there are none.

    Ordering: lane pin → health rank → declaration order. With a single
    candidate the result is that candidate regardless of state, so the
    single-slot path (and its existing recovery-load / error envelope) is
    unchanged.
    """
    ordered = list(candidates)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    return min(
        ordered,
        key=lambda name: (
            *rank_slot_name(slot_manager, name, lane_slot=lane_slot),
            ordered.index(name),
        ),
    )
