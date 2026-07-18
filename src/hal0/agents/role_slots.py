"""Resolve live agent roles onto stable slot identities."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

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
    """Small slot read model used by role policy, independent of the dashboard.

    ``role_hint`` is the platform's stable semantic role binding. A mutable
    label may initially match a role, but it is never treated as identity.
    """

    model_config = ConfigDict(frozen=True)

    slot_id: str | None
    label: str | None
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


_READY_STATES = frozenset({"ready", "running", "loaded", "ok", "online"})


def _string(value: Any) -> str | None:
    return str(value) if value is not None and str(value) else None


def _capability_values(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value.lower(),) if value else ()
    if isinstance(value, Sequence):
        return tuple(str(item).lower() for item in value if item is not None and str(item))
    return ()


def candidate_from_slot_mapping(slot: Mapping[str, Any]) -> RoleSlotCandidate:
    """Parse the current slot wire mapping without manufacturing identity."""
    raw_id = slot.get("slot_id") if "slot_id" in slot else slot.get("id")
    raw_ready = slot.get("ready")
    if isinstance(raw_ready, bool):
        ready = raw_ready
    else:
        ready = str(slot.get("state") or slot.get("status") or "").lower() in _READY_STATES

    capabilities = {
        *_capability_values(slot.get("type")),
        *_capability_values(slot.get("capabilities")),
        *_capability_values(slot.get("labels")),
    }
    return RoleSlotCandidate(
        slot_id=_string(raw_id),
        label=_string(
            slot.get("label") or slot.get("name") or slot.get("alias") or slot.get("slug")
        ),
        model=_string(slot.get("model_id") or slot.get("model") or slot.get("default_model")),
        ready=ready,
        capabilities=tuple(sorted(capabilities)),
        device_class=_string(slot.get("device_class") or slot.get("device")),
        role_hint=_string(slot.get("role_hint") or slot.get("role")),
    )


def _matches(candidate: RoleSlotCandidate, role: str) -> bool:
    return candidate.role_hint == role or candidate.label == role


def _llm_capable(candidate: RoleSlotCandidate) -> bool:
    return bool({"llm", "chat"}.intersection(candidate.capabilities))


def _main_candidate(slots: Sequence[RoleSlotCandidate]) -> RoleSlotCandidate | None:
    eligible = tuple(slot for slot in slots if _llm_capable(slot))
    for label in ("agent", "chat", "primary"):
        if candidate := next((slot for slot in eligible if _matches(slot, label)), None):
            return candidate
    return next((slot for slot in eligible if slot.ready), None)


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
        (
            slot
            for slot in normalized
            if _matches(slot, "utility") and slot.ready and slot.model and _llm_capable(slot)
        ),
        None,
    )
    npu = next(
        (
            slot
            for slot in normalized
            if slot.device_class == "npu" and slot.ready and slot.model and _llm_capable(slot)
        ),
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
    payload = {
        "agent_id": agent_id,
        "entries": [entry.model_dump(mode="json") for entry in frozen_entries],
    }
    generation = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return RoleSlotMap(agent_id=agent_id, generation=generation, entries=frozen_entries)
