"""hal0.slots — Inference slot lifecycle management.

Owns the full lifecycle of hal0-slot@<name>.service instances:
spawn, terminate, load, unload, restart, and swap.  Exposes a state
machine (SlotState) whose transitions are atomic, persisted to
/var/lib/hal0/slots/<name>/state.json, and streamable via SSE.

Port target: haloai lib/slots.py (1082 lines), refactored for the Tier 3
state machine.  See PLAN.md §3 and §5 Tier 3.

Key exports:
    SlotManager  — primary entry point for all slot operations.
    SlotState    — enum of lifecycle states (offline, ready, serving, …).
    Slot         — runtime snapshot handle returned by SlotManager methods.
"""

from __future__ import annotations

from hal0.slots.interface import DesiredSlotState, SlotInterface, SlotSnapshot
from hal0.slots.manager import Slot, SlotManager
from hal0.slots.npu import is_npu_trio_shadow
from hal0.slots.state import SELF_MANAGED_PROVIDERS, SlotState, provider_requires_model

__all__ = [
    "SELF_MANAGED_PROVIDERS",
    "DesiredSlotState",
    "Slot",
    "SlotInterface",
    "SlotManager",
    "SlotSnapshot",
    "SlotState",
    "is_npu_trio_shadow",
    "provider_requires_model",
]
