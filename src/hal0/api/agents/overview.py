"""``GET /api/agents/{name}/overview``: everything an agent card renders, in one payload.

The route owns the I/O: it probes systemd, the Hermes API server, the release
tree, ``provision.json``, the doctor checks, the approvals state, the memory
counters and the Pi npm tree, then hands the results to the pure builders in
:mod:`hal0.agents.overview`. Each external call is a module attribute here so
tests replace it without touching systemd, the network or the real Hermes tree.

Nothing in this route may raise for a normal box state. Every gather runs
behind a guard that returns a neutral value ("unknown", empty, ``None``) and
logs a warning, so a missing systemd, a stopped gateway or an unreadable file
shows up as a degraded card instead of a 500. The synchronous probes run
concurrently in threads, and the result is cached per agent for
:data:`_CACHE_TTL_S` (``?refresh=1`` bypasses and refills the cache).
"""

from __future__ import annotations

import asyncio
import json
import os
import pwd
import shutil
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Final

import structlog
from fastapi import APIRouter, Query, Request

from hal0.agents import overview as builders
from hal0.agents.hermes_api import (
    api_server_endpoint,
    authority,
    gateway_alive,
    hermes_home,
    probe_status,
    served_profiles,
)
from hal0.agents.hermes_command_approvals import status as command_status
from hal0.agents.hermes_doctor import (
    check_hermes_gateway_unit,
    check_hermes_ownership,
    check_hermes_plugins,
    check_hermes_sqlite,
)
from hal0.agents.hermes_platforms import configured_platform_names
from hal0.agents.hermes_provision import _HAL0_AGENTENV, HERMES_SECRETS_ENV, HERMES_VENV_DEFAULT
from hal0.agents.hermes_release import (
    Layout,
    Release,
    _link_release_id,
    live_release,
    read_pin,
    read_release,
)
from hal0.agents.manager import AgentRecord, agent_kind, installer_script_path
from hal0.agents.pi_coder import PiDriver
from hal0.api.agents.memory_stats import get_agent_memory_stats
from hal0.api.agents.restart import unit_is_active
from hal0.api.routes import agents as agents_routes
from hal0.api.routes.approvals import _queue
from hal0.api.routes.updater import _update_jobs
from hal0.components.status import component_status_snapshot
from hal0.config import paths
from hal0.services.systemd import unit_state

log = structlog.get_logger(__name__)
router = APIRouter()

#: How long a built overview is served from memory. The dashboard polls every
#: 10 s; the doctor interpreter check spawns the Hermes venv python, so a build
#: is not free.
_CACHE_TTL_S: Final[float] = 30.0

#: ``name -> (monotonic time it was built, body)``. Only installed agents are
#: cached: install state is re-read on every request so an install or an
#: uninstall shows at once.
_cache: dict[str, tuple[float, dict[str, Any]]] = {}

_GATEWAY_UNIT: Final[str] = "hermes-gateway.service"

#: One probe's budget. The route runs these concurrently, so a dead gateway
#: costs one second, not one per profile.
_PROBE_TIMEOUT_S: Final[float] = 1.0

#: The hal0 memory server (Hindsight) a Pi profile points at.
_HINDSIGHT_HEALTH_URL: Final[str] = "http://127.0.0.1:9177/health"

#: ``updater`` registry phases that are a Hermes build, and the states that are
#: not terminal.
_HERMES_JOB_PHASES: Final[frozenset[str]] = frozenset({"converge:hermes", "repair:hermes"})
_ACTIVE_JOB_STATES: Final[frozenset[str]] = frozenset({"queued", "running"})

#: Pins reported to the profile checks when the installer script cannot be read.
_UNKNOWN_PINS: Final[dict[str, str]] = {"pi": "unknown", "adapter": "unknown"}

_NO_APPROVALS: Final[dict[str, Any]] = {
    "mode": None,
    "effective": None,
    "in_sync": None,
    "pending": 0,
}
_NO_MEMORY: Final[dict[str, Any]] = {"available": False, "writes": None, "last_write": None}


# ── seams ───────────────────────────────────────────────────────────────────


def provision_path() -> Path:
    """Where the last Hermes provision run is recorded."""
    return paths.var_lib() / "state" / "agents" / "hermes" / "provision.json"


def pi_binary() -> Path | None:
    """The ``pi`` executable on this process's PATH, if any."""
    found = shutil.which("pi")
    return Path(found) if found else None


def pi_script() -> Path:
    """The installer script that pins the Pi and adapter versions."""
    return installer_script_path("pi")


def pi_marker_path() -> Path:
    """The profile marker the Pi driver stamps into its data dir."""
    return PiDriver()._profile_marker_path()


def _previous_release(layout: Layout) -> Release | None:
    """The release ``previous`` names, when it is complete."""
    rid = _link_release_id(layout.previous)
    return read_release(layout.releases / rid) if rid else None


def _owner_of(path: Path) -> str | None:
    try:
        return pwd.getpwuid(path.stat().st_uid).pw_name
    except (OSError, KeyError):
        return None


def _exists_quiet(path: Path) -> bool:
    """``Path.exists`` raises on an untraversable parent; an unauditable path is absent."""
    try:
        return path.exists()
    except OSError:
        return False


# ── guards ──────────────────────────────────────────────────────────────────


async def _guarded[T](label: str, awaitable: Awaitable[T], default: T) -> T:
    """``await awaitable``, or ``default`` (and a warning) when it raises."""
    try:
        return await awaitable
    except Exception as exc:
        log.warning("agents.overview.gather_failed", gather=label, error=str(exc))
        return default


def _in_thread[T](
    label: str, default: T, fn: Callable[..., T], *args: Any, **kwargs: Any
) -> Awaitable[T]:
    """``fn`` run in a worker thread behind :func:`_guarded`."""
    return _guarded(label, asyncio.to_thread(fn, *args, **kwargs), default)


async def _check[T](label: str, fn: Callable[[], T]) -> tuple[T | None, str | None]:
    """A doctor check in a thread: ``(result, None)``, or ``(None, error text)``."""
    try:
        return await asyncio.to_thread(fn), None
    except Exception as exc:
        log.warning("agents.overview.check_failed", check=label, error=str(exc))
        return None, f"{label} check failed: {exc}"


# ── Hermes gathers ──────────────────────────────────────────────────────────


async def _gateway_state() -> str | None:
    """The gateway unit's active state; ``"absent"`` when systemd has no such unit.

    ``systemctl show`` exits 0 for an unknown unit and reports ``inactive`` with
    an empty ``UnitFileState``, which :func:`unit_state` turns into ``unknown``.
    A real unit that is merely stopped has a unit-file state.
    """
    state = await unit_state(_GATEWAY_UNIT)
    active = state.get("active_state")
    if active == "inactive" and state.get("unit_file_state") in (None, "unknown"):
        return "absent"
    return active


def _read_releases(layout: Layout) -> tuple[Release | None, Release | None]:
    return live_release(layout), _previous_release(layout)


def _pin_ref() -> str:
    """The pinned Hermes ref, or ``"unknown"`` (the component table's word for it)."""
    try:
        return read_pin().ref
    except Exception as exc:
        log.warning("agents.overview.gather_failed", gather="pin", error=str(exc))
        return "unknown"


def _hermes_component() -> dict[str, Any] | None:
    """The Hermes row of the component table: installed, pinned and derived status."""
    for row in component_status_snapshot()["components"]:
        if row.get("id") == "hermes":
            return {key: row.get(key) for key in ("installed", "pinned", "status")}
    return None


def _read_provision() -> dict[str, Any] | None:
    try:
        data = json.loads(provision_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    return data if isinstance(data, dict) else None


def _api_server(home: Path) -> dict[str, Any]:
    """Endpoint, health and served profiles of the Hermes API server."""
    host, port = api_server_endpoint(home)

    def probe(url: str) -> int:
        return probe_status(url, timeout=_PROBE_TIMEOUT_S)

    healthy = gateway_alive(home, probe=probe)
    return {
        "endpoint": authority(host, port),
        "healthy": healthy,
        "profiles": served_profiles(home, probe=probe) if healthy else [],
    }


def _pending_approvals(request: Request) -> int:
    try:
        return len(_queue(request).list_pending())
    except Exception as exc:
        log.warning("agents.overview.gather_failed", gather="approval_queue", error=str(exc))
        return 0


def _active_job(request: Request) -> dict[str, str] | None:
    """The newest queued or running Hermes build (converge or repair), if any."""
    try:
        jobs = [
            job
            for job in _update_jobs(request).values()
            if job.get("phase") in _HERMES_JOB_PHASES and job.get("state") in _ACTIVE_JOB_STATES
        ]
    except Exception as exc:
        log.warning("agents.overview.gather_failed", gather="job", error=str(exc))
        return None
    if not jobs:
        return None
    newest = max(jobs, key=lambda job: job.get("created_at") or 0)
    return {
        "id": newest.get("id"),
        "kind": str(newest["phase"]).partition(":")[0],
        "state": newest["state"],
    }


def _provision_view(provision_raw: dict[str, Any] | None, live: Release | None) -> dict[str, Any]:
    """The provision summary, marked ``stale`` when the live release postdates the report.

    ``provision.json`` is written by the bootstrap pipeline only; the converge
    that ``hal0 update`` runs rebuilds Hermes and leaves it alone, so a failed
    phase can outlive the build that failed. A time that does not parse never
    marks the report stale.
    """
    provision = builders.provision_summary(provision_raw)
    built_at = live.data.get("built_at") if live is not None else None
    built = builders.parse_timestamp(built_at)
    reported = builders.parse_timestamp(provision["reported_at"])
    provision["live_built_at"] = built_at
    provision["stale"] = built is not None and reported is not None and built > reported
    return provision


def _absent(detail: str | None) -> dict[str, str]:
    return {"status": "absent", "detail": detail or "check unavailable"}


def _installed_record(name: str) -> AgentRecord | None:
    return next((rec for rec in agents_routes._manager().list() if rec.name == name), None)


async def _gather_hermes(request: Request) -> dict[str, Any]:
    """The overview of an installed Hermes. Never raises."""
    record = _installed_record("hermes")
    status = record.status if record is not None else "installed"
    home = hermes_home()
    layout = Layout.for_venv(HERMES_VENV_DEFAULT)
    seam = _HAL0_AGENTENV if os.geteuid() != 0 else None

    no_releases: tuple[Release | None, Release | None] = (None, None)
    no_platforms: list[str] = []
    no_api: dict[str, Any] = {"endpoint": None, "healthy": False, "profiles": []}

    # Three groups, themselves run concurrently: live state, files, doctor checks.
    state, files, checks = await asyncio.gather(
        asyncio.gather(
            _guarded("unit_agent", unit_is_active("hermes"), None),
            _guarded("unit_gateway", _gateway_state(), None),
            _in_thread("api_server", no_api, _api_server, home),
            _guarded("memory", get_agent_memory_stats("hermes", request), None),
            _in_thread(
                "platforms",
                no_platforms,
                configured_platform_names,
                home,
                HERMES_SECRETS_ENV,
                seam=seam,
            ),
        ),
        asyncio.gather(
            _in_thread("releases", no_releases, _read_releases, layout),
            _in_thread("pin", "unknown", _pin_ref),
            _in_thread("component", None, _hermes_component),
            _in_thread("provision", None, _read_provision),
            _in_thread("approvals", None, command_status),
        ),
        asyncio.gather(
            _check("interpreter", check_hermes_sqlite),
            _check("plugins", check_hermes_plugins),
            _check(
                "ownership",
                lambda: check_hermes_ownership(owner_of=_owner_of, exists=_exists_quiet),
            ),
            _check("gateway_unit", check_hermes_gateway_unit),
        ),
    )
    agent_active, gateway, api, memory_raw, platforms = state
    (live, previous), pin_ref, component, provision_raw, approvals_state = files
    sqlite_result, plugins_result, ownership_result, gateway_result = checks
    sqlite, sqlite_err = sqlite_result
    plugins, plugins_err = plugins_result
    ownership, ownership_err = ownership_result
    gateway_check, gateway_check_err = gateway_result

    units = builders.unit_states(agent_active, gateway)
    provision = _provision_view(provision_raw, live)
    doctor = builders.doctor_rows(
        sqlite or _absent(sqlite_err),
        plugins or _absent(plugins_err),
        ownership or [],
        gateway_check or _absent(gateway_check_err),
    )
    if ownership_err is not None:
        doctor = [
            {"id": "ownership", **_absent(ownership_err)} if row["id"] == "ownership" else row
            for row in doctor
        ]

    pending = _pending_approvals(request)
    # ``degraded_reasons`` also reads ``source`` and ``overrides_path`` from the
    # full status; the response carries only the four summary keys.
    approvals_full = {**(approvals_state or _NO_APPROVALS), "pending": pending}
    approvals = {
        key: approvals_full.get(key) for key in ("mode", "effective", "in_sync", "pending")
    }

    degraded = builders.degraded_reasons(
        units=units,
        api_healthy=bool(api["healthy"]),
        provision=provision,
        doctor=doctor,
        approvals=approvals_full,
    )
    job = _active_job(request)
    memory = (
        {key: memory_raw.get(key) for key in ("available", "writes", "last_write")}
        if isinstance(memory_raw, dict)
        else dict(_NO_MEMORY)
    )

    return {
        "name": "hermes",
        "kind": agent_kind("hermes"),
        "installed": True,
        "status": status,
        "unit_active": agent_active,
        "version": builders.hermes_version(live, previous, pin_ref, component),
        "units": units,
        "api_server": api,
        "platforms": platforms,
        "provision": provision,
        "doctor": doctor,
        "approvals": approvals,
        "memory": memory,
        "links": {
            "desktop": os.environ.get("HAL0_HERMES_PUBLIC_URL", "").strip().rstrip("/") or None
        },
        "job": job,
        "degraded": degraded,
        "phase": builders.hermes_phase(
            installed=True,
            status=status,
            unit_active=agent_active,
            degraded=degraded,
            job=job,
        ),
    }


# ── Pi gathers ──────────────────────────────────────────────────────────────


def _read_marker() -> dict[str, Any] | None:
    """The Pi profile marker, or ``None`` when absent or not a JSON object."""
    try:
        data = json.loads(pi_marker_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _pi_pins() -> dict[str, str] | None:
    """The versions ``installer/agents/pi.sh`` pins, or ``None`` when unreadable."""
    try:
        return builders.pi_pins(pi_script())
    except (OSError, ValueError) as exc:
        log.warning("agents.overview.gather_failed", gather="pi_pins", error=str(exc))
        return None


def _pi_profile(
    marker: dict[str, Any] | None,
    versions: dict[str, str | None] | None,
    pins: dict[str, str] | None,
) -> dict[str, Any]:
    raw_home = marker.get("home") if marker else None
    home = Path(raw_home) if isinstance(raw_home, str) and raw_home else None
    home_readable = home is not None and os.access(home, os.R_OK | os.X_OK)
    return {
        "home": str(home) if home is not None else None,
        "profiled_at": marker.get("profiled_at") if marker else None,
        "home_readable": home_readable,
        "checks": builders.pi_profile_checks(
            marker=marker,
            versions=versions,
            pins=pins or _UNKNOWN_PINS,
            home_readable=home_readable,
            home=home,
        ),
    }


def _pi_installed_state() -> tuple[dict[str, str | None] | None, dict[str, str] | None]:
    return builders.pi_installed_versions(pi_binary()), _pi_pins()


async def _gather_pi() -> dict[str, Any]:
    """The overview of an installed Pi. Never raises."""
    record = _installed_record("pi")
    status = record.status if record is not None else "installed"
    no_state: tuple[dict[str, str | None] | None, dict[str, str] | None] = (None, None)
    (versions, pins), marker, hindsight_status = await asyncio.gather(
        _in_thread("pi_versions", no_state, _pi_installed_state),
        _in_thread("pi_marker", None, _read_marker),
        _in_thread(
            "hindsight_probe", 0, probe_status, _HINDSIGHT_HEALTH_URL, timeout=_PROBE_TIMEOUT_S
        ),
    )
    no_profile: dict[str, Any] = {
        "home": None,
        "profiled_at": None,
        "home_readable": False,
        "checks": [],
    }
    profile = await _in_thread("pi_profile", no_profile, _pi_profile, marker, versions, pins)
    checks = {check["id"]: check["ok"] for check in profile["checks"]}
    return {
        "name": "pi",
        "kind": agent_kind("pi"),
        "installed": True,
        "status": status,
        "version": {
            "installed": versions,
            "pin": pins,
            "update_available": pins is not None and builders.pi_update_available(versions, pins),
        },
        "profile": profile,
        "memory": {
            "mcp_entry": checks.get("mcp_entry"),
            "hindsight_config": checks.get("hindsight_config"),
            "hindsight_api_reachable": hindsight_status == 200,
        },
        "commands": {
            "install": "sudo hal0 agent install pi",
            "upgrade": "sudo hal0 agent upgrade pi",
            "uninstall": "sudo hal0 agent uninstall pi",
        },
        "phase": builders.pi_phase(installed=True, status=status),
    }


# ── not installed ───────────────────────────────────────────────────────────


async def _not_installed(name: str) -> dict[str, Any]:
    """What the card shows for a bundled agent that is not installed: the pin it would get."""
    body: dict[str, Any] = {
        "name": name,
        "kind": agent_kind(name),
        "installed": False,
        "phase": "not_installed",
    }
    if name == "hermes":
        body["pin"] = {"ref": await asyncio.to_thread(_pin_ref), "describe": None}
    else:
        pins = await asyncio.to_thread(_pi_pins)
        body["version"] = {
            "installed": None,
            "pin": pins,
            "update_available": False,
        }
    body["commands"] = {"install": f"sudo hal0 agent install {name}"}
    return body


# ── route ───────────────────────────────────────────────────────────────────


@router.get("/{name}/overview")
async def agent_overview(name: str, request: Request, refresh: int = Query(0)) -> dict[str, Any]:
    """The state a Hermes or Pi card renders, built from live probes.

    404 ``agent.unknown`` for a name that is not a bundled agent. A bundled
    agent that is not installed answers with just its pin and the install
    command. An installed agent's overview is cached for 30 s per agent;
    ``?refresh=1`` rebuilds it and refills the cache.
    """
    name = agents_routes._bundled_name(name)
    if _installed_record(name) is None:
        _cache.pop(name, None)
        return await _not_installed(name)

    cached = _cache.get(name)
    if not refresh and cached is not None and time.monotonic() - cached[0] < _CACHE_TTL_S:
        return cached[1]

    body = await (_gather_hermes(request) if name == "hermes" else _gather_pi())
    _cache[name] = (time.monotonic(), body)
    return body
