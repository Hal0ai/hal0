"""Slot artefact naming — the ONE seam the M5 id-flip changes (§11.1 / P3-quadlet).

Every slot has three name-shaped runtime artefacts:

  * the Podman Quadlet source file  — ``hal0-slot@<token>.container`` under
    ``/etc/containers/systemd/`` (P3-quadlet: replaces the hand-rendered
    ``.service`` unit);
  * the systemd service Quadlet generates from it — ``hal0-slot@<token>.service``
    (the name every ``systemctl is-active`` / ``stop`` / ``restart`` call site
    and the ``hal0-systemctl`` seam already targets);
  * the running podman container — ``hal0-slot-<token>`` (what
    :meth:`ContainerProvider.running_image` / ``running_argv`` inspect).

The **instance token** is name-based today (``<token>`` == the slot's mutable
``name``). §11.1 flips slots to a stable opaque ``id`` primary key in the M5
downtime window; that flip is a change to :func:`slot_instance_token` (and,
if the shape changes too, the two format strings below) in THIS module ALONE —
never a renderer rewrite. That is the whole point of routing every artefact
name through one seam: the id-flip is a parameter change, not a scatter of
``f"hal0-slot@{name}..."`` edits across the tree.

Kept deliberately dependency-free so the container provider, the migrator, and
the seam can all import it without an import cycle.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def slot_instance_token(slot_cfg: Mapping[str, Any]) -> str:
    """The runtime-artefact instance token for a slot config.

    NAME-BASED today — the single place §11.1's M5 downtime window re-points to
    ``slot_cfg["id"]``. Reads the flat ``name`` or the nested ``[slot].name``
    (the two on-disk shapes) so every caller gets the identical token.
    """
    token = slot_cfg.get("name")
    if not token:
        nested = slot_cfg.get("slot")
        if isinstance(nested, Mapping):
            token = nested.get("name")
    return str(token or "")


def slot_unit_name(token: str) -> str:
    """The systemd **service** name for a slot (what ``systemctl`` verbs target).

    Kept ``hal0-slot@<token>.service`` — the shape every call site + the
    ``hal0-systemctl`` privilege seam already validate. Quadlet generates this
    unit from :func:`slot_quadlet_name` on ``daemon-reload``.
    """
    return f"hal0-slot@{token}.service"


def slot_quadlet_name(token: str) -> str:
    """The Podman Quadlet ``.container`` source filename for a slot."""
    return f"hal0-slot@{token}.container"


def slot_container_name(token: str) -> str:
    """The running podman container name (Quadlet ``ContainerName=`` default).

    ``hal0-slot-<token>`` — unchanged from the pre-Quadlet ``--name=`` value, so
    ``running_image`` / ``running_argv`` inspect the same container.
    """
    return f"hal0-slot-{token}"


__all__ = [
    "slot_container_name",
    "slot_instance_token",
    "slot_quadlet_name",
    "slot_unit_name",
]
