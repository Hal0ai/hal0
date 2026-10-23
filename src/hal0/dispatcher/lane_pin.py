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

#: Attribute marking a request that may ONLY be served by a remote upstream
#: (#2919). The memory engine's ``hal0/memory`` alias rewrites to a bare
#: upstream model id at the route layer; without this marker a cold or
#: offline catalog would let the dispatcher's step-4 heuristics land the
#: request on the agent anchor (or the flm slot for an ``a:b`` id) — memory
#: extraction silently running on the operator's chat lane while status says
#: "upstream". The dispatcher refuses to fall through when it is set.
UPSTREAM_ONLY_ATTR = "hal0_upstream_only"

#: The cheap-helper slot (ADR-0023). Keyed on the slot NAME, as the reaper's
#: pinned set is (:data:`hal0.slots.reaper._PINNED_BY_DEFAULT`): ADR-0023 fixes
#: the name, and a role/label lookup would need a config read on the dispatch
#: hot path.
UTILITY_SLOT_NAME = "utility"

__all__ = [
    "LANE_PIN_ATTR",
    "UPSTREAM_ONLY_ATTR",
    "lane_slot_pin",
    "preferred_slot",
    "rank_slot_name",
    "set_lane_pin",
    "set_upstream_only",
    "upstream_only_model",
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


def set_upstream_only(request: Any, model_id: str) -> None:
    """Mark this request as servable only by a remote upstream (see
    :data:`UPSTREAM_ONLY_ATTR`). Never raises on a request stand-in."""
    if not model_id:
        return
    with contextlib.suppress(Exception):
        setattr(request.state, UPSTREAM_ONLY_ATTR, str(model_id))


def upstream_only_model(request: Any) -> str:
    """The upstream model id this request was marked upstream-only for, or ``""``."""
    try:
        value = getattr(request.state, UPSTREAM_ONLY_ATTR, "")
    except Exception:
        return ""
    return value if isinstance(value, str) else ""


def _in_flight(slot_manager: Any, slot_name: str) -> int:
    """Requests in flight on ``slot_name`` right now; ``0`` when unknown.

    :meth:`hal0.slots.manager.SlotManager.in_flight_count` is exact and
    synchronous: ``Dispatcher.forward`` takes a dispatch ticket before its
    first await and holds ``serving()`` until a stream drains, and both are
    released on every exit path (error, cancel, client disconnect). Every chat
    request reaches a slot through it, Hindsight extraction included (it calls
    ``hal0/<slot>`` over ``/v1``). A stand-in without the method reads as idle,
    so the preference degrades to "non-utility first", never to an exception.
    """
    counter = getattr(slot_manager, "in_flight_count", None)
    if counter is None:
        return 0
    try:
        return int(counter(slot_name))
    except Exception:
        return 0


def rank_slot_name(
    slot_manager: Any,
    slot_name: str,
    *,
    lane_slot: str = "",
) -> tuple[int, int, int]:
    """Selection key for one slot — lower sorts first.

    ``(lane-miss, health-rank, preference-miss)``: the lane-pinned slot wins
    outright (the pin is only set for a model-bound slot the resolver matched in
    the chain, which the route layer loads before dispatch — #2741), then health
    ordering from :func:`hal0.slots.state.slot_selection_rank`, then the
    operator rule for #2716: among equally healthy siblings, a slot that is NOT
    ``utility`` and has no request in flight wins. Every other candidate ties at
    1, so when none qualifies (brain busy, utility idle) the caller's final
    tiebreak — declaration order — decides exactly as before. ADR-0023: utility
    is never the fallback for general chat; it runs ``--parallel 1`` and also
    serves memory extraction, so a chat turn landing there queues behind it
    while an idle sibling serving the same model id waits. A ``hal0/utility``
    lane still wins via the pin.
    """
    lane_miss = 0 if (lane_slot and slot_name == lane_slot) else 1
    try:
        state = slot_manager.state(slot_name)
    except Exception:
        # A slot whose state cannot be read is not evidence of health; rank it
        # with the last resort rather than ahead of a slot we know is serving.
        state = SlotState.ERROR
    idle_non_utility = slot_name != UTILITY_SLOT_NAME and _in_flight(slot_manager, slot_name) == 0
    return (lane_miss, slot_selection_rank(state), 0 if idle_non_utility else 1)


def preferred_slot(
    slot_manager: Any,
    candidates: Iterable[str],
    *,
    lane_slot: str = "",
) -> str | None:
    """Pick the slot most likely to serve now, or ``None`` when there are none.

    Ordering: lane pin → health rank → idle non-utility slot (#2716) →
    declaration order (see :func:`rank_slot_name`). With a single
    candidate the result is that candidate regardless of state, so the
    single-slot path (and its existing recovery-load / error envelope) is
    unchanged.

    The route layer's cold-load pick (``_ensure_backend_for_model``) shares this
    ordering, so with ``utility`` and a sibling both OFFLINE on one model id the
    sibling is the slot that gets loaded (ADR-0023), not utility.
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
