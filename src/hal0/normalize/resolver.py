"""Live-slot model resolution for hal0 virtual model names.

Pure core (``resolve_chain``) + an async wrapper (``LiveSlotResolver``) that reads
slot config and the cached lemond health snapshot. No new lemond polling — the
wrapper reuses ``MetricsShim._health`` (see hal0-api lifespan).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

# Canonical virtual names -> ordered chain of roles to try against loaded slots.
DEFAULT_CHAINS: dict[str, tuple[str, ...]] = {
    "hal0/chat": ("chat",),
    "hal0/npu": ("npu", "utility", "chat"),
    "hal0/utility": ("utility", "npu", "chat"),
}

# Aliases that resolve to a canonical name before chain lookup.
VIRTUAL_ALIASES: dict[str, str] = {
    "hal0/flm": "hal0/npu",
    # Back-compat: hal0/primary was the pre-v0.4 name for hal0/chat.
    "hal0/primary": "hal0/chat",
}


@dataclass(frozen=True)
class SlotView:
    """Minimal view of one enabled llm slot, drawn from slot config."""

    name: str
    role: str | None
    device: str  # "gpu-vulkan" | "gpu-rocm" | "cpu" | "npu"
    model_id: str
    context_length: int


@dataclass(frozen=True)
class Resolution:
    model_id: str
    context_length: int
    matched_role: str | None  # role that matched a loaded slot, or None on fallback
    fallback: bool  # True => nothing in the chain was loaded; caller should ensure-load


def _slot_matches_role(slot: SlotView, role: str) -> bool:
    """Authoritative role binding: device for npu, role tag (else name) for primary/utility."""
    if role == "npu":
        return slot.device == "npu" or (slot.role or "").lower() == "npu"
    effective = (slot.role or slot.name).lower()
    return effective == role


def _configured_primary(slots: list[SlotView]) -> SlotView | None:
    # Prefer canonical "chat" role; also accept legacy "primary" name/role
    # from slots that haven't been migrated yet.
    for role in ("chat", "primary"):
        for s in slots:
            if _slot_matches_role(s, role):
                return s
    # last-resort: first enabled llm slot if none is tagged/named chat/primary
    return slots[0] if slots else None


def resolve_chain(
    virtual_name: str,
    slots: list[SlotView],
    loaded: set[str],
) -> Resolution | None:
    """Resolve a virtual name to a live slot's physical model id.

    Returns ``None`` if ``virtual_name`` is not a known virtual name. Otherwise
    always returns a ``Resolution`` (falling back to the configured primary,
    ``fallback=True``, when no chain role is currently loaded).
    """
    canonical = VIRTUAL_ALIASES.get(virtual_name, virtual_name)
    chain = DEFAULT_CHAINS.get(canonical)
    if chain is None:
        return None

    for role in chain:
        for slot in slots:
            # Contract: both sides are the canonical lemond model_id, compared exactly.
            # The async wrapper passes lemond's loaded model_names verbatim — exact
            # match is intended, do NOT lowercase.
            if _slot_matches_role(slot, role) and slot.model_id in loaded:
                return Resolution(slot.model_id, slot.context_length, role, fallback=False)

    primary = _configured_primary(slots)
    if primary is not None:
        return Resolution(primary.model_id, primary.context_length, None, fallback=True)
    # No slots at all: degrade to a bare model id so the caller can still 503 cleanly.
    return Resolution("", 0, None, fallback=True)


class LiveSlotResolver:
    """Async wrapper around ``resolve_chain``.

    ``slot_views_provider`` returns the current list of ``SlotView`` (built from
    slot config). ``loaded_models_provider`` returns the set of currently-loaded
    model ids from the cached health snapshot — NO new lemond poll.
    """

    def __init__(
        self,
        slot_views_provider: Callable[[], list[SlotView]],
        loaded_models_provider: Callable[[], set[str]],
    ) -> None:
        self._views = slot_views_provider
        self._loaded = loaded_models_provider

    async def resolve(self, model_name: str) -> Resolution | None:
        if model_name not in DEFAULT_CHAINS and model_name not in VIRTUAL_ALIASES:
            return None
        try:
            views = list(self._views() or [])
            loaded = set(self._loaded() or set())
        except Exception:
            views, loaded = [], set()
        return resolve_chain(model_name, views, loaded)
