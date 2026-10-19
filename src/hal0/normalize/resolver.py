"""Live-slot model resolution for hal0 virtual model names.

Pure core (``resolve_chain``) + an async wrapper (``LiveSlotResolver``) that reads
slot config and the container slot state. The loaded-model set derives from the
slot states SlotManager already tracks — no extra polling.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import structlog

log = structlog.get_logger("hal0-normalize")

# ADR-0023: `agent` is the canonical default/anchor slot (replaces `chat`); every
# fallback chain ends in `agent`. `utility` is the explicitly-targeted cheap helper
# and is NEVER the fallback for general chat.
# Each chain entry is matched against slot `name`. `npu` is special-cased to also
# match by device class (so `hal0/npu` resolves to the trio's chat edge regardless
# of whether the slot is named `npu`, `flm`, or `npuchat`).
_ANCHOR_NAME = "agent"
_VIRTUAL_PREFIX = "hal0/"

# Canonical virtual names -> ordered chain of slot names; the first model-bound one wins.
# These three are the *advertised* names (the Hermes / OpenWebUI picker). ANY other
# enabled llm slot `X` is still addressable as `hal0/X` via the generalized chain
# built in `_chain_for` — the mapping is read from the live slot set, not frozen here.
DEFAULT_CHAINS: dict[str, tuple[str, ...]] = {
    "hal0/agent": (_ANCHOR_NAME,),
    # hal0/utility → the cheap helper slot (loaded on demand when cold); falls
    # back to the anchor only when utility has no bound model (#2741).
    "hal0/utility": ("utility", _ANCHOR_NAME),
    "hal0/npu": ("npu", "utility", _ANCHOR_NAME),
    # hal0/brain → the dashboard agent-chat persona. No installer seeds a `brain`
    # slot, so without a canonical entry the generalized chain below would pass the
    # name through unresolved and every chat turn would 404 on a stock box.
    "hal0/brain": ("brain", _ANCHOR_NAME),
}


def _chain_for(virtual_name: str, slots: list[SlotView]) -> tuple[str, ...] | None:
    """Resolve a virtual name to an ordered slot-name chain.

    Canonical names (DEFAULT_CHAINS) win. Otherwise ADR-0023 §2 generalization:
    ``hal0/<slot>`` for ANY enabled llm slot resolves to ``(<slot>, agent)`` so an
    operator-chosen slot (e.g. the memory extraction slot) is addressable without a
    hardcoded entry. Unknown / non-prefixed names return None (caller leaves the body
    model untouched).
    """
    canonical = DEFAULT_CHAINS.get(virtual_name)
    if canonical is not None:
        return canonical
    if virtual_name.startswith(_VIRTUAL_PREFIX):
        slot = virtual_name[len(_VIRTUAL_PREFIX) :]
        if slot and any(_slot_matches_name(s, slot) for s in slots):
            return (slot, _ANCHOR_NAME)
    return None


@dataclass(frozen=True)
class SlotView:
    """Minimal view of one enabled llm slot, drawn from slot config.

    Identity is the slot's ``name``. The optional ``role`` that used to live
    here was retired in favour of the slot name (slot names ARE the routing
    key for operator-visible aliases like ``hal0/agent`` / ``hal0/utility``
    / ``hal0/npu`` — there is no separate role tag the operator owns).
    """

    name: str
    device: str  # "gpu-vulkan" | "gpu-rocm" | "cpu" | "npu"
    model_id: str
    context_length: int


@dataclass(frozen=True)
class Resolution:
    model_id: str
    context_length: int
    # Chain entry that matched a model-bound slot (loaded or not), or None when
    # no chain entry could serve and the configured primary was substituted.
    matched_name: str | None
    # True => no chain entry has a model-bound slot; the configured primary
    # (normally the `agent` anchor) was substituted.
    fallback: bool
    # True => the resolved slot's model is not currently loaded; the route
    # layer's backend-aware load brings it up (#2741).
    needs_load: bool = False


def _slot_matches_name(slot: SlotView, name: str) -> bool:
    """Name-based binding with one silicon-class escape hatch.

    Plain names match the slot's ``name`` exactly (case-insensitive). The
    special name ``npu`` additionally matches any slot with ``device == "npu"``
    so a container that calls the trio's chat edge by a different slot name
    (e.g. ``flm``) still answers ``hal0/npu``. All other names match name only.
    """
    target = name.lower()
    if target == "npu" and slot.device == "npu":
        return True
    return slot.name.lower() == target


def _configured_primary(slots: list[SlotView]) -> SlotView | None:
    # ADR-0023: the anchor is the `agent` slot.
    for s in slots:
        if _slot_matches_name(s, _ANCHOR_NAME):
            return s
    # last-resort: first enabled llm slot if none is named agent.
    return slots[0] if slots else None


def resolve_chain(
    virtual_name: str,
    slots: list[SlotView],
    loaded: set[str],
) -> Resolution | None:
    """Resolve a virtual name to a live slot's physical model id.

    Returns ``None`` if ``virtual_name`` is not a known virtual name. Otherwise
    always returns a ``Resolution``: the first chain entry with a model-bound
    slot (``needs_load=True`` when that model is not loaded yet), or the
    configured primary with ``fallback=True`` when no chain entry has a
    model-bound slot. Every substitution is logged as ``resolver.fallback``.
    """
    chain = _chain_for(virtual_name, slots)
    if chain is None:
        return None

    # #2741 (ADR-0023 §2 amendment): the first chain entry whose slot has a
    # bound model wins, LOADED OR NOT. Counting only loaded entries let a warm
    # (or merely configured) agent anchor capture `hal0/utility` /
    # `hal0/brain` / `hal0/<slot>` whenever that slot was cold, so the
    # backend-aware load woke the agent instead of the slot the caller named.
    # A cold match returns ``needs_load=True`` with ``matched_name`` set, so the
    # route layer pins the lane and loads exactly that slot.
    for name in chain:
        for slot in slots:
            if not (_slot_matches_name(slot, name) and slot.model_id):
                continue
            # Contract: both sides are the canonical model_id, compared exactly.
            # The async wrapper passes the loaded model ids verbatim — exact
            # match is intended, do NOT lowercase.
            needs_load = slot.model_id not in loaded
            if needs_load:
                log.info(
                    "resolver.fallback",
                    requested=virtual_name,
                    chain=list(chain),
                    resolved_slot=slot.name,
                    reason="not_loaded_loading",
                )
            return Resolution(
                slot.model_id,
                slot.context_length,
                name,
                fallback=False,
                needs_load=needs_load,
            )

    # No chain entry has a model-bound slot (e.g. a model-less utility on a
    # fresh install, #2716): fall back to the configured primary.
    primary = _configured_primary(slots)
    if primary is not None:
        log.info(
            "resolver.fallback",
            requested=virtual_name,
            chain=list(chain),
            resolved_slot=primary.name,
            reason="no_model_bound_using_anchor",
        )
        return Resolution(
            primary.model_id,
            primary.context_length,
            None,
            fallback=True,
            needs_load=primary.model_id not in loaded,
        )
    # No slots at all: degrade to a bare model id so the caller can still 503 cleanly.
    return Resolution("", 0, None, fallback=True)


class LiveSlotResolver:
    """Async wrapper around ``resolve_chain``.

    ``slot_views_provider`` returns the current list of ``SlotView`` (built from
    slot config). ``loaded_models_provider`` returns the set of currently-loaded
    model ids derived from container slot state — no extra polling.
    """

    def __init__(
        self,
        slot_views_provider: Callable[[], list[SlotView]],
        loaded_models_provider: Callable[[], set[str]],
    ) -> None:
        self._views = slot_views_provider
        self._loaded = loaded_models_provider

    async def resolve(self, model_name: str) -> Resolution | None:
        # ADR-0023 §2: any `hal0/<slot>` may resolve (canonical or generalized), so
        # we can't pre-filter on DEFAULT_CHAINS membership — defer to resolve_chain,
        # which returns None for a non-virtual name or an unknown slot.
        if not model_name.startswith(_VIRTUAL_PREFIX):
            return None
        try:
            views = list(self._views() or [])
            loaded = set(self._loaded() or set())
        except Exception:
            views, loaded = [], set()
        return resolve_chain(model_name, views, loaded)
