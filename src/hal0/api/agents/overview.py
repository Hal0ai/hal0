"""``GET /api/agents/{name}/overview``: everything an agent card renders, in one payload.

The route owns the I/O: it probes systemd, the Hermes API server, the release
tree, ``provision.json``, the doctor checks, the approvals state, the memory
counters and the Pi npm tree, then hands the results to the pure builders in
:mod:`hal0.agents.overview`. Each external call is a module attribute here so
tests replace it without touching systemd, the network or the real Hermes tree.

No Hermes module is imported at module level. ``hal0.api`` builds the app, and so
mounts this router, when it is imported, and the core import graph must stay
Hermes-free (GP15, ``tests/golden_paths/test_gp15_no_hermes.py``). The seams that
reach Hermes are thin functions below that import their module when called; they
stay module attributes, which is what the tests replace.

Nothing in this route may raise for a normal box state. Every gather and every
builder call runs behind a guard that returns a neutral value ("unknown",
empty, ``None``) and logs a warning, so a missing systemd, a stopped gateway,
an unreadable file or a release record of an unexpected shape shows up as a
degraded card instead of a 500.

The slow part (probes, doctor checks, file reads) is built once per agent per
:data:`_CACHE_TTL_S`, counted from when the build finished. Requests that miss
the cache while a build is running wait for that build instead of starting
another; ``?refresh=1`` always starts a new one, and a build never overwrites a
cache entry that a later-started build stored. The Hermes job that was running
when a build started is part of its cache key: a job starting or ending is a
miss, so the state a converge or repair leaves behind is never served from
before it. Other routes call :func:`invalidate` after they change what the
overview reports. The cheap live fields (unit state, pending approvals, the
running job, ``status`` and ``phase``) are recomputed on every request.
"""

from __future__ import annotations

import asyncio
import json
import os
import pwd
import shutil
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import structlog
from fastapi import APIRouter, Query, Request

from hal0.agents import overview as builders
from hal0.agents.manager import AgentManager, AgentRecord, agent_kind, installer_script_path
from hal0.agents.pi_coder import PiDriver
from hal0.agents.pi_coder.driver import _HINDSIGHT_API_URL
from hal0.api.agents.memory_stats import get_agent_memory_stats
from hal0.api.agents.restart import unit_is_active
from hal0.api.routes import agents as agents_routes
from hal0.api.routes.approvals import _queue
from hal0.api.routes.updater import _update_jobs
from hal0.components.status import component_status_snapshot
from hal0.services.systemd import unit_state

if TYPE_CHECKING:
    from hal0.agents.hermes_release import Layout, Pin, Release

log = structlog.get_logger(__name__)
router = APIRouter()

#: How long a built overview is served from memory. The dashboard polls every
#: 10 s; the doctor interpreter check spawns the Hermes venv python, so a build
#: is not free.
_CACHE_TTL_S: Final[float] = 30.0


@dataclass(frozen=True)
class _Entry:
    """One cached build."""

    started: float  # when the build started; orders builds, so a late one cannot win
    completed: float  # when it finished; the TTL runs from here
    job: str | None  # id of the Hermes job running when it started
    body: dict[str, Any]


@dataclass(frozen=True)
class _Flight:
    """A build in progress."""

    task: asyncio.Task[dict[str, Any]]
    refresh: bool  # a refresh started it
    job: str | None


@dataclass(frozen=True)
class _Gateway:
    state: str | None  # active state, transition states folded in
    enabled: bool | None  # whether the unit file says it is meant to run
    settling: bool  # starting, reloading or in maintenance: its API may not answer yet


#: Only installed agents are cached: install state is re-read on every request,
#: so an install or an uninstall shows at once.
_cache: dict[str, _Entry] = {}

#: Builds in progress. Created and awaited only inside the running event loop.
_inflight: dict[str, _Flight] = {}

#: ``name -> time of its last invalidation``. A build that started earlier does
#: not store its result.
_invalidated: dict[str, float] = {}

_GATEWAY_UNIT: Final[str] = "hermes-gateway.service"

#: systemd transition states, folded into the state they are heading for or
#: coming from, so a normal gateway restart is not reported as the gateway
#: being down for the length of a cache window.
_GATEWAY_STATE_ALIASES: Final[dict[str, str]] = {
    "activating": "active",
    "reloading": "active",
    "refreshing": "active",
    "maintenance": "active",
    "deactivating": "inactive",
}

#: The aliased states that are on the way up. The gateway's API may not answer
#: yet, which is not a fault.
_GATEWAY_SETTLING_STATES: Final[frozenset[str]] = frozenset(
    {"activating", "reloading", "refreshing", "maintenance"}
)

#: ``get_agent_memory_stats`` can page the Hindsight bank with long read
#: timeouts; the overview waits this long for it, then reports memory as
#: unavailable.
_MEMORY_TIMEOUT_S: Final[float] = 2.0

#: One probe's budget. The probes run concurrently, so a dead gateway costs one
#: second in total, not one per profile.
_PROBE_TIMEOUT_S: Final[float] = 1.0

#: The hal0 memory server (Hindsight) a Pi profile points at.
_HINDSIGHT_HEALTH_URL: Final[str] = f"{_HINDSIGHT_API_URL}/health"

#: ``updater`` registry phases that are a Hermes build, and the states that are
#: not terminal.
_HERMES_JOB_PHASES: Final[frozenset[str]] = frozenset({"converge:hermes", "repair:hermes"})
_ACTIVE_JOB_STATES: Final[frozenset[str]] = frozenset({"queued", "running"})

#: Pins reported to the profile checks when the installer script cannot be read.
_UNKNOWN_PINS: Final[dict[str, str]] = {"pi": "unknown", "adapter": "unknown"}

_NO_APPROVALS: Final[dict[str, Any]] = {"mode": None, "effective": None, "in_sync": None}
_NO_MEMORY: Final[dict[str, Any]] = {"available": False, "writes": None, "last_write": None}


# ── seams ───────────────────────────────────────────────────────────────────


def _now() -> float:
    """The cache clock; a module function so tests move time without touching the loop's."""
    return time.monotonic()


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def invalidate(name: str) -> None:
    """Drop ``name``'s cached overview so the next request rebuilds it.

    For routes that change what the overview reports (a restart, a repair, an
    approvals change). Call it from the event loop, as an async route does. A
    build already running when this is called finishes for its own waiters but
    stores nothing, and new requests start a fresh one.
    """
    _cache.pop(name, None)
    _inflight.pop(name, None)
    _invalidated[name] = _now()


def provision_path() -> Path:
    """Where the last Hermes provision run is recorded."""
    from hal0.agents.hermes_provision import _STATE_FILE_NAME

    return AgentManager()._state_dir("hermes") / _STATE_FILE_NAME


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
    from hal0.agents.hermes_release import _link_release_id, read_release

    rid = _link_release_id(layout.previous)
    return read_release(layout.releases / rid) if rid else None


# ── Hermes seams ────────────────────────────────────────────────────────────
#
# Each imports its Hermes module on call (see the module docstring).


def gateway_alive(home: Path, *, probe: Callable[[str], int] | None = None) -> bool:
    from hal0.agents import hermes_api

    return hermes_api.gateway_alive(home, probe=probe)


def profile_dirs(home: Path) -> list[str]:
    from hal0.agents import hermes_api

    return hermes_api.profile_dirs(home)


def profile_health(home: Path, profile: str, *, probe: Callable[[str], int] | None = None) -> int:
    from hal0.agents import hermes_api

    return hermes_api.profile_health(home, profile, probe=probe)


def probe_status(url: str, *, timeout: float = 3.0) -> int:
    from hal0.agents import hermes_api

    return hermes_api.probe_status(url, timeout=timeout)


def live_release(layout: Layout) -> Release | None:
    from hal0.agents import hermes_release

    return hermes_release.live_release(layout)


def read_pin() -> Pin:
    from hal0.agents import hermes_release

    return hermes_release.read_pin()


def command_status() -> dict[str, Any]:
    from hal0.agents import hermes_command_approvals

    return hermes_command_approvals.status()


def check_hermes_sqlite() -> dict[str, str]:
    from hal0.agents import hermes_doctor

    return hermes_doctor.check_hermes_sqlite()


def check_hermes_plugins() -> dict[str, str]:
    from hal0.agents import hermes_doctor

    return hermes_doctor.check_hermes_plugins()


def check_hermes_ownership(
    *, owner_of: Callable[[Path], str | None], exists: Callable[[Path], bool]
) -> list[dict[str, str]]:
    from hal0.agents import hermes_doctor

    return hermes_doctor.check_hermes_ownership(owner_of=owner_of, exists=exists)


def check_hermes_gateway_unit() -> dict[str, str]:
    from hal0.agents import hermes_doctor

    return hermes_doctor.check_hermes_gateway_unit()


def configured_platform_names(
    home: Path, secrets_env: Path | None, *, seam: str | None = None
) -> list[str]:
    from hal0.agents import hermes_platforms

    return hermes_platforms.configured_platform_names(home, secrets_env, seam=seam)


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


def _safe[T](label: str, default: T, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """``fn(*args, **kwargs)``, or ``default`` (and a warning) when a builder rejects its input."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        log.warning("agents.overview.build_failed", build=label, error=str(exc))
        return default


async def _check[T](label: str, fn: Callable[[], T]) -> tuple[T | None, str | None]:
    """A doctor check in a thread: ``(result, None)``, or ``(None, error text)``."""
    try:
        return await asyncio.to_thread(fn), None
    except Exception as exc:
        log.warning("agents.overview.check_failed", check=label, error=str(exc))
        return None, f"{label} check failed: {exc}"


# ── Hermes gathers ──────────────────────────────────────────────────────────


async def _gateway_state() -> _Gateway:
    """The gateway unit's state, whether it is meant to run, and whether it is settling.

    ``"absent"`` when systemd has no such unit: ``systemctl show`` exits 0 for an
    unknown unit and reports ``inactive`` with an empty ``UnitFileState``, which
    :func:`unit_state` turns into ``unknown``. A real unit that is merely stopped
    has a unit-file state, and that state also says whether the operator turned
    the gateway off (``disabled``, ``masked``). The transition states fold into
    their neighbours: ``activating``, ``reloading``, ``refreshing`` and
    ``maintenance`` read as ``active`` (and mark the gateway as settling) and
    ``deactivating`` as ``inactive``, so a gateway restart is not reported as down.
    """
    state = await unit_state(_GATEWAY_UNIT)
    active = state.get("active_state")
    file_state = state.get("unit_file_state")
    if active == "inactive" and file_state in (None, "unknown"):
        return _Gateway("absent", None, False)
    return _Gateway(
        _GATEWAY_STATE_ALIASES.get(active, active) if active else None,
        builders.unit_file_enabled(file_state),
        active in _GATEWAY_SETTLING_STATES,
    )


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


def _endpoint(home: Path) -> str:
    """``host:port`` of the API server, from config. No network."""
    from hal0.agents.hermes_api import api_server_endpoint, authority

    host, port = api_server_endpoint(home)
    return authority(host, port)


async def _api_health(home: Path) -> tuple[bool, list[str]]:
    """Whether the gateway answers, and the profiles it serves.

    The gateway's ``/health`` and each profile's ``/p/<name>/health`` are
    probed at once, one thread per probe. Profiles are only reported while the
    gateway itself is healthy; ``default`` is always among them.
    """

    from hal0.agents.hermes_api import DEFAULT_PROFILE

    def probe(url: str) -> int:
        return probe_status(url, timeout=_PROBE_TIMEOUT_S)

    no_names: list[str] = []
    names = await _in_thread("profile_dirs", no_names, profile_dirs, home)
    healthy, codes = await asyncio.gather(
        _in_thread("gateway_probe", False, gateway_alive, home, probe=probe),
        asyncio.gather(
            *(_in_thread("profile_probe", 0, profile_health, home, n, probe=probe) for n in names)
        ),
    )
    if not healthy:
        return False, []
    return True, [
        DEFAULT_PROFILE,
        *(n for n, code in zip(names, codes, strict=True) if code == 200),
    ]


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
    """The slow half of an installed Hermes' overview. Never raises.

    Everything that needs a probe, a subprocess or a file read. The live fields
    that change between builds (``status``, ``unit_active``, ``units.agent``,
    ``approvals.pending``, ``job``, ``phase``) are added per request by
    :func:`_overlay_hermes`.
    """
    from hal0.agents.hermes_api import hermes_home
    from hal0.agents.hermes_provision import (
        _HAL0_AGENTENV,
        HERMES_SECRETS_ENV,
        HERMES_VENV_DEFAULT,
    )
    from hal0.agents.hermes_release import Layout

    home = hermes_home()
    layout = Layout.for_venv(HERMES_VENV_DEFAULT)
    seam = _HAL0_AGENTENV if os.geteuid() != 0 else None

    no_releases: tuple[Release | None, Release | None] = (None, None)
    no_platforms: list[str] = []
    no_health: tuple[bool, list[str]] = (False, [])
    no_gateway = _Gateway(None, None, False)

    # Three groups, themselves run concurrently: live state, files, doctor checks.
    state, files, checks = await asyncio.gather(
        asyncio.gather(
            _guarded("unit_gateway", _gateway_state(), no_gateway),
            _in_thread("endpoint", None, _endpoint, home),
            _guarded("api_health", _api_health(home), no_health),
            _guarded(
                "memory",
                asyncio.wait_for(get_agent_memory_stats("hermes", request), _MEMORY_TIMEOUT_S),
                None,
            ),
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
    gateway, endpoint, (healthy, profiles), memory_raw, platforms = state
    gateway_units = {"gateway": gateway.state or "unknown", "gateway_enabled": gateway.enabled}
    (live, previous), pin_ref, component, provision_raw, approvals_state = files
    sqlite_result, plugins_result, ownership_result, gateway_result = checks
    sqlite, sqlite_err = sqlite_result
    plugins, plugins_err = plugins_result
    ownership, ownership_err = ownership_result
    gateway_check, gateway_check_err = gateway_result

    unknown_provision = {**builders.provision_summary(None), "live_built_at": None, "stale": False}
    unknown_version = {
        "live": None,
        "previous": None,
        "pin": {"ref": pin_ref, "describe": None},
        "component": component,
        "update_available": False,
    }
    provision = _safe("provision", unknown_provision, _provision_view, provision_raw, live)
    no_rows: list[dict[str, str]] = []
    doctor = _safe(
        "doctor",
        no_rows,
        builders.doctor_rows,
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
    # ``degraded_reasons`` also reads ``source`` and ``overrides_path`` from the
    # full status; the response carries only the summary keys.
    approvals_full = approvals_state or _NO_APPROVALS
    no_reasons: list[dict[str, Any]] = []
    degraded = _safe(
        "degraded",
        no_reasons,
        builders.degraded_reasons,
        units=gateway_units,
        gateway_settling=gateway.settling,
        api_healthy=healthy,
        provision=provision,
        doctor=doctor,
        approvals=approvals_full,
    )
    memory = (
        {key: memory_raw.get(key) for key in ("available", "writes", "last_write")}
        if isinstance(memory_raw, dict)
        else dict(_NO_MEMORY)
    )

    return {
        "name": "hermes",
        "kind": agent_kind("hermes"),
        "version": _safe(
            "version", unknown_version, builders.hermes_version, live, previous, pin_ref, component
        ),
        "units": gateway_units,
        "api_server": {"endpoint": endpoint, "healthy": healthy, "profiles": profiles},
        "platforms": platforms,
        "provision": provision,
        "doctor": doctor,
        "approvals": {key: approvals_full.get(key) for key in ("mode", "effective", "in_sync")},
        "memory": memory,
        "links": {
            "desktop": os.environ.get("HAL0_HERMES_PUBLIC_URL", "").strip().rstrip("/") or None
        },
        "degraded": degraded,
    }


async def _overlay_hermes(
    base: dict[str, Any], record: AgentRecord, request: Request, job: dict[str, str] | None
) -> dict[str, Any]:
    """``base`` (possibly cached) plus the fields that must be current on every request."""
    agent_active = await _guarded("unit_agent", unit_is_active("hermes"), None)
    return {
        **base,
        "installed": True,
        "status": record.status,
        "unit_active": agent_active,
        "units": builders.unit_states(
            agent_active,
            base["units"]["gateway"],
            gateway_enabled=base["units"]["gateway_enabled"],
        ),
        "approvals": {**base["approvals"], "pending": _pending_approvals(request)},
        "job": job,
        "phase": _safe(
            "phase",
            "unknown",
            builders.hermes_phase,
            installed=True,
            status=record.status,
            unit_active=agent_active,
            degraded=base["degraded"],
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
    """The slow half of an installed Pi's overview. Never raises.

    ``installed``, ``status`` and ``phase`` come per request from
    :func:`_overlay_pi`.
    """
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
        "version": {
            "installed": versions,
            "pin": pins,
            "update_available": pins is not None
            and _safe("pi_update", False, builders.pi_update_available, versions, pins),
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
    }


def _overlay_pi(base: dict[str, Any], record: AgentRecord) -> dict[str, Any]:
    return {
        **base,
        "installed": True,
        "status": record.status,
        "phase": _safe("phase", "unknown", builders.pi_phase, installed=True, status=record.status),
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


# ── cache ───────────────────────────────────────────────────────────────────


async def _build(name: str, request: Request, started: float, job: str | None) -> dict[str, Any]:
    """Build ``name``'s slow half and store it.

    Nothing is stored when ``name`` was invalidated after this build started, or
    when a later-started build already stored its result.
    """
    generated_at = _utc_iso()
    body = {
        **await (_gather_hermes(request) if name == "hermes" else _gather_pi()),
        "generated_at": generated_at,
    }
    held = _cache.get(name)
    if started >= _invalidated.get(name, float("-inf")) and (
        held is None or held.started <= started
    ):
        _cache[name] = _Entry(started, _now(), job, body)
    return body


def _forget(name: str, task: asyncio.Task[dict[str, Any]]) -> None:
    """Drop ``name``'s in-flight entry once its task finished, unless a newer one replaced it."""
    flight = _inflight.get(name)
    if flight is not None and flight.task is task:
        del _inflight[name]


async def _slow_half(name: str, request: Request, refresh: bool, job: str | None) -> dict[str, Any]:
    """The cached slow half of ``name``'s overview, built at most once per miss.

    A cached build answers while it is younger than the TTL (counted from when it
    finished) and was started under the same Hermes ``job``. A request that
    misses joins the build already running for ``name`` when that build was
    started under the same job and, for a refresh, by a refresh: any other
    running build began before the refresh was asked for, or before the job
    changed, and may predate the change the caller wants to see. Waiters are
    shielded, so one client disconnecting does not cancel the build the others
    are waiting on.
    """
    started = _now()
    entry = _cache.get(name)
    if (
        not refresh
        and entry is not None
        and entry.job == job
        and started - entry.completed < _CACHE_TTL_S
    ):
        return entry.body

    flight = _inflight.get(name)
    if flight is not None and (
        flight.task.done() or flight.task.get_loop() is not asyncio.get_running_loop()
    ):
        flight = None  # finished, or left behind by a loop that has closed
    if flight is None or flight.job != job or (refresh and not flight.refresh):
        task = asyncio.create_task(_build(name, request, started, job))
        task.add_done_callback(lambda t: _forget(name, t))
        flight = _Flight(task, refresh, job)
        _inflight[name] = flight
    return await asyncio.shield(flight.task)


# ── route ───────────────────────────────────────────────────────────────────


@router.get("/{name}/overview")
async def agent_overview(name: str, request: Request, refresh: int = Query(0)) -> dict[str, Any]:
    """The state a Hermes or Pi card renders, built from live probes.

    404 ``agent.unknown`` for a name that is not a bundled agent. A bundled
    agent that is not installed answers with just its pin and the install
    command. For an installed agent the probes are cached for 30 s per agent
    and built once per miss; ``?refresh=1`` rebuilds them and refills the
    cache, and a Hermes build or repair starting or ending is a miss too. Unit
    state, pending approvals, the running job, ``status`` and ``phase`` are
    current on every request.
    """
    name = agents_routes._bundled_name(name)
    record = _installed_record(name)
    if record is None:
        invalidate(name)
        return await _not_installed(name)

    job = _active_job(request) if name == "hermes" else None
    base = await _slow_half(name, request, bool(refresh), job["id"] if job else None)
    if name == "hermes":
        return await _overlay_hermes(base, record, request, job)
    return _overlay_pi(base, record)
