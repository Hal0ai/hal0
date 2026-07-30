"""Shared helper for ContainerProvider probe doubles (#1417).

Production hands the slot **config** to ``is_active`` / ``running_image`` /
``running_argv`` so the provider can derive the id-keyed instance token
(``hal0-slot@<id>``) rather than formatting the mutable slot name into a unit
that does not exist on a post-migration box. Test doubles index their state by
slot NAME, which is the same string for every fixture here (no ``id`` set →
token == name), so they lift the name back out of whatever the caller passed.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def probe_slot_name(slot: Any) -> str:
    """The slot name a probe argument refers to (config mapping or bare token)."""
    if isinstance(slot, Mapping):
        return str(slot.get("name") or "")
    return str(slot)
