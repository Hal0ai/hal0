"""Resolve live agent roles onto stable slot identities."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

ROLE_ORDER: tuple[str, ...] = (
    "main",
    "compression",
    "vision",
    "approval",
    "session_search",
    "memory_flush",
    "skills_hub",
    "mcp",
)
_UTILITY_ROLES = frozenset(
    {"compression", "approval", "session_search", "memory_flush", "skills_hub", "mcp"}
)


class RoleSlotCandidate(BaseModel):
    """Small slot read model used by role policy, independent of the dashboard."""

    model_config = ConfigDict(frozen=True)

    slot_id: str
    label: str
    model: str | None = None
    ready: bool
    capabilities: tuple[str, ...] = ()
    device_class: str | None = None
    role_hint: str | None = None


class RoleSlotEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: str
    slot_id: str | None
    label: str | None
    model: str | None
    ready: bool
    capabilities: tuple[str, ...]
    basis: str


class RoleSlotMap(BaseModel):
    model_config = ConfigDict(frozen=True)

    agent_id: str
    generation: str
    entries: tuple[RoleSlotEntry, ...]


def _matches(candidate: RoleSlotCandidate, role: str) -> bool:
    return candidate.role_hint == role or candidate.label == role


def _main_candidate(slots: Sequence[RoleSlotCandidate]) -> RoleSlotCandidate | None:
    for label in ("agent", "chat", "primary"):
        if candidate := next((slot for slot in slots if _matches(slot, label)), None):
            return candidate
    return next((slot for slot in slots if slot.ready and "llm" in slot.capabilities), None)


def _entry(
    role: str,
    candidate: RoleSlotCandidate | None,
    *,
    basis: str,
    model: str | None = None,
) -> RoleSlotEntry:
    if candidate is None:
        return RoleSlotEntry(
            role=role,
            slot_id=None,
            label=None,
            model=None,
            ready=False,
            capabilities=(),
            basis=basis,
        )
    return RoleSlotEntry(
        role=role,
        slot_id=candidate.slot_id,
        label=candidate.label,
        model=candidate.model if model is None else model,
        ready=candidate.ready,
        capabilities=candidate.capabilities,
        basis=basis,
    )


def resolve_role_slots(agent_id: str, slots: Sequence[RoleSlotCandidate]) -> RoleSlotMap:
    """Return the complete deterministic role map for an agent."""
    normalized = tuple(slots)
    main = _main_candidate(normalized)
    utility = next(
        (slot for slot in normalized if _matches(slot, "utility") and slot.ready and slot.model),
        None,
    )
    npu = next(
        (slot for slot in normalized if slot.device_class == "npu" and slot.ready and slot.model),
        None,
    )

    entries: list[RoleSlotEntry] = []
    for role in ROLE_ORDER:
        if role in {"main", "vision"}:
            entries.append(_entry(role, main, basis="main"))
        elif role in _UTILITY_ROLES and utility is not None:
            entries.append(_entry(role, utility, basis="utility"))
        elif role in _UTILITY_ROLES and npu is not None:
            entries.append(_entry(role, npu, basis="npu_virtual", model="hal0/npu"))
        else:
            entries.append(_entry(role, main, basis="main_fallback"))

    frozen_entries = tuple(entries)
    payload = [entry.model_dump(mode="json") for entry in frozen_entries]
    generation = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return RoleSlotMap(agent_id=agent_id, generation=generation, entries=frozen_entries)
