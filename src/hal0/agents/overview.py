"""Pure builders behind the agent overview route.

Every function takes already-fetched inputs and returns plain dicts, so the
route owns all I/O and each rule here is testable on its own. The only reads
are the Pi helpers, which open the npm ``package.json`` files, the installer
script and the profiled home they are handed.
"""

from __future__ import annotations

import json
import os
import re
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from packaging.version import InvalidVersion, Version

from hal0.components.status import _PENDING_STATUSES

if TYPE_CHECKING:
    from hal0.agents.hermes_release import Release


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


#: ``UnitFileState`` values that mean the operator turned the gateway off
#: (``systemctl disable`` / ``mask``; ADR-0034 keeps both supported), and the
#: ones that mean it is meant to run.
_GATEWAY_OFF_FILE_STATES = frozenset({"disabled", "masked", "masked-runtime"})
_GATEWAY_ON_FILE_STATES = frozenset(
    {"enabled", "enabled-runtime", "static", "linked", "generated", "transient"}
)


def unit_file_enabled(unit_file_state: str | None) -> bool | None:
    """Whether a unit's ``UnitFileState`` says it is meant to run.

    ``False`` for ``disabled``, ``masked`` and ``masked-runtime``; ``True`` for
    ``enabled``, ``enabled-runtime``, ``static``, ``linked``, ``generated`` and
    ``transient``; ``None`` for anything else (including ``unknown``).
    """
    if unit_file_state in _GATEWAY_OFF_FILE_STATES:
        return False
    if unit_file_state in _GATEWAY_ON_FILE_STATES:
        return True
    return None


def unit_states(
    agent_active: bool | None, gateway: str | None, *, gateway_enabled: bool | None = None
) -> dict[str, Any]:
    """Systemd state of the agent unit and the gateway unit.

    ``agent_active`` is the boolean liveness probe (``None`` when systemd could
    not answer); ``gateway`` is the unit's active state string or ``None``.
    ``gateway_enabled`` says whether the gateway is meant to run (see
    :func:`unit_file_enabled`); ``None`` when that is not known.
    """
    agent = {True: "active", False: "inactive", None: "unknown"}[agent_active]
    return {"agent": agent, "gateway": gateway or "unknown", "gateway_enabled": gateway_enabled}


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


def parse_timestamp(value: object) -> datetime | None:
    """An ISO-8601 timestamp as an aware datetime, or ``None`` when it is not one.

    A trailing ``Z`` is read as UTC and a naive time is taken to be UTC, so
    values written by different hal0 components compare.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def provision_summary(data: dict[str, Any] | None) -> dict[str, Any]:
    """Summarise ``provision.json`` (the last provision run) for the card.

    Phases keep file order. ``ok``/``warn``/``fail`` count phases by status;
    ``skip`` counts nowhere. The ``config_write`` phase's details are lifted
    so the card does not need the raw blob. ``reported_at`` is the newest
    phase time (as written), ``None`` when no phase carries a parseable one.
    ``data`` is ``None`` when no run has been recorded.
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
    stamped = [(t, p["at"]) for p in phases if (t := parse_timestamp(p["at"])) is not None]
    newest = max(stamped, key=lambda pair: pair[0], default=None)
    return {
        "present": bool(data),
        "hal0_version": data.get("hal0_version"),
        "hermes_version": data.get("hermes_version"),
        "completed_at": data.get("completed_at"),
        "reported_at": newest[1] if newest else None,
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

#: The root-only command that installs the gateway unit. The daemon cannot: the
#: unit lives under ``/etc/systemd/system``.
_GATEWAY_INSTALL_COMMAND = "sudo hal0 agent install hermes"

#: Doctor rows whose fix is a root CLI command, with that command. Every other
#: row is fixed by a Repair job.
_DOCTOR_CLI_FIXES = {
    "ownership": _OWNERSHIP_REPAIR_COMMAND,
    "gateway_unit": _GATEWAY_INSTALL_COMMAND,
}


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


def _doctor_fix(row: dict[str, Any]) -> tuple[str, str | None]:
    """The fix for one drifted doctor row: ``(fix, command)``."""
    if row["id"] == "gateway_unit" and row["status"] == "unreadable":
        return "none", None  # a read error is not a missing unit
    command = _DOCTOR_CLI_FIXES.get(row["id"])
    return ("cli", command) if command else ("repair", None)


def degraded_reasons(
    *,
    units: dict[str, Any],
    api_healthy: bool,
    provision: dict[str, Any],
    doctor: list[dict[str, Any]],
    approvals: dict[str, Any],
    gateway_settling: bool = False,
    gateway_note: str | None = None,
) -> list[dict[str, Any]]:
    """Why a running Hermes is degraded, in display order, each with its fix.

    ``gateway_down`` fires when the gateway is in a state other than ``active``,
    ``unknown`` or ``absent``, unless ``units["gateway_enabled"]`` is ``False``:
    an operator who disabled or masked the gateway (ADR-0034 keeps both) wants
    it off, and the card shows that from ``units``. ``unknown`` means the probe
    could not answer, which is not evidence of a fault. ``absent`` (no unit file)
    raises nothing here: the ``gateway_unit`` doctor row covers it, with the
    command that installs the unit. ``api_unhealthy`` needs an active gateway
    (the API server runs inside it, and an inactive gateway already produced
    ``gateway_down``) and is not raised while ``gateway_settling`` (the gateway
    is starting, reloading or in maintenance, so its API may not answer yet).
    ``gateway_note``, when given, replaces the state in the ``gateway_down``
    detail (``crash-looping (auto-restart)``).
    ``doctor_drift`` covers every doctor row whose status is not ``ok`` or
    ``absent``, one reason per distinct fix: the ``ownership`` row needs a root
    CLI command (hal0 cannot ``chown`` its own tree), the ``gateway_unit`` row
    another (hal0 cannot write ``/etc/systemd/system``), and the rest are fixed
    by a Repair job. A ``gateway_unit`` row that is ``unreadable`` (the check
    could not read Hermes' home) is a read error, not a missing unit, and has
    no fix. ``in_sync`` of ``None`` (Hermes not provisioned) is not
    drift. A ``provision`` marked ``stale`` (its report predates the live
    release, which ``hal0 update`` rebuilds without rewriting
    ``provision.json``) raises neither ``provision_failed`` nor
    ``provision_warn``.
    """
    reasons: list[dict[str, Any]] = []
    gateway = units.get("gateway") or "unknown"
    if gateway not in ("active", "unknown", "absent"):
        if units.get("gateway_enabled") is not False:
            detail = f"hermes-gateway.service is {gateway_note or gateway}"
            reasons.append(_reason("gateway_down", detail, "restart_gateway"))
    elif gateway == "active" and not api_healthy and not gateway_settling:
        reasons.append(
            _reason(
                "api_unhealthy",
                "the Hermes API server did not answer its health check",
                "restart_gateway",
            )
        )

    stale = bool(provision.get("stale"))
    phases = provision.get("phases") or []
    failed = 0 if stale else provision.get("fail", 0)
    if failed:
        names = [p["name"] for p in phases if p.get("status") == "fail"]
        detail = f"{failed} provision phase(s) failed"
        reasons.append(
            _reason(
                "provision_failed", f"{detail}: {', '.join(names)}" if names else detail, "repair"
            )
        )
    if not stale and (provision.get("warn") or any(p.get("failure_count") for p in phases)):
        reasons.append(
            _reason("provision_warn", "the last provision run recorded warnings", "none")
        )

    # Group the drifted rows by fix, in row order, so each reason names one command.
    fixes: dict[tuple[str, str | None], list[dict[str, Any]]] = {}
    for row in doctor:
        if row.get("status") not in ("ok", "absent"):
            fixes.setdefault(_doctor_fix(row), []).append(row)
    for (fix, command), rows in fixes.items():
        detail = "; ".join(f"{row['id']}: {row['detail']}" for row in rows)
        reasons.append(_reason("doctor_drift", detail, fix, command))

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


# ── Pi ───────────────────────────────────────────────────────────────────────

_PI_PACKAGE = "@earendil-works/pi-coding-agent"
_ADAPTER_PACKAGE = "pi-mcp-adapter"

#: The versions of Pi and ``pi-mcp-adapter`` that read ``~/.pi/agent/mcp.json``
#: (see ``PiDriver._write_mcp_config``): the adapter from 5.0, on Pi from 0.99.
_ADAPTER_MIN = Version("5.0")
_PI_MIN_FOR_ADAPTER = Version("0.99")

_PI_PIN_RE = re.compile(r'^PI_PKG="[^"]*@(?P<version>[^"@]+)"', re.MULTILINE)
_ADAPTER_PIN_RE = re.compile(r'^ADAPTER_PKG="[^"]*@(?P<version>[^"@]+)"', re.MULTILINE)


def _package_json(path: Path) -> dict[str, Any] | None:
    """A parsed ``package.json``, or ``None`` when it is missing or not an object."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def pi_installed_versions(pi_binary: Path | None) -> dict[str, str | None] | None:
    """Installed Pi and ``pi-mcp-adapter`` versions, read from the npm tree.

    ``pi_binary`` is the ``pi`` executable on ``PATH``, an npm symlink into
    ``<root>/@earendil-works/pi-coding-agent/``. Resolve it, walk up to the
    package's own ``package.json``, and read the adapter's next to it under
    the same npm root. ``None`` when there is no binary or it is not an npm
    install of Pi.
    """
    if pi_binary is None:
        return None
    for parent in Path(os.path.realpath(pi_binary)).parents:
        pkg = _package_json(parent / "package.json")
        if pkg is None or pkg.get("name") != _PI_PACKAGE:
            continue
        version = pkg.get("version")
        if not isinstance(version, str):
            return None
        adapter = _package_json(parent.parent.parent / _ADAPTER_PACKAGE / "package.json")
        adapter_version = adapter.get("version") if adapter else None
        return {
            "pi": version,
            "adapter": adapter_version if isinstance(adapter_version, str) else None,
        }
    return None


def pi_pins(script: Path) -> dict[str, str]:
    """The Pi and adapter versions the installer script pins.

    Raises ``ValueError`` when either pin is missing from ``script``.
    """
    text = script.read_text(encoding="utf-8")
    pi = _PI_PIN_RE.search(text)
    adapter = _ADAPTER_PIN_RE.search(text)
    if pi is None or adapter is None:
        missing = "PI_PKG" if pi is None else "ADAPTER_PKG"
        raise ValueError(f"{script} does not pin {missing}")
    return {"pi": pi.group("version"), "adapter": adapter.group("version")}


def _older(installed: str | None, pinned: str) -> bool:
    """Whether ``installed`` is behind ``pinned`` (absent counts as behind).

    npm versions that PEP 440 cannot parse fall back to plain inequality.
    """
    if installed is None:
        return True
    try:
        return Version(installed) < Version(pinned)
    except InvalidVersion:
        return installed != pinned


def pi_update_available(versions: dict[str, str | None] | None, pins: dict[str, str]) -> bool:
    """True when Pi or its adapter is installed but behind the installer's pin."""
    if versions is None:
        return False
    return _older(versions.get("pi"), pins["pi"]) or _older(
        versions.get("adapter"), pins["adapter"]
    )


def _check(check_id: str, ok: bool | None, detail: str) -> dict[str, Any]:
    return {"id": check_id, "ok": ok, "detail": detail}


def _adapter_gate(versions: dict[str, str | None] | None) -> dict[str, Any]:
    """Whether the installed adapter and Pi are new enough to read ``mcp.json``."""
    needs = f"needs pi-mcp-adapter >= {_ADAPTER_MIN} on pi >= {_PI_MIN_FOR_ADAPTER}"
    pi = versions.get("pi") if versions else None
    adapter = versions.get("adapter") if versions else None
    if pi is None or adapter is None:
        return _check(
            "adapter_gate", False, f"pi and pi-mcp-adapter are not both installed ({needs})"
        )
    try:
        ok = Version(adapter) >= _ADAPTER_MIN and Version(pi) >= _PI_MIN_FOR_ADAPTER
    except InvalidVersion:
        return _check("adapter_gate", False, f"cannot compare pi {pi} / pi-mcp-adapter {adapter}")
    return _check("adapter_gate", ok, f"pi {pi}, pi-mcp-adapter {adapter} ({needs})")


def _not_readable(check_id: str, label: str) -> dict[str, Any]:
    """``ok: None``: a path under the profiled home is not reachable by the daemon."""
    return _check(check_id, None, f"{label} not readable from the daemon")


def _mcp_entry_check(path: Path) -> dict[str, Any]:
    """Whether ``mcp.json`` has the ``hal0-memory`` server entry."""
    label = "hal0-memory MCP entry"
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, NotADirectoryError, ValueError):
        config = None
    except OSError:
        return _not_readable("mcp_entry", label)
    servers = config.get("mcpServers") if isinstance(config, dict) else None
    present = isinstance(servers, dict) and "hal0-memory" in servers
    return _check("mcp_entry", present, f"{label} {'present' if present else 'missing'}")


def _file_check(check_id: str, path: Path, label: str) -> dict[str, Any]:
    """Whether ``path`` is a file; ``ok: None`` when a parent is untraversable.

    ``stat`` is called directly rather than ``Path.is_file``: from Python 3.14
    ``is_file`` answers ``False`` on any ``OSError``, so an untraversable parent
    would read as "missing". ``stat`` raises ``PermissionError`` there on every
    interpreter, which is the "unknown" answer this check must give.
    """
    try:
        present = stat.S_ISREG(os.stat(path).st_mode)
    except (FileNotFoundError, NotADirectoryError):
        present = False
    except OSError:
        return _not_readable(check_id, label)
    return _check(check_id, present, f"{label} {'present' if present else 'missing'}")


def pi_profile_checks(
    *,
    marker: dict[str, Any] | None,
    versions: dict[str, str | None] | None,
    pins: dict[str, str],
    home_readable: bool,
    home: Path | None,
) -> list[dict[str, Any]]:
    """The Pi card's profile checks, in display order.

    ``binary`` and ``adapter_gate`` come from the probed ``versions``. The
    rest read the profiled ``home``; when the daemon cannot read it (``home``
    is ``None``, ``home_readable`` is false, or a path under it is not
    traversable) they answer ``ok: None`` ("unknown") rather than a false
    negative. The caller has already resolved ``home`` and ``home_readable``
    from the profile ``marker``, so the marker itself is not read here.
    """
    if versions is None:
        binary = _check("binary", False, "pi is not on PATH as an npm install")
    else:
        binary = _check("binary", True, f"pi {versions['pi']} (pinned {pins['pi']})")

    if home is None or not home_readable:
        profile = [
            _check(check_id, None, "home not readable from the daemon")
            for check_id in (
                "provider_ext",
                "hindsight_ext",
                "theme",
                "mcp_entry",
                "hindsight_config",
            )
        ]
    else:
        agent = home / ".pi" / "agent"
        profile = [
            _file_check(
                "provider_ext",
                agent / "extensions" / "hal0-provider" / "index.ts",
                "hal0-provider extension",
            ),
            _file_check(
                "hindsight_ext",
                agent / "extensions" / "hindsight" / "index.ts",
                "hindsight extension",
            ),
            _file_check("theme", agent / "themes" / "hal0.json", "hal0 theme"),
            _mcp_entry_check(agent / "mcp.json"),
            _file_check(
                "hindsight_config",
                home / ".hindsight" / "coding-agent.json",
                "Hindsight client config",
            ),
        ]
    return [binary, *profile, _adapter_gate(versions)]
