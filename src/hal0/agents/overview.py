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


#: ``config_write`` phase details the card lifts to the top of the summary.
_CONFIG_WRITE_KEYS = (
    "primary_model",
    "delegation_model",
    "chat_slot_count",
    "mcp_server_count",
    "memory_provider",
    "persona",
    "terminal_tool",
    "approvals_mode",
)


def provision_summary(data: dict[str, Any] | None) -> dict[str, Any]:
    """Summarise ``provision.json`` (the last provision run) for the card.

    Phases keep file order. ``ok``/``warn``/``fail`` count phases by status;
    ``skip`` counts nowhere. The ``config_write`` phase's details are lifted
    so the card does not need the raw blob. ``data`` is ``None`` when no run
    has been recorded.
    """
    data = data or {}
    phases = [
        {
            "name": name,
            "status": entry.get("status"),
            "at": entry.get("at"),
            "failure_count": entry.get("failure_count"),
            "reason": entry.get("reason"),
        }
        for name, entry in (data.get("phases") or {}).items()
    ]
    config_write = ((data.get("phases") or {}).get("config_write") or {}).get("details") or {}
    return {
        "present": bool(data),
        "hal0_version": data.get("hal0_version"),
        "hermes_version": data.get("hermes_version"),
        "completed_at": data.get("completed_at"),
        "phases": phases,
        "ok": sum(1 for p in phases if p["status"] == "ok"),
        "warn": sum(1 for p in phases if p["status"] == "warn"),
        "fail": sum(1 for p in phases if p["status"] == "fail"),
        **{key: config_write.get(key) for key in _CONFIG_WRITE_KEYS},
    }


def _ownership_row(ownership: list[dict[str, Any]]) -> dict[str, str]:
    """Collapse the per-path ownership audit into one doctor row."""
    drifted = [f"{r['label']}: {r['detail']}" for r in ownership if r["status"] == "drift"]
    if drifted:
        return {"id": "ownership", "status": "fail", "detail": "; ".join(drifted)}
    return {"id": "ownership", "status": "ok", "detail": "no ownership drift"}


def doctor_rows(
    sqlite: dict[str, str],
    plugins: dict[str, str],
    ownership: list[dict[str, Any]],
    gateway_unit: dict[str, str] | None,
) -> list[dict[str, str]]:
    """Doctor rows for the card: interpreter, plugins, ownership, gateway unit.

    ``sqlite`` and ``plugins`` are the ``{status, detail}`` results of
    ``check_hermes_sqlite`` and ``check_hermes_plugins``; ``ownership`` is the
    row list from ``check_hermes_ownership``; ``gateway_unit`` is ``None``
    when that check is not available.
    """
    rows = [
        {"id": "interpreter", "status": sqlite["status"], "detail": sqlite["detail"]},
        {"id": "plugins", "status": plugins["status"], "detail": plugins["detail"]},
        _ownership_row(ownership),
    ]
    if gateway_unit is not None:
        rows.append(
            {
                "id": "gateway_unit",
                "status": gateway_unit["status"],
                "detail": gateway_unit["detail"],
            }
        )
    return rows


#: The root-only command that repairs ownership drift (the daemon runs as
#: ``hal0`` and cannot ``chown`` the Hermes tree itself).
_OWNERSHIP_REPAIR_COMMAND = "sudo hal0 agent bootstrap hermes --repair"


def _reason(reason: str, detail: str, fix: str, command: str | None = None) -> dict[str, Any]:
    """One degraded reason: what is wrong, and the card control that fixes it."""
    return {"reason": reason, "detail": detail, "fix": fix, "command": command}


def _approvals_reason(approvals: dict[str, Any]) -> dict[str, Any]:
    """The reason for ``config.yaml`` drifting from the effective approvals mode.

    A re-sync rewrites ``config.yaml`` from the operator setting, so it only
    helps when the setting (or the default) owns the mode. When
    ``overrides.yaml`` owns it the re-sync is a no-op and the operator has to
    edit that file.
    """
    if approvals.get("source") == "overrides":
        path = approvals.get("overrides_path") or "overrides.yaml"
        return _reason(
            "approvals_drift",
            f"config.yaml does not match approvals.mode in {path}",
            "none",
        )
    return _reason(
        "approvals_drift",
        f"config.yaml does not match the setting ({approvals.get('mode')})",
        "resync_approvals",
    )


def degraded_reasons(
    *,
    units: dict[str, str],
    api_healthy: bool,
    provision: dict[str, Any],
    doctor: list[dict[str, Any]],
    approvals: dict[str, Any],
) -> list[dict[str, Any]]:
    """Why a running Hermes is degraded, in display order, each with its fix.

    ``gateway_down`` fires for any known gateway state other than ``active``;
    ``unknown`` means the probe could not answer, which is not evidence of a
    fault. ``api_unhealthy`` needs an active gateway (the API server runs
    inside it, and an inactive gateway already produced ``gateway_down``).
    ``in_sync`` of ``None`` (Hermes not provisioned) is not drift.
    """
    reasons: list[dict[str, Any]] = []
    gateway = units.get("gateway")
    if gateway not in ("active", "unknown"):
        reasons.append(
            _reason("gateway_down", f"hermes-gateway.service is {gateway}", "restart_gateway")
        )
    elif gateway == "active" and not api_healthy:
        reasons.append(
            _reason(
                "api_unhealthy",
                "the Hermes API server did not answer its health check",
                "restart_gateway",
            )
        )

    phases = provision.get("phases") or []
    failed = provision.get("fail", 0)
    if failed:
        names = [p["name"] for p in phases if p.get("status") == "fail"]
        detail = f"{failed} provision phase(s) failed"
        reasons.append(
            _reason(
                "provision_failed", f"{detail}: {', '.join(names)}" if names else detail, "repair"
            )
        )
    if provision.get("warn") or any(p.get("failure_count") for p in phases):
        reasons.append(
            _reason("provision_warn", "the last provision run recorded warnings", "none")
        )

    for row in doctor:
        if row.get("status") != "fail":
            continue
        detail = f"{row['id']}: {row['detail']}"
        if row["id"] == "ownership":
            reasons.append(_reason("doctor_drift", detail, "cli", _OWNERSHIP_REPAIR_COMMAND))
        else:
            reasons.append(_reason("doctor_drift", detail, "repair"))

    if approvals.get("in_sync") is False:
        reasons.append(_approvals_reason(approvals))
    return reasons


def hermes_phase(
    *,
    installed: bool,
    status: str,
    unit_active: bool | None,
    degraded: list[dict[str, Any]],
    job: dict[str, Any] | None,
) -> str:
    """The card's phase. First match wins.

    ``serving`` is not decided here: it needs the slot the UI holds, so the
    server answers ``ready`` and the UI upgrades it.
    """
    if not installed:
        return "not_installed"
    if job is not None and job.get("state") in ("queued", "running"):
        return "working"
    if status == "broken":
        return "broken"
    if unit_active is None:
        return "unknown"
    if not unit_active:
        return "down"
    return "degraded" if degraded else "ready"


def pi_phase(*, installed: bool, status: str) -> str:
    """Pi has no service to be up or down: it is absent, broken, or installed."""
    if not installed:
        return "not_installed"
    return "broken" if status == "broken" else "installed"
