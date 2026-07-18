"""Runtime role→slot resolution — one policy, two consumers.

Finding #2 of the Hermes integration suite design: auxiliary-role→slot
resolution (``compression`` / ``vision`` / … → a named hal0 slot) used to
run **only** at provision time inside
``hermes_provision._resolve_auxiliary_tasks``, freezing role assignments
into static Hermes config. This module lifts that policy into one place so
the live ``GET /api/agents/{agent_id}/role-slots`` endpoint and the
provisioner resolve roles identically. The endpoint owns resolution; the
provider consumes it and never reimplements role policy.

Two surfaces sit on top of the same primitives:

- ``build_auxiliary_tasks`` / ``build_delegation`` reproduce the exact
  provision-time dict shapes (task → ``{provider, model, base_url}`` and the
  ``delegation`` block), so ``hermes_provision`` delegates to them without
  behaviour change.
- ``resolve_role_slots`` produces the design's generation-stamped runtime
  mapping: one entry per role carrying the role, opaque slot id, mutable
  slot label, advertised model/alias, readiness, and capability basis.

Slot shape is the ``/api/slots`` read model (``name``/``type``/``state``/
``model_id``/``device``/``id``/``labels``); every accessor here is
defensive about key variants so both the provision fetch and the API
aggregator payload resolve the same way.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

# ── canonical role → slot policy ─────────────────────────────────────────────
#
# Kept here (not in the Hermes config template) so the resolution stays
# data-driven and a future slot rename is a one-line edit shared by both
# consumers.

#: The subagent/anchor slot. ADR-0023: the canonical primary anchor is the
#: ``agent`` slot, surfaced to Hermes as the ``hal0/agent`` virtual.
DELEGATION_SLOT_NAME = "agent"
#: The cheap side-task slot the utility-class roles route to when it is live.
UTILITY_SLOT_NAME = "utility"
#: Virtual the gateway resolves to the current primary chat slot (ADR-0023).
MAIN_ANCHOR_ALIAS = "hal0/agent"
#: Virtual the gateway resolves to the ready NPU llm slot.
NPU_UTILITY_MODEL = "hal0/npu"

_READY_STATES = frozenset({"ready", "running", "loaded", "ok", "online"})

#: Runtime role names (design finding #2 role set). ``main`` follows the
#: primary chat anchor; ``vision`` has no dedicated slot and inherits the
#: main chat model; the remaining side roles route to the ``utility`` slot
#: (NPU fallback, then a safe degrade back to the main chat model). ``approval``
#: and ``memory_flush`` are lightweight auxiliary work, so they share the
#: utility-class target the seed ``_resolve_auxiliary_tasks`` policy uses.
MAIN_ROLE = "main"
INHERIT_MAIN_ROLES: tuple[str, ...] = ("vision",)
UTILITY_ROLES: tuple[str, ...] = (
    "compression",
    "session_search",
    "skills_hub",
    "mcp",
    "approval",
    "memory_flush",
)
DEFAULT_ROLES: tuple[str, ...] = (MAIN_ROLE, *INHERIT_MAIN_ROLES, *UTILITY_ROLES)


# ── slot-field accessors (defensive over schema variants) ────────────────────


def slot_alias(slot: dict[str, Any]) -> str:
    for key in ("name", "alias", "slug"):
        v = slot.get(key)
        if isinstance(v, str) and v:
            return v
    return DELEGATION_SLOT_NAME  # ADR-0023 canonical default anchor


def slot_model_id(slot: dict[str, Any]) -> str | None:
    for key in ("model_id", "model", "default_model"):
        v = slot.get(key)
        if isinstance(v, str) and v:
            return v
    return None


def slot_id_of(slot: dict[str, Any]) -> int | None:
    """Opaque stable slot id (rework §11.1), or ``None`` before assignment."""
    v = slot.get("id")
    return v if isinstance(v, int) else None


def slot_labels(slot: dict[str, Any]) -> list[str]:
    """Capability basis for a slot — the advertised model labels.

    Sourced from the ``/api/slots`` ``labels`` field (tools / reasoning /
    vision / …). Absent → empty, so a role entry still resolves; the
    provider treats an empty basis as "unknown, probe separately".
    """
    raw = slot.get("labels")
    if isinstance(raw, (list, tuple)):
        return [str(x) for x in raw if isinstance(x, str) and x]
    return []


def is_ready(slot: dict[str, Any]) -> bool:
    """True iff the slot reports a live/ready state."""
    state = slot.get("state") or slot.get("status") or ""
    return str(state).lower() in _READY_STATES


# ── slot finders ─────────────────────────────────────────────────────────────


def find_named_ready_slot(slots: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    """Return the ready ``type=='llm'`` slot whose name matches ``name``.

    Degrade-safe: returns ``None`` when the slot is absent OR present but
    not ready/loaded OR carries no model_id, so callers fall back
    gracefully (delegation omitted; aux tasks revert to provider:"main").
    """
    for s in slots:
        if not isinstance(s, dict):
            continue
        if slot_alias(s) != name:
            continue
        if (s.get("type") or "").lower() != "llm":
            continue
        if not is_ready(s):
            continue
        if not slot_model_id(s):
            continue
        return s
    return None


def find_ready_npu_llm_slot(slots: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the ready ``type=='llm'`` slot reporting ``device`` npu, or None.

    Detects the utility role living on the NPU slot (name ``npu``, not
    ``utility``) when ``/api/slots`` doesn't expose a role tag. Callers then
    route the utility-class roles to the ``hal0/npu`` virtual.
    """
    for s in slots:
        if not isinstance(s, dict):
            continue
        if (s.get("device_class") or s.get("device") or "").lower() != "npu":
            continue
        if (s.get("type") or "").lower() != "llm":
            continue
        if not is_ready(s):
            continue
        if not slot_model_id(s):
            continue
        return s
    return None


def has_ready_npu_llm_slot(slots: list[dict[str, Any]]) -> bool:
    """Boolean form of :func:`find_ready_npu_llm_slot`."""
    return find_ready_npu_llm_slot(slots) is not None


def find_primary_slot(slots: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Select the primary chat slot backing the ``main`` role.

    Mirrors ``hermes_provision._resolve_primary_slot``'s ADR-0023
    selection: prefer a chat slot literally named ``agent``/``chat``/
    ``primary``, else the first ready chat slot. Returns the slot dict (so
    the caller can read its id / label / capabilities) or ``None`` when no
    chat slot is present.
    """

    def _is_chat(s: dict[str, Any]) -> bool:
        kind = str(s.get("type") or s.get("kind") or "").lower()
        return kind in {"llm", "chat"}

    candidates = [s for s in slots if isinstance(s, dict) and _is_chat(s)]
    primary = next((s for s in candidates if s.get("name") in ("agent", "chat", "primary")), None)
    if primary is None:
        primary = next((s for s in candidates if is_ready(s)), None)
    return primary


# ── provision-time dict shapes (delegated to from hermes_provision) ──────────


def build_auxiliary_tasks(
    slots: list[dict[str, Any]],
    *,
    hal0_base_url: str,
    main_tasks: tuple[str, ...],
    utility_tasks: tuple[str, ...],
) -> dict[str, dict[str, Any]]:
    """Build the ``auxiliary_tasks`` template dict (task → {provider, model, base_url}).

    ``main_tasks`` always render as provider:"main" (no dedicated slot).
    ``utility_tasks`` route to the ``utility`` slot when it's live; if that
    slot is missing they route to the ready NPU llm slot (``hal0/npu``), and
    only then degrade to provider:"main" so side-tasks fall back to the chat
    model rather than breaking. Resolution keys off the slot NAME
    (``utility``) and sends the slot's model_id — swapping the slot's model
    flows through on the next resolve.
    """
    tasks: dict[str, dict[str, Any]] = {}
    for task in main_tasks:
        tasks[task] = {"provider": "main", "model": "", "base_url": ""}

    utility = find_named_ready_slot(slots, UTILITY_SLOT_NAME)
    npu_utility = utility is None and has_ready_npu_llm_slot(slots)
    for task in utility_tasks:
        if utility is not None:
            tasks[task] = {
                "provider": "custom",
                "model": slot_model_id(utility),
                "base_url": hal0_base_url,
            }
        elif npu_utility:
            tasks[task] = {
                "provider": "custom",
                "model": NPU_UTILITY_MODEL,
                "base_url": hal0_base_url,
            }
        else:
            tasks[task] = {"provider": "main", "model": "", "base_url": ""}
    return tasks


def build_delegation(
    slots: list[dict[str, Any]],
    *,
    hal0_base_url: str,
) -> dict[str, Any] | None:
    """Build the ``delegation`` template dict from the ``agent`` slot.

    Returns ``{model, base_url, provider}`` when the slot is live, else
    ``None`` so the template omits the block and subagents inherit the
    parent (chat) model.
    """
    slot = find_named_ready_slot(slots, DELEGATION_SLOT_NAME)
    if slot is None:
        return None
    return {
        "model": slot_model_id(slot),
        "base_url": hal0_base_url,
        "provider": "custom",
    }


# ── runtime role-slot map (the API endpoint's read model) ────────────────────


@dataclass(frozen=True)
class RoleSlot:
    """One resolved role → slot binding in the runtime role-slot map.

    Carries everything the provider needs to follow an alias without
    reimplementing role policy: the ``role`` name, the opaque stable
    ``slot_id`` and the mutable ``slot`` label (a rename is a pure relabel
    the provider follows by id), the advertised ``model``/``alias``,
    ``ready`` readiness, and the ``capabilities`` basis. ``degraded`` +
    ``fallback`` make a non-ideal resolution (missing slot, NPU fallback,
    inherit-main degrade) observable instead of silent.
    """

    role: str
    provider: str
    base_url: str
    ready: bool
    slot: str | None = None
    slot_id: int | None = None
    model: str | None = None
    alias: str | None = None
    capabilities: tuple[str, ...] = ()
    degraded: bool = False
    fallback: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "slot": self.slot,
            "slot_id": self.slot_id,
            "model": self.model,
            "alias": self.alias,
            "provider": self.provider,
            "base_url": self.base_url,
            "ready": self.ready,
            "capabilities": list(self.capabilities),
            "degraded": self.degraded,
            "fallback": self.fallback,
        }


def _main_role_slot(role: str, primary: dict[str, Any] | None, base_url: str) -> RoleSlot:
    if primary is not None and is_ready(primary):
        return RoleSlot(
            role=role,
            provider="custom",
            base_url=base_url,
            ready=True,
            slot=slot_alias(primary),
            slot_id=slot_id_of(primary),
            model=slot_model_id(primary),
            alias=MAIN_ANCHOR_ALIAS,
            capabilities=tuple(slot_labels(primary)),
        )
    if primary is not None:
        # Slot exists but isn't ready yet (warming / pulling). The alias still
        # resolves at the gateway once it warms — surface it as degraded.
        return RoleSlot(
            role=role,
            provider="custom",
            base_url=base_url,
            ready=False,
            slot=slot_alias(primary),
            slot_id=slot_id_of(primary),
            model=slot_model_id(primary),
            alias=MAIN_ANCHOR_ALIAS,
            capabilities=tuple(slot_labels(primary)),
            degraded=True,
            fallback="primary chat slot present but not ready",
        )
    return RoleSlot(
        role=role,
        provider="custom",
        base_url=base_url,
        ready=False,
        alias=MAIN_ANCHOR_ALIAS,
        degraded=True,
        fallback="no chat slot loaded; gateway resolves hal0/agent once a model warms",
    )


def _inherit_role_slot(role: str, base_url: str, *, main_ready: bool) -> RoleSlot:
    # No dedicated slot: inherit the main chat model (provider:"main"). This is
    # not a degrade — it is the intended routing for these roles.
    return RoleSlot(
        role=role,
        provider="main",
        base_url=base_url,
        ready=main_ready,
        alias=MAIN_ANCHOR_ALIAS,
        fallback=None if main_ready else "inherits main chat model (not ready)",
    )


def _utility_role_slot(
    role: str,
    utility: dict[str, Any] | None,
    npu_slot: dict[str, Any] | None,
    base_url: str,
    *,
    main_ready: bool,
) -> RoleSlot:
    if utility is not None:
        return RoleSlot(
            role=role,
            provider="custom",
            base_url=base_url,
            ready=True,
            slot=slot_alias(utility),
            slot_id=slot_id_of(utility),
            model=slot_model_id(utility),
            alias=f"hal0/{UTILITY_SLOT_NAME}",
            capabilities=tuple(slot_labels(utility)),
        )
    if npu_slot is not None:
        return RoleSlot(
            role=role,
            provider="custom",
            base_url=base_url,
            ready=True,
            slot=slot_alias(npu_slot),
            slot_id=slot_id_of(npu_slot),
            model=NPU_UTILITY_MODEL,
            alias=NPU_UTILITY_MODEL,
            capabilities=tuple(slot_labels(npu_slot)),
            degraded=True,
            fallback="no utility slot; utility roles served by the NPU llm slot",
        )
    return RoleSlot(
        role=role,
        provider="main",
        base_url=base_url,
        ready=main_ready,
        degraded=True,
        fallback="no utility or NPU slot; inherits main chat model",
    )


def resolve_role_slots(
    slots: list[dict[str, Any]],
    *,
    base_url: str,
    roles: tuple[str, ...] = DEFAULT_ROLES,
) -> list[RoleSlot]:
    """Resolve every ``role`` to its current slot binding.

    One complete pass over the live slot inventory — same primitives the
    provision path uses — returning one :class:`RoleSlot` per role in
    ``roles`` order. Pure: no I/O, no clock, so the same inventory always
    yields the same mapping (see :func:`generation_of`).
    """
    utility = find_named_ready_slot(slots, UTILITY_SLOT_NAME)
    npu_slot = None if utility is not None else find_ready_npu_llm_slot(slots)
    primary = find_primary_slot(slots)
    main_ready = primary is not None and is_ready(primary)

    entries: list[RoleSlot] = []
    for role in roles:
        if role == MAIN_ROLE:
            entries.append(_main_role_slot(role, primary, base_url))
        elif role in INHERIT_MAIN_ROLES:
            entries.append(_inherit_role_slot(role, base_url, main_ready=main_ready))
        else:
            entries.append(
                _utility_role_slot(role, utility, npu_slot, base_url, main_ready=main_ready)
            )
    return entries


def generation_of(entries: list[RoleSlot]) -> str:
    """Content-addressed generation stamp for a resolved role-slot map.

    A stable digest over each entry's resolution-relevant fields (role,
    slot id, model, provider, alias, readiness, degrade state). The stamp
    changes iff the resolution changes — the provider compares it after an
    invalidating event to decide whether a refetch actually moved anything,
    and it doubles as an ETag-style identity for the map.
    """
    payload = [
        [e.role, e.slot_id, e.model, e.provider, e.alias, e.ready, e.degraded] for e in entries
    ]
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.blake2b(raw.encode("utf-8"), digest_size=16).hexdigest()
