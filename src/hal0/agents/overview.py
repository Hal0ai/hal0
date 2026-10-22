"""Pure builders behind the agent overview route.

Every function takes already-fetched inputs and returns plain dicts, so the
route owns all I/O and each rule here is testable on its own. The only reads
are the Pi helpers, which open the ``package.json`` files and the installer
script they are handed.
"""

from __future__ import annotations

from typing import Any

from hal0.agents.hermes_release import Release

#: Component statuses that mean the pinned Hermes is not the running one yet
#: (the Services page uses the same set; ``components.status._PENDING_STATUSES``).
_PENDING_STATUSES = frozenset(
    {"pending", "stale", "build_failed", "snapshot_failed", "rolled_back", "activation_failed"}
)


def _live_dict(release: Release) -> dict[str, Any]:
    """The live release as the overview shows it."""
    data = release.data
    return {
        "id": release.id,
        "ref": release.ref,
        "describe": data.get("describe"),
        "built_at": data.get("built_at"),
        "python_minor": release.python_minor,
        "extras": list(data.get("extras") or []),
        "platform_extras": list(data.get("platform_extras") or []),
        "hal0_version": data.get("hal0_version"),
    }


def hermes_version(
    live: Release | None,
    previous: Release | None,
    pin_ref: str,
    component: dict[str, Any] | None,
) -> dict[str, Any]:
    """Version block for the Hermes card.

    ``component`` is the Hermes row of ``component_status_snapshot()``;
    ``update_available`` follows the Services page rule: installed differs
    from pinned, or the derived status is one of the pending ones.
    """
    update_available = bool(
        component is not None
        and (
            component.get("installed") != component.get("pinned")
            or component.get("status") in _PENDING_STATUSES
        )
    )
    return {
        "live": _live_dict(live) if live is not None else None,
        "previous": (
            {"id": previous.id, "ref": previous.ref, "describe": previous.data.get("describe")}
            if previous is not None
            else None
        ),
        "pin": {"ref": pin_ref, "describe": None},
        "component": component,
        "update_available": update_available,
    }


def unit_states(agent_active: bool | None, gateway: str | None) -> dict[str, str]:
    """Systemd state of the agent unit and the gateway unit.

    ``agent_active`` is the boolean liveness probe (``None`` when systemd could
    not answer); ``gateway`` is the unit's active state string or ``None``.
    """
    agent = {True: "active", False: "inactive", None: "unknown"}[agent_active]
    return {"agent": agent, "gateway": gateway or "unknown"}
