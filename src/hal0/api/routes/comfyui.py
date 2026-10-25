"""ComfyUI "generation engine" status aggregator + control routes.

The dashboard models ComfyUI as ONE containerized generation engine, not a list
of per-model slots (a single run loads many cooperating models at once, and it
is mutually exclusive with the LLM stack on the single iGPU). The Image-Gen tab
renders that engine pane from ``GET /api/comfyui/status``, which folds together:

  - **systemd** state of the img slot's unit (``hal0-slot@<token>``, the podman
    container that runs ComfyUI: running / absent),
  - **systemd** state of the Hermes agent (``hal0-agent@hermes``) so the pane
    can show which mode currently owns the GPU, and
  - **ComfyUI's own HTTP API** (``/system_stats`` for GTT/RAM, ``/queue`` for the
    running + pending job counts).

Every source degrades to a safe default — the pane polls this every few seconds
and a dead container must surface as "stopped", never a 500.

The switchover *write* path (``POST /api/comfyui/switchover``) drives the
SlotManager's :class:`~hal0.slots.arbiter.GpuArbiter` (Phase D): generation →
``ensure_img(pin=...)`` (drain + unload the llm GPU group; the resident
ComfyUI container is only cold-started when it is down), inference →
``restore_llm(force=...)`` (free ComfyUI's models via POST /free — the
container and its web UI stay up — then reload the saved llm slots). It runs
in the background behind a 202; the ``switchover`` block on /status tracks the
transition. The API no longer shells out to any ComfyUI control scripts — the
podman ``hal0-slot@<token>`` unit is the sole owner of the container lifecycle
(the standalone ``comfy-*.sh`` docker scripts were retired in #984).
``POST /api/comfyui/pin`` toggles the arbiter's manual pin (holds image mode).

The image-model routes (ADR-0037) sit beside the engine ones: ``/models`` is the
catalog with each model's install state, ``/models/{id}/install`` and
``DELETE /models/{id}`` change what is on disk, ``/jobs`` tracks the installs, and
``/files`` lists every weight file under the models tree. An install is a
:class:`~hal0.registry.pull.PullJob` kept on ``app.state.image_install_jobs``.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import os
import re
import shutil
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from hal0.api import auth as auth_layer
from hal0.api import memory_source
from hal0.api._redact import redact_log_line
from hal0.api.auth import GATE_SCOPE_KEY, admin_gated, gate_path_refused_at
from hal0.api.middleware.error_codes import Conflict, Forbidden, NotFound, UnprocessableEntity
from hal0.api.routes.power import _probe_power
from hal0.capabilities.catalog import COMFYUI_NO_ENGINE_REASON, comfyui_backends
from hal0.comfyui import install as comfyui_install
from hal0.comfyui import inventory
from hal0.comfyui import paths as comfyui_paths
from hal0.comfyui import progress as render_progress
from hal0.comfyui.autoselect import auto_select_first_image_model, enabled_img_model
from hal0.comfyui.catalog import (
    BYTES_PER_GIB,
    CATALOG,
    ImageModel,
    get_model,
    install_state,
    load_template,
    missing_gib,
    models_using,
    weights_gib,
)
from hal0.comfyui.fit import assess, need_for
from hal0.comfyui.gate_reason import clean_reason
from hal0.comfyui.gate_version import (
    GATE_USER_AGENT_PREFIX,
    GATE_VERSION,
    GATE_VERSION_HEADER,
)
from hal0.comfyui.picker import recommendation, recommended_id
from hal0.hardware import gpu_view
from hal0.hardware.memory_budget import MemoryBudget
from hal0.http_client import async_client
from hal0.providers import comfyui as comfyui_provider
from hal0.providers.comfyui_workflows import effective_size
from hal0.registry.pull import PullJob, make_job
from hal0.slots.naming import slot_container_name, slot_token_for, slot_unit_name
from hal0.upstreams.huggingface import hf_token_from_env

router = APIRouter()
log = logging.getLogger(__name__)

# iGPU memory ceilings on the Strix Halo target (GB). The host addresses ~80 GB
# of the 96 GB unified pool from the GPU, with zero swap — so memory pressure is
# the real constraint, surfaced when GTT crosses half the ceiling.
_GTT_CEIL_GB = 80
_RAM_CEIL_GB = 96
_PRESSURE_GB = 50

_HERMES_UNIT = "hal0-agent@hermes.service"
# The seeded name of the image slot; its unit and container are named after the slot's
# id on a box migrated to id-keyed slots (see ``_img_artefacts``).
_IMG_SLOT = "img"

# Job states an install can still be changed from.
_ACTIVE_JOB_STATES = ("queued", "running")

# Switchover target modes. "generation" hands the iGPU to ComfyUI
# (arbiter.ensure_img); "inference" hands it back to the LLM stack
# (arbiter.restore_llm).
_MODES = ("generation", "inference")
_WORKFLOW_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# Short connect so a dead engine surfaces fast; the read budget is modest because
# /system_stats and /queue are cheap snapshots.
_DEFAULT_TIMEOUT = httpx.Timeout(connect=1.5, read=4.0, write=2.0, pool=2.0)
_POOL_LIMITS = httpx.Limits(max_connections=4, max_keepalive_connections=2)

# The memory budget polls ComfyUI from /api/stats/hardware, which the dashboard hits every
# 2.5 s: that read must give up far sooner than the pane's ``_DEFAULT_TIMEOUT`` and be
# cached, so a dead or wedged engine never makes the poll wait.
_ENGINE_TIMEOUT = httpx.Timeout(connect=0.25, read=0.5, write=0.5, pool=0.5)
_ENGINE_TTL_S = 5.0

_client: httpx.AsyncClient | None = None

# ``(read at, MB or None)`` of the last ``engine_used_mb`` read; a failed read is cached too.
_engine_cache: tuple[float, float | None] | None = None

# Clock seam for the cache above; tests drive the TTL through it.
_now = time.monotonic

# Keep strong refs to fire-and-forget tasks so they aren't GC'd mid-flight (RUF006).
_BG_TASKS: set[asyncio.Task[Any]] = set()

# In-flight switchover tracker. Module-global on purpose: there is exactly one
# iGPU, so there is exactly one switch — /status surfaces it so the pane's poll
# can render "switching…" and any error from the last attempt.
_SWITCH_IDLE: dict[str, Any] = {"active": False, "target": None, "error": None}
_switch: dict[str, Any] = dict(_SWITCH_IDLE)

# The ComfyUI GPU gate (the ``hal0_gpu_gate`` node in the img container) tells hal0 two
# things, and /status reports both: its version, on every request it makes
# (``X-Hal0-Gate-Version``), and why it refused a prompt (``POST /gate-refusal``). Like
# ``_switch`` this is module state: there is one engine, so one gate.
#
# ``_gate_seen``: the version header of the gate's latest call (``None`` for a call
# with none) and when it came (epoch seconds); ``at`` is ``None`` until a call arrives.
# ``_gate_refusal``: the latest refusal, ``{"at": epoch seconds, "reason": str}``.
_GATE_SEEN_IDLE: dict[str, Any] = {"version": None, "at": None}
_gate_seen: dict[str, Any] = dict(_GATE_SEEN_IDLE)
_gate_refusal: dict[str, Any] | None = None

# The version is a header any caller on a box without auth can set, so hal0 keeps a
# bounded piece of it (the reason is bounded by ``clean_reason``).
_GATE_VERSION_MAX = 32

# Wall clock for the timestamps above (epoch seconds); tests drive it through this seam.
_wall_clock = time.time

# Failures the status and model routes absorb (an unreadable gate state, a model whose
# catalog facts cannot be judged) are logged once per process, keyed by what failed: the
# dashboard polls ``/status`` every few seconds, and a persistent fault is one fact.
_warned: set[str] = set()


def _warn_once(key: str, event: str, **fields: Any) -> None:
    """Log ``event`` as a warning, with the exception being handled, the first time ``key`` fails."""
    if key in _warned:
        return
    _warned.add(key)
    log.warning(event, extra=fields, exc_info=True)


def _comfyui_base_url() -> str:
    """Base URL of the operational ComfyUI container's HTTP API.

    Defaults to loopback :8188 (the container publishes there on the runtime
    host); ``COMFYUI_BASE_URL`` re-points it for dev / off-box dashboards.
    """
    return os.environ.get("COMFYUI_BASE_URL", "http://127.0.0.1:8188").rstrip("/")


async def _img_artefacts(request: Request) -> tuple[str, str]:
    """``(unit, container)`` names of the img slot, as the slot naming seam spells them.

    Both are named after the slot's durable id on a box migrated to id-keyed slots and
    after its name otherwise, so they are resolved from the slot's config rather than
    assumed (#2302). With no slot manager, or none that knows the slot, the name-keyed
    names are the answer.
    """
    manager = getattr(request.app.state, "slot_manager", None)
    token = await slot_token_for(manager, _IMG_SLOT) if manager is not None else _IMG_SLOT
    return slot_unit_name(token), slot_container_name(token)


def _build_client(timeout: httpx.Timeout) -> httpx.AsyncClient:
    return async_client(timeout=timeout, limits=_POOL_LIMITS)


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = _build_client(_DEFAULT_TIMEOUT)
    return _client


async def aclose_client() -> None:
    """Close the shared client on app shutdown. Idempotent."""
    global _client
    if _client is not None:
        with contextlib.suppress(Exception):
            await _client.aclose()
        _client = None


def _reset_state() -> None:
    """Drop the shared client, switch tracker, engine-read cache and what the gate told hal0
    (including the auth layer's marker of a refused version-less gate request).

    For test isolation only.
    """
    global _client, _engine_cache, _gate_refusal
    _client = None
    _engine_cache = None
    _switch.clear()
    _switch.update(_SWITCH_IDLE)
    _gate_seen.clear()
    _gate_seen.update(_GATE_SEEN_IDLE)
    _gate_refusal = None
    _warned.clear()
    auth_layer._gate_path_refused_at = None


# ---------------------------------------------------------------------------
# What the GPU gate tells hal0: its version, and why it refused a prompt
# ---------------------------------------------------------------------------


async def _gate_call(request: Request) -> None:
    """Route dependency: remember the version the gate called with.

    A call with the gate's version header is the gate's. A call without it is an old
    gate's only when it looks like one: the gate's default HTTP client (a User-Agent of
    ``Python-urllib/…``; v1.6.5's gate made ``GET /status`` and ``POST /switchover``
    that way, and the first gates with the header's routes ``/arbiter`` and
    ``/prompt-handoff``) calling from where the gate dials, the box itself (the peer
    test the auth layer's marker uses). A browser, curl, another machine or any other
    client without the header is nobody in particular: the dashboard polls ``/status``
    and posts ``/switchover``. The User-Agent test is a heuristic (see
    :func:`hal0.api.auth.gate_path_refused_at`).
    """
    version = request.headers.get(GATE_VERSION_HEADER)
    if version is None and not (
        request.headers.get("user-agent", "").startswith(GATE_USER_AGENT_PREFIX)
        and auth_layer._gate_peer(request.scope)
    ):
        return
    _gate_seen.update(
        version=None if version is None else version[:_GATE_VERSION_MAX], at=_wall_clock()
    )


def record_gate_refusal(reason: str) -> None:
    """Remember why the gate refused a prompt, for ``/status`` and ``/arbiter``.

    The gate reports its own refusals (``POST /gate-refusal``); a refusal it cannot
    report, because hal0 requires a credential it lacks, is recorded from the render
    that ran into it (``/v1/images/generations``).
    """
    global _gate_refusal
    _gate_refusal = {"at": _wall_clock(), "reason": clean_reason(reason)}
    log.info("ComfyUI GPU gate refused a prompt: %r", _gate_refusal["reason"])


def _record_gate_rejection(request: Request, status: int, message: str) -> None:
    """Record a switch request hal0 rejected, when the gate made it (it carries the header)."""
    if GATE_VERSION_HEADER in request.headers:
        record_gate_refusal(f"hal0 refused the gate's switch request (HTTP {status}): {message}")


def _gate_must_authenticate() -> bool:
    """True iff hal0 would refuse the gate's requests without a credential.

    The auth middleware's own rule (:func:`hal0.api.auth.admin_gated`: enforcement is on,
    or the LAN rule for ADMIN routes applies to the request's peer), asked about the
    peer the gate's requests come from, not the dashboard asking for ``/status``.
    """
    peer = comfyui_provider.gate_dial_host()
    return admin_gated({"type": "http", "client": (peer, 0)})


def _gate_refusal_of(resp: httpx.Response) -> str | None:
    """The gate's reason when ``resp`` is the GPU gate's 503 for a prompt, else ``None``.

    The same shape check as the provider's (:func:`hal0.providers.comfyui.gate_refusal_reason`),
    for a ``/prompt`` hal0 posts without the provider (a quick-launched workflow).
    """
    try:
        body = resp.json()
    except ValueError:
        return None
    return comfyui_provider.gate_refusal_reason(resp.status_code, body)


def _gate_state() -> str:
    """``no_credential`` | ``stale`` | ``unknown`` | ``current``, in that order of precedence.

    ``no_credential``: hal0 requires the gate to authenticate and the gate has no way to
    (no token on disk, or it is given a host name and holds the token back from names).
    Its calls are refused, so whatever it told hal0 earlier is no longer the news.

    ``stale`` first for a gate the auth layer refused: on a box that enforces auth, an
    engine running a gate older than the version header is turned away before it reaches
    a route (:func:`hal0.api.auth.gate_path_refused_at`), so its call is never recorded
    here. A refusal newer than the gate's latest recorded call, or with none, reads as
    stale: restart the engine. The gate it is replaced by calls with the header and
    clears it.

    Otherwise by the gate's latest call: ``unknown`` when there has been none since
    hal0-api started, ``current`` when it carried this hal0's gate version, and
    ``stale`` for another version or none (an engine still running the gate it started
    with: restart it).
    """
    if _gate_must_authenticate() and not comfyui_provider.gate_sends_credential():
        return "no_credential"
    refused_at = gate_path_refused_at()
    seen_at = _gate_seen["at"]
    if refused_at is not None and (seen_at is None or refused_at > seen_at):
        return "stale"
    if seen_at is None:
        return "unknown"
    return "current" if _gate_seen["version"] == GATE_VERSION else "stale"


def _gate_block() -> dict[str, Any]:
    return {"state": _gate_state(), "version_seen": _gate_seen["version"]}


def _gate_block_or_unknown() -> dict[str, Any]:
    """:func:`_gate_block`, or an unknown gate when its state cannot be read.

    The state reads files (the gate's token, ``api.env``, the auth config), and ``/status``
    is the page the dashboard polls: nothing here may turn it into a 500. The failure is
    logged once.
    """
    try:
        return _gate_block()
    except Exception:
        _warn_once("gate_block", "comfyui.gate_state_unavailable")
        return {"state": "unknown", "version_seen": None}


def _gate_refusal_view() -> dict[str, Any] | None:
    return None if _gate_refusal is None else dict(_gate_refusal)


async def _fetch_json(path: str) -> dict[str, Any] | None:
    """GET ``path`` from ComfyUI and return parsed JSON, or None if unreachable.

    Fail-soft by contract: any connection / HTTP / decode error returns None so
    the aggregator can report ``reachable: false`` instead of erroring.
    """
    url = f"{_comfyui_base_url()}{path}"
    try:
        resp = await _get_client().get(url)
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data if isinstance(data, dict) else None
    except (httpx.HTTPError, ValueError):
        return None


async def _fetch_engine_stats() -> dict[str, Any] | None:
    """GET ComfyUI's /system_stats with the budget's short timeouts, or None.

    Same fail-soft contract as :func:`_fetch_json`, but a per-request timeout
    (:data:`_ENGINE_TIMEOUT`) instead of the shared client's, which ``/status``
    keeps using unchanged.
    """
    url = f"{_comfyui_base_url()}/system_stats"
    try:
        resp = await _get_client().get(url, timeout=_ENGINE_TIMEOUT)
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data if isinstance(data, dict) else None
    except (httpx.HTTPError, ValueError):
        return None


async def engine_used_mb() -> float | None:
    """ComfyUI's own GPU usage in MiB (``vram_total - vram_free``), or None.

    The memory budget's measured figure for the image engine (ADR-0039); callers
    fall back to the img slot's attribution on None. None too when ComfyUI runs on
    the CPU: its ``vram_*`` fields then describe system RAM, which is not the GPU
    pool and must never be read as the engine's share of it. Cached for
    :data:`_ENGINE_TTL_S`, failures included, and bounded by
    :data:`_ENGINE_TIMEOUT`, so the dashboard's 2.5 s poll costs ComfyUI at most
    one short read per TTL. A read cancelled by the caller's own deadline is
    cached as a failure too, so the next poll does not wait the same deadline out.
    """
    global _engine_cache
    cached = _engine_cache
    if cached is not None and _now() - cached[0] < _ENGINE_TTL_S:
        return cached[1]
    used_mb: float | None = None
    try:
        stats = await _fetch_engine_stats()
        used_bytes = None if _runs_on_cpu(stats) else _device_used_bytes(stats)
        if used_bytes is not None:
            used_mb = used_bytes / (1024.0 * 1024.0)
    finally:
        _engine_cache = (_now(), used_mb)
    return used_mb


async def _list_files_or_none() -> list[dict[str, Any]] | None:
    """The models-tree listing for ``/status``; ``None`` when the scan fails.

    The pane polls ``/status`` and a failed scan must cost it the inventory counts, not
    the whole answer. ``None``, not an empty listing, so the counts are hidden rather than
    shown as zeros nobody can vouch for. Counting needs no checkpoint architectures, so the
    scan skips reading their headers on every poll.
    """
    try:
        return await asyncio.to_thread(inventory.list_files, sniff=False)
    except Exception:
        log.warning("comfyui.inventory_scan_failed", exc_info=True)
        return None


async def _container_state(unit: str) -> str:
    """Return the generation container state: 'running' or 'absent'.

    The img slot runs as a systemd unit (podman), so ``unit``'s state is the
    truth. Any non-active state reads as 'absent' — the pane treats it as
    stopped.
    """
    return "running" if await _systemd_active(unit) else "absent"


async def _systemd_active(unit: str) -> bool:
    """True iff ``systemctl is-active <unit>`` reports the unit active."""
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return False
    try:
        proc = await asyncio.create_subprocess_exec(
            systemctl,
            "is-active",
            unit,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=2.0)
    except (TimeoutError, OSError):
        return False
    return out.decode("utf-8", "replace").strip() == "active"


def _bytes_to_gb(value: Any) -> float | None:
    if not isinstance(value, (int, float)) or value < 0:
        return None
    return round(value / 1024**3, 1)


def _first_device(stats: dict[str, Any] | None) -> dict[str, Any] | None:
    """The first device ComfyUI's /system_stats reports, or None."""
    devices = stats.get("devices") if stats else None
    if isinstance(devices, list) and devices and isinstance(devices[0], dict):
        return devices[0]
    return None


def _runs_on_cpu(stats: dict[str, Any] | None) -> bool:
    """True when the first device ComfyUI reports is the CPU (``type == "cpu"``)."""
    dev = _first_device(stats)
    return dev is not None and str(dev.get("type") or "").lower() == "cpu"


def _device_used_bytes(stats: dict[str, Any] | None) -> float | None:
    """The first device's ``vram_total - vram_free`` in bytes, or None.

    None when there is no device, either figure is missing or not a number, or
    free exceeds total. The one parse behind both the pane's GTT gauge and
    :func:`engine_used_mb`.
    """
    dev = _first_device(stats)
    if dev is None:
        return None
    total = dev.get("vram_total")
    free = dev.get("vram_free")
    if not isinstance(total, (int, float)) or not isinstance(free, (int, float)):
        return None
    used = total - free
    return used if used >= 0 else None


def _parse_memory(stats: dict[str, Any] | None) -> dict[str, Any] | None:
    """Fold ComfyUI's /system_stats into the pane's GTT + RAM gauges.

    GTT comes from the first reported device's vram_total/vram_free; RAM from the
    system block. Any missing field becomes ``None`` rather than a guessed value.
    """
    if not stats:
        return None
    gtt_used = gtt_ceil = None
    dev = _first_device(stats)
    if dev is not None:
        gtt_ceil = _bytes_to_gb(dev.get("vram_total")) or _GTT_CEIL_GB
        used = _device_used_bytes(stats)
        if used is not None:
            gtt_used = _bytes_to_gb(used)
    ram_used = None
    ram_ceil = None
    system = stats.get("system")
    if isinstance(system, dict):
        rtotal = system.get("ram_total")
        rfree = system.get("ram_free")
        ram_ceil = _bytes_to_gb(rtotal)
        if isinstance(rtotal, (int, float)) and isinstance(rfree, (int, float)):
            ram_used = _bytes_to_gb(rtotal - rfree)
    pressure = gtt_used is not None and gtt_used >= _PRESSURE_GB
    return {
        "gtt_used_gb": gtt_used,
        "gtt_ceil_gb": int(gtt_ceil) if gtt_ceil else _GTT_CEIL_GB,
        "ram_used_gb": ram_used,
        # Derive from the device's reported ram_total (the Strix box reports
        # ~128 GB, not the brief's 96) — fall back to the nominal ceiling only
        # when the field is absent, so the gauge never shows used > ceil.
        "ram_ceil_gb": int(ram_ceil) if ram_ceil else _RAM_CEIL_GB,
        "pressure": pressure,
    }


def _queue_counts(queue: dict[str, Any] | None) -> dict[str, int]:
    if not queue:
        return {"running": 0, "pending": 0}
    running = queue.get("queue_running")
    pending = queue.get("queue_pending")
    return {
        "running": len(running) if isinstance(running, list) else 0,
        "pending": len(pending) if isinstance(pending, list) else 0,
    }


def _as_pct(value: Any) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    return round(max(0.0, min(1.0, float(value))) * 100, 1)


def _mhz_to_ghz(value: Any) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    return round(float(value) / 1000.0, 2)


async def _gpu_telemetry(request: Request, *, render_active: bool) -> dict[str, float | int | None]:
    """Read live GPU util/temp/clock for /status, degrading per-field to null.

    ``gpu_busy_percent`` is a forced-high artifact on the target AMD host while
    the GPU is pinned to a high perf level, so util is exposed only while a
    ComfyUI render is actively running. Temp and shader clock are factual reads
    and are surfaced independently.
    """
    util: float | int | None = 0
    temp: float | None = None
    clock: float | None = None

    try:
        stats = getattr(request.app.state, "hardware_stats", None)
        if stats is not None and hasattr(stats, "gpu_sample"):
            sample = await asyncio.to_thread(stats.gpu_sample)
        else:
            sample = await asyncio.to_thread(gpu_view.sample)
        if render_active:
            util = _as_pct(getattr(sample, "gpu_busy", None))
    except Exception:
        util = 0 if not render_active else None

    try:
        power = await asyncio.to_thread(_probe_power)
        temp_value = power.get("gpu_temp_c") if isinstance(power, dict) else None
        clock_mhz = power.get("gpu_sclk_mhz") if isinstance(power, dict) else None
        temp = round(float(temp_value), 1) if isinstance(temp_value, (int, float)) else None
        clock = _mhz_to_ghz(clock_mhz)
    except Exception:
        temp = None
        clock = None

    return {"util": util, "temp": temp, "clock": clock}


def _engine_state(container: str, reachable: bool, running_jobs: int) -> str:
    # A reachable ComfyUI IS the engine truth — the resident container must
    # not render as "stopped".
    if reachable:
        return "generating" if running_jobs > 0 else "running"
    if container == "running":
        return "starting"  # container up but ComfyUI hasn't bound the port yet
    return "stopped"


def _get_arbiter(request: Request) -> Any | None:
    """The SlotManager's GpuArbiter off app.state, or None when unwired."""
    manager = getattr(request.app.state, "slot_manager", None)
    return getattr(manager, "arbiter", None)


def api_mode(arbiter_status: Any) -> str | None:
    """The API's mode ("generation" | "inference") for an arbiter snapshot, or None.

    "generation" only once image mode is entered and no switch or restore is
    running. The arbiter writes ``mode=img`` before a switch drains and unloads
    the LLM set, and keeps it while a restore reloads that set, so the
    persisted mode alone would announce the GPU as ComfyUI's while LLM weights
    still hold it. The ``hal0_gpu_gate`` node released up to v1.6.5, which an
    upgraded img container keeps running until it restarts, reads only this
    field: "inference" makes it ask for the switch and wait for "generation".
    None means "no arbiter answer": the mode is unknown (the resident img unit
    stays up in inference mode, so it cannot stand in).
    """
    if not isinstance(arbiter_status, dict):
        return None
    mode = arbiter_status.get("mode")
    if mode == "llm":
        return "inference"
    if mode == "img":
        return "inference" if arbiter_status.get("switching") else "generation"
    return None


def _arbiter_status(arbiter: Any) -> dict[str, Any] | None:
    """``arbiter.status()``, or None when there is no arbiter or it cannot answer."""
    if arbiter is None:
        return None
    try:
        status = arbiter.status()
    except Exception:
        return None
    return status if isinstance(status, dict) else None


@router.get("/status", dependencies=[Depends(_gate_call)])
async def comfyui_status(request: Request) -> dict[str, Any]:
    """Aggregate systemd + ComfyUI HTTP into one engine-status object."""
    img_unit, img_container = await _img_artefacts(request)
    # The gate block reads files (the token, api.env, the auth config): off the event loop,
    # on every poll, so what the operator just changed shows at once.
    container, hermes, stats, queue, files, gate = await asyncio.gather(
        _container_state(img_unit),
        _systemd_active(_HERMES_UNIT),
        _fetch_json("/system_stats"),
        _fetch_json("/queue"),
        _list_files_or_none(),
        asyncio.to_thread(_gate_block_or_unknown),
    )
    reachable = stats is not None
    counts = _queue_counts(queue)
    telemetry = await _gpu_telemetry(request, render_active=counts["running"] > 0)
    engine = _engine_state(container, reachable, counts["running"])
    # Arbiter snapshot is fail-soft like every other probe here: a missing
    # manager or a corrupt state file degrades to null, never a 500.
    arbiter_block: dict[str, Any] | None = None
    try:
        arbiter = _get_arbiter(request)
        if arbiter is not None:
            arbiter_block = arbiter.status()
    except Exception:
        arbiter_block = None
    # Mode: the arbiter is the only source of truth (see api_mode). The
    # resident img unit stays up in inference mode too, so its state is NOT
    # mode evidence: with no arbiter answer the mode is ``None``.
    mode = api_mode(arbiter_block)
    return {
        "mode": mode,
        "reachable": reachable,
        "engine": engine,
        "container": {"name": img_container, "state": container},
        # Resident container: the web UI is usable whenever ComfyUI answers,
        # regardless of which mode owns the GPU memory.
        "endpoint": ":8188" if (reachable or mode == "generation") else None,
        "memory": _parse_memory(stats),
        "queue": counts,
        **telemetry,
        # The hal0-submitted render (/v1 and the Generate tab) ComfyUI is running: model,
        # step n/N, node, elapsed. None when none is live. A render from the ComfyUI editor
        # has no entry (its client id is not hal0's), so for those the pane shows
        # queue-level state.
        "progress": render_progress.current(),
        "inference": {"hermes": hermes},
        # Verified counts of the weight files on disk, never faked; None without a models
        # tree or when the scan failed.
        "inventory": None if files is None else inventory.status_counts(files),
        "switchover": dict(_switch),
        "arbiter": arbiter_block,
        # The GPU gate in the img container (the hal0_gpu_gate node): whether it is the
        # gate this hal0 ships (``current``), an older one (``stale``: restart the engine),
        # not heard from since hal0-api started (``unknown``), or one that hal0 requires to
        # authenticate and that has no credential (``no_credential``); and the latest
        # prompt it refused, ``{"at": epoch seconds, "reason": str}``, else null.
        "gate": gate,
        "gate_refusal": _gate_refusal_view(),
    }


@router.get("/arbiter", dependencies=[Depends(_gate_call)])
async def comfyui_arbiter(request: Request) -> dict[str, Any]:
    """The GPU arbiter's state alone, for the ``hal0_gpu_gate`` node.

    The gate reads this before every prompt, hal0's own ``/v1`` renders
    included, with a 2 s budget, and refuses the prompt when hal0 does not
    answer. So it runs none of ``/status``'s probes (systemd, ComfyUI's HTTP
    API, the models scan): only ``mode`` (see :func:`api_mode`), the
    ``arbiter`` block, the ``switchover`` tracker and the gate's latest
    ``gate_refusal``, in the same shape.

    The call is noted as the gate's, by its ``X-Hal0-Gate-Version`` header or, without
    one, by the gate's default HTTP client (see :func:`_gate_call`): that is what
    ``/status``'s ``gate.state`` reads.
    """
    arbiter_block = _arbiter_status(_get_arbiter(request))
    return {
        "mode": api_mode(arbiter_block),
        "arbiter": arbiter_block,
        "switchover": dict(_switch),
        "gate_refusal": _gate_refusal_view(),
    }


#: How long the prompt hand-off waits for the switch lock: under the gate's 2 s
#: request timeout, so a busy lock answers "not held" (the gate re-reads the
#: status) instead of timing the request out.
_HANDOFF_LOCK_WAIT_S = 1.5


@router.post("/prompt-handoff", dependencies=[Depends(_gate_call)])
async def comfyui_prompt_handoff(request: Request) -> JSONResponse:
    """The ``hal0_gpu_gate`` node announces an editor prompt in image mode.

    Answers ``{"held": true}`` once the arbiter holds the GPU for the prompt
    until ComfyUI's queue can show it (:meth:`GpuArbiter.hold_for_prompt`),
    and ``{"held": false}`` when the GPU is not ComfyUI's (the gate then asks
    for the switch) or a switch or restore holds the lock right now (the gate
    re-reads the status).
    """
    arbiter = _get_arbiter(request)
    if arbiter is None:
        return _arbiter_unavailable()
    try:
        held = await asyncio.wait_for(arbiter.hold_for_prompt(), _HANDOFF_LOCK_WAIT_S)
    except TimeoutError:
        held = False
    return JSONResponse(status_code=200, content={"held": bool(held)})


class GateRefusalBody(BaseModel):
    # ``reason`` accepts any JSON so a missing or non-string value routes to the handler's
    # custom ``comfyui.invalid_gate_refusal`` 422 (as ``/pin`` does for its body) rather
    # than a generic pydantic ``validation.error``.
    model_config = ConfigDict(extra="ignore")
    reason: Any = None


@router.post("/gate-refusal", status_code=202, dependencies=[Depends(_gate_call)])
async def comfyui_gate_refusal(body: GateRefusalBody | None = None) -> JSONResponse:
    """The ``hal0_gpu_gate`` node reports a prompt it refused (best effort).

    Body: ``{"reason": str}``, the text the editor was shown. hal0 keeps the latest, cut
    to 500 characters, with the time it arrived: ``/status`` and ``/arbiter`` report it
    as ``gate_refusal``. Nothing here acts on it. The route is the gate's own (the gate's
    token admits it, see :mod:`hal0.api.auth`) and is not an MCP tool.
    """
    reason = body.reason if body is not None else None
    if not isinstance(reason, str) or not reason.strip():
        raise UnprocessableEntity(
            "body must be {'reason': a non-empty string}",
            code="comfyui.invalid_gate_refusal",
        )
    record_gate_refusal(reason.strip())
    return JSONResponse(status_code=202, content={"status": "recorded"})


async def _run_switch(arbiter: Any, mode: str, *, pin: bool = False, force: bool = False) -> None:
    """Drive the arbiter for ``mode``; record failure for /status to surface."""
    try:
        if mode == "generation":
            await arbiter.ensure_img(pin=pin)
        else:
            await arbiter.restore_llm(force=force)
    except Exception as exc:  # any failure must land in /status, never raise
        _switch["error"] = f"{mode}: {exc}"
    finally:
        _switch["active"] = False
        _switch["target"] = None


_ARBITER_UNAVAILABLE_MESSAGE = "slot manager / GPU arbiter is not wired on this app"


def _arbiter_unavailable() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "error": {
                "code": "comfyui.arbiter_unavailable",
                "message": _ARBITER_UNAVAILABLE_MESSAGE,
            }
        },
    )


class SwitchoverBody(BaseModel):
    # Fields stay optional (not a required Literal) so an empty/omitted body or
    # a bad ``mode`` still routes to the handler's custom ``comfyui.invalid_mode``
    # 422 rather than a generic pydantic ``validation.error`` — the wire contract
    # (status + code) is byte-preserved. ``extra="ignore"`` matches today's
    # silent-pass on unrecognised fields.
    model_config = ConfigDict(extra="ignore")
    mode: str | None = None
    force: bool = False
    pin: bool = False


@router.post("/switchover", dependencies=[Depends(_gate_call)])
async def comfyui_switchover(
    request: Request, background_tasks: BackgroundTasks, body: SwitchoverBody | None = None
) -> JSONResponse:
    """Flip the iGPU between LLM inference and ComfyUI generation.

    Body: ``{"mode": "generation" | "inference", "force": bool, "pin": bool}``
    (``pin`` only matters for generation: hold image mode until unpinned;
    without it, image mode ends ``_RESTORE_GRACE_S`` after the last render).
    Refuses while a switch is in flight (409), no-ops when already in the
    target mode (200), and refuses to drop a busy render queue without
    ``force`` (409). Otherwise answers 202 and drives the GpuArbiter in the
    background — track completion via the ``switchover`` block on
    ``GET /status``.

    The endpoint is always available when the SlotManager/GpuArbiter is wired:
    ComfyUI prompt submission can call it as the implicit handoff before
    enqueueing a render.

    A request the ComfyUI GPU gate's own token admitted (#3058) may only ask
    for generation, without ``force`` or ``pin``: 403 otherwise. A request that
    carries the gate's version header and is rejected (that 403, a bad mode, no
    arbiter) is recorded as the gate's latest refusal, for ``/status``.
    """
    if body is None:
        body = SwitchoverBody()
    mode = body.mode
    if request.scope.get(GATE_SCOPE_KEY) and (mode != "generation" or body.force or body.pin):
        message = "ComfyUI's GPU gate may only ask for image mode, without force or pin"
        _record_gate_rejection(request, 403, message)
        raise Forbidden(message, code="auth.forbidden")
    if mode not in _MODES:
        message = "body must be {'mode': 'generation' | 'inference'}"
        _record_gate_rejection(request, 422, message)
        raise UnprocessableEntity(message, code="comfyui.invalid_mode")
    # One switch at a time — racing systemctl calls is never right. Checked
    # before the noop probe so a mid-flight flip is reported as in-progress, not
    # as "already there" based on a half-transitioned snapshot.
    if _switch["active"]:
        raise Conflict(
            f"a switch to {_switch['target']} is already running",
            code="comfyui.switch_in_progress",
        )
    # Idempotency: the arbiter is the source of truth for the current mode
    # (D7). The img unit stays up in inference mode, so it cannot tell the
    # modes apart: when the arbiter cannot answer, refuse rather than guess.
    # Generation is already done only once no switch or restore is running
    # (api_mode); inference is done once the persisted mode is llm, so a Stop
    # issued mid-switch into image mode is not swallowed.
    arbiter = _get_arbiter(request)
    arbiter_st = _arbiter_status(arbiter)
    current = api_mode(arbiter_st)
    if arbiter_st is None or current is None:
        _record_gate_rejection(request, 503, _ARBITER_UNAVAILABLE_MESSAGE)
        return _arbiter_unavailable()
    already = current == "generation" if mode == "generation" else arbiter_st.get("mode") == "llm"
    if already:
        return JSONResponse(status_code=200, content={"status": "noop", "mode": mode})
    # Mid-render guard: switching to inference frees ComfyUI's models from GPU
    # memory, killing any running/pending renders (the container itself stays
    # up). A /v1 image request counts too: it holds an image job from before
    # its prompt reaches ComfyUI's queue. Refuse unless the caller forces it —
    # the dashboard confirm dialog states the blast radius and passes force on
    # user confirm.
    force = body.force
    if mode == "inference" and not force:
        counts = _queue_counts(await _fetch_json("/queue"))
        image_jobs = int((arbiter.status() or {}).get("image_jobs") or 0)
        busy = counts["running"] + counts["pending"] + image_jobs
        if busy:
            raise Conflict(
                f"{busy} render job(s) running or queued would be dropped; "
                "retry with {'force': true} to switch anyway.",
                code="comfyui.busy",
                details={"queue": counts, "image_jobs": image_jobs},
            )
    pin = body.pin
    # Dispatch in the background and answer 202 immediately — the drain/reload
    # takes seconds to tens of seconds (slot unloads, container boot) and the
    # pane's /status poll tracks the transition via the switchover block.
    _switch.update(active=True, target=mode, error=None)
    background_tasks.add_task(_run_switch, arbiter, mode, pin=pin, force=force)
    return JSONResponse(status_code=202, content={"status": "switching", "mode": mode})


class SetPinnedBody(BaseModel):
    # ``pinned`` accepts any JSON so a missing/non-bool value routes to the
    # handler's custom ``comfyui.invalid_pin`` 422 (byte-preserved wire contract)
    # instead of a generic pydantic ``validation.error``.
    model_config = ConfigDict(extra="ignore")
    pinned: Any = None


@router.post("/pin")
async def comfyui_pin(request: Request, body: SetPinnedBody | None = None) -> JSONResponse:
    """Toggle the arbiter's manual pin.

    Body: ``{"pinned": bool}``. A pin holds image mode until unpinned or a
    forced switch to inference. Pinning needs image mode: the next switch into
    image mode takes its pin from its own request (``switchover`` with
    ``pin``), so a pin set in LLM mode would never hold and is refused with
    409 ``comfyui.not_image_mode``. Unpinning always succeeds.
    """
    pinned = body.pinned if body is not None else None
    if not isinstance(pinned, bool):
        raise UnprocessableEntity(
            "body must be {'pinned': true | false}",
            code="comfyui.invalid_pin",
        )
    arbiter = _get_arbiter(request)
    if arbiter is None:
        return _arbiter_unavailable()
    if pinned:
        arbiter_st = _arbiter_status(arbiter)
        if arbiter_st is None:
            return _arbiter_unavailable()
        if arbiter_st.get("mode") != "img":
            raise Conflict(
                "image mode is not active, so there is nothing to pin; switch with "
                "{'mode': 'generation', 'pin': true} to enter image mode pinned",
                code="comfyui.not_image_mode",
            )
    arbiter.set_pin(pinned)
    return JSONResponse(status_code=200, content={"pinned": pinned})


# ---------------------------------------------------------------------------
# Image models: catalog, installs, jobs, files (ADR-0037)
# ---------------------------------------------------------------------------


def _engine_availability() -> dict[str, Any]:
    """``{available, reason}``: whether this host can run the ComfyUI image at all.

    ``comfyui_backends()`` is empty when the host has no ROCm lane (any GPU vendor), and the dashboard
    shows ``reason`` beside the catalog.
    """
    available = bool(comfyui_backends())
    return {"available": available, "reason": None if available else COMFYUI_NO_ENGINE_REASON}


def _template_defaults(model: ImageModel) -> dict[str, Any]:
    """The default sampler parameters the model's workflow template declares, if any."""
    meta = load_template(model.template).get("_meta")
    defaults = meta.get("defaults") if isinstance(meta, dict) else None
    return dict(defaults) if isinstance(defaults, dict) else {}


def _model_row(
    model: ImageModel,
    *,
    recommended: str,
    job: PullJob | None,
    root: Path,
    budget: MemoryBudget | None,
) -> dict[str, Any]:
    """One catalog model with its install state, as ``GET /models`` lists it.

    ``missing`` names each absent weight file relative to the models root, like the
    paths ``DELETE /models/{id}`` reports. ``job`` is the model's latest install, if any.
    ``root`` is the models tree, resolved once per request by the caller. ``approx_gb``
    and ``missing_gb`` are GiB, like every GB figure the memory budget reports (ADR-0039);
    the catalog's own decimal GB never leaves it.

    ``fit`` is the verdict at the model's native size against ``budget`` (collected once
    per request by the caller), as :meth:`~hal0.comfyui.fit.Fit.as_dict`; ``None`` when
    there is no budget or it cannot say.
    """
    state = install_state(model, root)
    fit = _fit(model, None, budget)
    return {
        "id": model.id,
        "label": model.label,
        "task": model.task,
        "api_renderable": model.api_renderable,
        "recommended": model.id == recommended,
        "license": model.license,
        "est_seconds": model.est_seconds,
        "sizes": list(model.sizes),
        "notes": model.notes,
        "defaults": _template_defaults(model),
        "approx_gb": weights_gib(model),
        "missing_gb": missing_gib(state),
        "state": state.state,
        "missing": [f"{c.kind.subdir}/{c.relpath}" for c in state.missing],
        "workflow": model.workflow_filename,
        "job": job.as_dict() if job is not None else None,
        "fit": fit,
    }


async def _collect_budget(app: FastAPI) -> MemoryBudget | None:
    """This box's memory budget, or ``None`` when it cannot be collected.

    ``collect_memory_budget`` has no outer guard, by design: an empty budget would read as
    "no GPU". Here a failure is an unknown budget, so the fit is ``null`` and never
    ``wont_fit``. The failure is logged once per request.
    """
    try:
        return await memory_source.collect_memory_budget(app)
    except Exception:
        log.warning("comfyui.fit_budget_unavailable", exc_info=True)
        return None


def _fit(model: ImageModel, size: str | None, budget: MemoryBudget | None) -> dict[str, Any] | None:
    """``model``'s fit at ``size`` (``None`` is native) as a dict; ``None`` when unknown.

    A model whose catalog facts cannot be judged (a measured size that is not ``WxH``) is an
    unknown fit, like an unknown budget: one bad entry must not fail ``GET /models`` or
    ``hal0 comfyui models list``. The failure is logged once per model.
    """
    if budget is None:
        return None
    try:
        fit = assess(need_for(model, size), budget)
    except Exception:
        _warn_once(f"fit:{model.id}", "comfyui.fit_failed", model_id=model.id)
        return None
    return fit.as_dict() if fit is not None else None


def _store_info(root: Path) -> dict[str, Any]:
    """``{"free_gb", "path"}`` for the models tree ``root``.

    ``path`` is ``root`` itself. ``free_gb`` is the free space, in GiB to one decimal, of
    the filesystem it lives on: ``root``'s nearest existing parent when the tree does not
    exist yet (a fresh store), the same place the install's disk preflight measures.
    ``None`` when the filesystem cannot be measured, never 0.
    """
    probe = comfyui_install._existing_ancestor(root)
    try:
        free_gb: float | None = round(shutil.disk_usage(probe).free / BYTES_PER_GIB, 1)
    except OSError:
        free_gb = None
    return {"free_gb": free_gb, "path": str(root)}


def _install_jobs(app: FastAPI) -> dict[str, PullJob]:
    jobs: dict[str, PullJob] = app.state.image_install_jobs
    return jobs


def _tree_lock(app: FastAPI) -> asyncio.Lock:
    """Serialises the install route's check-then-start with a removal running in a worker thread.

    An install must not start (and skip a file as present) while the files it would skip are
    being deleted. The lock belongs to the app, created in its lifespan, because an
    ``asyncio.Lock`` binds to the first event loop that contends on it, and a module-level one
    outlives the app (and its loop) in anything that builds more than one.
    """
    lock: asyncio.Lock = app.state.image_tree_lock
    return lock


def _require_model(model_id: str) -> ImageModel:
    model = get_model(model_id)
    if model is None:
        raise comfyui_install.ImageModelNotFound(
            f"no image model {model_id!r}", details={"model_id": model_id}
        )
    return model


def _install_running(
    busy: ImageModel, job: PullJob, *, removing: ImageModel | None = None
) -> Conflict:
    """409 ``comfyui.install_running`` for ``busy``'s active ``job``.

    ``removing`` is the model a delete was asked for, when it is not ``busy`` itself but
    shares weight files with it. ``details.busy_model_id`` names the model that is installing.
    """
    if removing is None or removing.id == busy.id:
        message = f"an install of {busy.label} is already {job.state}"
    else:
        message = (
            f"an install of {busy.label} is {job.state} and loads files shared with "
            f"{removing.label}; wait for it to finish"
        )
    return Conflict(
        message,
        code="comfyui.install_running",
        details={"job": job.as_dict(), "busy_model_id": busy.id},
    )


def _blocking_install(model: ImageModel, jobs: dict[str, PullJob]) -> PullJob | None:
    """The active install that removing ``model`` would cut under: its own, or a neighbour's.

    A neighbour is any model loading a file ``model`` also loads. Its installer may have
    skipped that file as already present, so deleting it would leave the neighbour
    reporting ``completed`` with the file gone.
    """
    root = comfyui_paths.models_root()
    candidates = [model.id, *(other for c in model.components for other in models_using(c, root))]
    for model_id in dict.fromkeys(candidates):  # model first, then neighbours, once each
        job = jobs.get(model_id)
        if job is not None and job.state in _ACTIVE_JOB_STATES:
            return job
    return None


@router.get("/models")
async def comfyui_models(request: Request) -> dict[str, Any]:
    """The image-model catalog with each model's install state and latest install job.

    Returns ``{"models": [...], "engine": {"available": bool, "reason": str | None},
    "store": {"free_gb": float | None, "path": str},
    "recommendation": {"id", "pool_gb", "limited_by"} | None}``, the models in catalog order.
    Exactly one row is ``recommended``: the starter pick for this box's memory pool.
    ``recommendation`` says what the pick was chosen from: ``pool_gb`` is the figure
    ``picker.image_pool_gb`` used and ``limited_by`` is ``"ram"`` when the RAM limit set it
    and ``"gpu"`` when the GPU pool did; it is ``null`` when the pool is unknown (a box that
    was never probed). ``engine`` says
    whether this host can run the ComfyUI image at all; the catalog is listed either way, so
    a box without the ROCm lane can still see it. ``store`` is the models tree and the free
    space (GiB) of the filesystem under it; ``free_gb`` is ``null`` when that cannot be
    measured.

    Each row's ``fit`` is the model's verdict at its native size against this box's memory
    budget, collected once per request: ``{"verdict": "fits" | "tight" | "wont_fit",
    "reason", "need", "render_free_gb", "ram_free_gb"}``. It is ``null`` when the budget is
    unknown (no GPU lane, nothing measured the pool, RAM unreadable, or the collector
    failed); an unknown budget is never ``wont_fit``. See ``GET /models/{model_id}/fit`` for
    other sizes.

    409 ``comfyui.models_dir_not_writable`` (``error.details.path``) when the models tree
    cannot be read by the hal0 user.
    """
    jobs = _install_jobs(request.app)
    # One read of the hardware file names both the pick and the figure it came from, so the
    # row marked `recommended` and `recommendation.id` cannot differ.
    pick = recommendation()
    recommended = pick["id"] if pick else recommended_id()
    root = comfyui_paths.models_root()
    budget = await _collect_budget(request.app)
    with comfyui_install.readable_tree():
        models = [
            _model_row(
                model, recommended=recommended, job=jobs.get(model.id), root=root, budget=budget
            )
            for model in CATALOG.values()
        ]
        store = _store_info(root)
    return {
        "models": models,
        "engine": _engine_availability(),
        "store": store,
        "recommendation": pick,
    }


# The wire form of a size: two to four ASCII digits a side, lower-case ``x``. Matched whole
# (``fullmatch``), so a trailing newline does not slip through ``$``.
_FIT_SIZE = re.compile(r"[0-9]{2,4}x[0-9]{2,4}")


@router.get("/models/{model_id}/fit")
async def comfyui_model_fit(
    model_id: str, request: Request, size: str | None = None
) -> dict[str, Any]:
    """The model's fit at ``size`` (``WxH``; native when omitted) against this box's memory.

    Returns ``{"fit": {...} | null, "size": "WxH" | null}``. ``fit`` is the same object a
    ``GET /models`` row carries, here for any size: a size the catalog measured is exact, any
    other is estimated from the nearest larger measurement (``need.gpu_estimated``), and it
    is ``null`` when the budget is unknown. ``size`` is the size that was judged: the one
    ``POST /v1/images/generations`` would render for this ``size``, each side clamped to
    64..2048 and floored to a multiple of 8 (:func:`effective_size`), so ``1030x1030``
    answers for ``1024x1024`` and says so. It is the model's native size when ``size`` is
    omitted, and ``null`` for a model with none. 404 ``comfyui.model_unknown`` for an id not
    in the catalog, and 422 ``image.invalid_size`` (``error.details.size``) for a ``size``
    that is not two to four digits, ``x``, two to four digits. The size is validated before
    the budget is read.
    """
    model = _require_model(model_id)
    if size is not None and _FIT_SIZE.fullmatch(size) is None:
        raise UnprocessableEntity(
            f"size must be WIDTHxHEIGHT in pixels, for example 1024x1024; got {size!r}",
            code="image.invalid_size",
            details={"size": size},
        )
    judged = _judged_size(model, size)
    budget = await _collect_budget(request.app)
    return {"fit": _fit(model, judged, budget), "size": judged}


def _judged_size(model: ImageModel, size: str | None) -> str | None:
    """The ``WxH`` a render of ``model`` at ``size`` is judged at, as ``/v1`` renders it.

    ``size`` goes through the translator's own parse and clamp (:func:`effective_size`), so
    the verdict is for the render that would run, not for a size the workflow would change.
    ``None`` is the model's native size, clamped the same way; a model with no native size
    has none to report.
    """
    if size is None:
        if not model.sizes:
            return None
        size = model.sizes[0]
    return effective_size(size)


def _schedule_install(app: FastAPI, model: ImageModel, job: PullJob) -> None:
    """Run the install as a detached task tracked for shutdown, never a BackgroundTask.

    Same reasoning as ``hal0.registry.pull_jobs.schedule_pull_task`` (#1225): a
    background task would hold the request's connection open for the whole download
    and block a graceful restart. ``app.state.image_install_tasks`` is what the lifespan
    shutdown cancels.
    """
    on_complete = functools.partial(
        _after_install,
        orchestrator=getattr(app.state, "capability_orchestrator", None),
        tree_lock=_tree_lock(app),
    )
    task = asyncio.create_task(
        comfyui_install.run_install(
            job, model, hf_token=hf_token_from_env(), on_complete=on_complete
        )
    )
    tasks: dict[str, asyncio.Task[None]] = app.state.image_install_tasks
    tasks[model.id] = task

    def _untrack(finished: asyncio.Task[None], _model_id: str = model.id) -> None:
        if tasks.get(_model_id) is finished:
            tasks.pop(_model_id, None)

    task.add_done_callback(_untrack)


def _rewire_open_webui(model_id: str) -> None:
    """Re-render Open WebUI's image-generation env after the model set changed; never raises.

    A no-op when the Open WebUI unit is not installed or the rendered bytes did not change
    (:func:`~hal0.components.openwebui_arm.reconcile_openwebui_env_background`), so it is
    cheap enough to run after every install and removal. It blocks (it may restart the
    unit), so callers run it off the event loop.
    """
    try:
        from hal0.components.openwebui_arm import reconcile_openwebui_env_background

        reconcile_openwebui_env_background()
    except Exception:
        log.exception(
            "comfyui.follow_up_failed", extra={"step": "open_webui", "model_id": model_id}
        )


async def _follow_up(step: str, model_id: str, run: Callable[[], Awaitable[Any]]) -> None:
    """Await ``run()``; log and swallow its failure so the steps after it still happen."""
    try:
        await run()
    except Exception:
        log.exception("comfyui.follow_up_failed", extra={"step": step, "model_id": model_id})


async def _after_install(model: ImageModel, *, orchestrator: Any, tree_lock: asyncio.Lock) -> None:
    """What follows a finished install, in order: workflows, auto-select, Open WebUI.

    ``ensure_workflows`` covers models whose weights were put on disk by hand, which no
    install ever provisions; it runs under ``tree_lock`` so it cannot write the workflow of a
    model a removal is deleting at that moment. Auto-select makes the first renderable model
    the ``img`` capability's pick; the Open WebUI rewire matters for a model the API can
    render, since ``orchestrator.apply`` called directly does not reconcile it and an
    install of the already-selected model changes nothing else that would. Every step is
    best-effort and independent: a failure is logged and the install, already ``completed``,
    stays so.
    """

    async def provision() -> None:
        async with tree_lock:
            await asyncio.to_thread(comfyui_install.ensure_workflows)

    await _follow_up("ensure_workflows", model.id, provision)
    await _follow_up(
        "auto_select",
        model.id,
        lambda: auto_select_first_image_model(model, orchestrator=orchestrator),
    )
    if model.api_renderable:
        await asyncio.to_thread(_rewire_open_webui, model.id)


async def start_image_install(app: FastAPI, model_id: str) -> PullJob:
    """Start downloading what the model is missing and return the new install job.

    The one place an image install starts: ``POST /models/{model_id}/install`` calls it
    (its docstring lists every refusal this raises, before any job exists), and so does the
    installer for the ``img`` selections of ``POST /api/install/apply`` and
    ``/apply-selections``. The job runs in the background on ``app.state.image_install_jobs``.
    """
    model = _require_model(model_id)
    jobs = _install_jobs(app)
    async with _tree_lock(app):  # waits only while a removal is running
        existing = jobs.get(model_id)
        if existing is not None and existing.state in _ACTIVE_JOB_STATES:
            raise _install_running(model, existing)
        with comfyui_install.readable_tree():
            state = install_state(model)
            provisioned = (comfyui_paths.workflows_dir() / model.workflow_filename).is_file()
            if state.state == "complete" and provisioned:
                raise Conflict(
                    f"{model.label} is already installed",
                    code="comfyui.model_installed",
                    details={"model_id": model_id},
                )
            if state.missing:  # the guards are about downloads; a workflow write needs none
                comfyui_install.require_one_filesystem()
                comfyui_install.require_writable_destinations(model)
                comfyui_install.preflight_disk(model)
        comfyui_install.clear_removed(model.id)  # asking for it is the operator changing their mind
        job = make_job(model_id)
        jobs[model_id] = job
        _schedule_install(app, model, job)
    return job


@router.post("/models/{model_id}/install", status_code=202)
async def comfyui_model_install(model_id: str, request: Request) -> JSONResponse:
    """Start downloading what the model is missing; 202 with the new install job.

    Refused before any job exists with 404 ``comfyui.model_unknown``, 409
    ``comfyui.install_running`` (its job rides in ``error.details.job``), 409
    ``comfyui.model_installed`` (every weight is on disk and its workflow is provisioned),
    409 ``comfyui.staging_cross_device`` (download staging and models tree on two
    filesystems), 409 ``comfyui.models_dir_not_writable`` (a destination directory the
    hal0 user cannot write, or a models tree it cannot read; ``error.details.path``) and 507
    ``comfyui.insufficient_disk``.
    The job then runs in the background; follow it on ``GET /jobs``. A finished job stays
    listed until the next install of the model replaces it.

    A model whose weights are all on disk but whose workflow is not (another model loading
    the same files completed it, or the weights were put there by hand) is not refused:
    the job downloads nothing and writes the workflow, the only thing the launcher lists.
    The download guards (one filesystem, writable destinations, free space) are skipped then.
    """
    job = await start_image_install(request.app, model_id)
    return JSONResponse(status_code=202, content={"job": job.as_dict()})


@router.delete("/models/{model_id}")
async def comfyui_model_remove(
    model_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    include_shared: bool = False,
) -> dict[str, Any]:
    """Delete the model's weight files and workflow; the body says what went and what stayed.

    Returns ``{"removed": [...], "kept": [...], "affected": [...]}``: the paths deleted
    (weights, then ``workflows/<file>``); each weight file left in place, as
    ``{"relpath", "models"}`` with the complete models that hold it; and the other models
    that were complete and are not now.

    A file another complete model loads stays (``kept``) unless ``include_shared=true``,
    which deletes every file of the model and lists the models it broke in ``affected``.
    Models can share a whole file set, so without the flag a removal may free only the
    workflow. 409 ``comfyui.install_running`` while the model's install is queued or
    running, or while an install of a model that shares a weight file with it is
    (``error.details.busy_model_id`` names that model), in either mode. 409
    ``comfyui.remove_failed`` when a file cannot be deleted (a read-only directory):
    ``error.details`` lists what was removed before it and the path that failed. 409
    ``comfyui.models_dir_not_writable`` when the models tree cannot be read by the hal0
    user. After a removal, including one that stopped partway, Open WebUI's
    image-generation env is re-rendered, so a model that is gone is no longer wired.
    The removal is also recorded: a model whose weights other models hold stays
    ``complete``, and without the record the next startup or install would give it its
    workflow back; installing the model again clears it.
    """
    model = _require_model(model_id)
    try:
        async with _tree_lock(request.app):
            job = _blocking_install(model, _install_jobs(request.app))
            if job is not None:
                raise _install_running(CATALOG[job.model_id], job, removing=model)
            # The delete runs off the event loop (hundreds of unlinks on a slow store); the
            # lock keeps an install from starting under it.
            with comfyui_install.readable_tree():
                result = await asyncio.to_thread(
                    comfyui_install.remove_model, model, include_shared=include_shared
                )
    except comfyui_install.RemoveFailed:
        # Stopped partway: files may already be gone, so what Open WebUI is wired to may be
        # too. Background tasks only run behind a successful response, so do it here.
        await asyncio.to_thread(_rewire_open_webui, model_id)
        raise
    background_tasks.add_task(_rewire_open_webui, model_id)  # so a removed model un-wires
    return result


@router.get("/jobs")
async def comfyui_jobs(request: Request) -> dict[str, Any]:
    """The latest install job of every model that has had one, finished or not."""
    return {"jobs": [job.as_dict() for job in _install_jobs(request.app).values()]}


@router.post("/jobs/{model_id}/cancel", status_code=202)
async def comfyui_job_cancel(model_id: str, request: Request) -> JSONResponse:
    """Ask the model's install to stop at the next chunk; 202 with the job.

    Files that already landed stay, so a later install carries on from there. A job
    that already finished is returned unchanged. 404 ``comfyui.job_not_found`` when
    the model has no job.
    """
    job = _install_jobs(request.app).get(model_id)
    if job is None:
        raise NotFound(
            f"no install job for {model_id!r}",
            code="comfyui.job_not_found",
            details={"model_id": model_id},
        )
    if job.state in _ACTIVE_JOB_STATES:
        job.cancel_requested = True
    return JSONResponse(status_code=202, content={"job": job.as_dict()})


@router.get("/files")
async def comfyui_files() -> dict[str, Any]:
    """Every weight file under the models tree, with the catalog models that own it.

    Each row also says whether the listed path is a ``symlink``, which the delete route
    refuses.
    """
    files = await asyncio.to_thread(inventory.list_files)
    return {"root": str(comfyui_paths.models_root()), "files": files}


async def _was_the_selected_checkpoint(request: Request, subdir: str, relpath: str) -> bool:
    """True when the deleted ``subdir/relpath`` is, or may be, the file behind the ``img`` selection.

    Only a checkpoint the operator dropped in carries a ``ckpt/<path>`` id
    (:func:`~hal0.comfyui.inventory.ckpt_api_id`), and the selection does not change when
    its file goes. Call it after a delete succeeded, so ``relpath`` is a valid file path. A
    selection that cannot be read may have been this file, so it counts: the re-render this
    gates is a no-op when nothing changed.
    """
    if subdir != comfyui_paths.SUBDIRS["checkpoints"]:
        return False
    orchestrator = getattr(request.app.state, "capability_orchestrator", None)
    if orchestrator is None:
        return False
    try:
        selected = await enabled_img_model(orchestrator)
    except Exception:
        log.warning("comfyui.file_delete_selection_unreadable", exc_info=True)
        return True
    return selected == inventory.ckpt_api_id(relpath)


@router.delete("/files/{subdir}/{relpath:path}")
async def comfyui_file_delete(
    subdir: str, relpath: str, request: Request, background_tasks: BackgroundTasks
) -> dict[str, Any]:
    """Delete one weight file no catalog model loads; ``{"deleted": "<subdir>/<relpath>"}``.

    ``subdir`` and ``relpath`` are the row's fields from ``GET /files``, so a file the
    listing shows without an owner can be deleted, and no other. 400 ``comfyui.file_path_invalid``
    (``subdir`` is not a model directory, or ``relpath`` is absolute, climbs out with ``..``,
    has a hidden part such as an install's ``.hf_stage_*`` partial, does not end in a weight-file
    extension (a ``.yaml``, ComfyUI's ``put_checkpoints_here``), or leaves ``subdir``
    through a symlinked directory), 400 ``comfyui.file_symlink`` (the file is a link: delete
    its target), 404 ``comfyui.file_not_found``, 409 ``comfyui.file_catalog_owned``
    (``error.details.models``: remove the model instead) and 403 ``comfyui.file_permission``.

    Deleting the checkpoint behind the ``img`` selection (a ``ckpt/<path>`` id) re-renders
    Open WebUI's image-generation env, as removing a catalog model does, so it is no longer
    wired to a model that is gone. No lock with the installs is needed: an install only writes
    catalog files, which this route never deletes, and its partials are hidden.
    """
    await asyncio.to_thread(inventory.delete_file, subdir, relpath)
    if await _was_the_selected_checkpoint(request, subdir, relpath):
        background_tasks.add_task(_rewire_open_webui, inventory.ckpt_api_id(relpath))
    return {"deleted": f"{subdir}/{relpath}"}


def _user_workflows_dir() -> str:
    """ComfyUI's own saved-workflows dir (``user/default/workflows`` under the data root)."""
    return str(comfyui_paths.user_dir() / "default" / "workflows")


def _find_workflow(name: str) -> str | None:
    """Locate <name>.json, trying primary then user/default fallback. None if absent."""
    if not _WORKFLOW_NAME_RE.fullmatch(name) or ".." in name:
        return None
    primary = os.path.join(comfyui_paths.workflows_dir(), f"{name}.json")
    if os.path.isfile(primary):
        return primary
    fallback = os.path.join(_user_workflows_dir(), f"{name}.json")
    if os.path.isfile(fallback):
        return fallback
    return None


def _workflow_not_found_response() -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={
            "error": {
                "code": "comfyui.workflow_not_found",
                "message": "Workflow not found.",
            }
        },
    )


def _list_workflow_names() -> list[dict[str, Any]]:
    """Enumerate launchable ``<name>.json`` workflows across both search dirs.

    Scans the primary workflows dir and the ``user/default/workflows``
    fallback (both bind-mounted into the container, so operator-dropped
    files show up here). Names are deduped with primary winning; each entry
    carries the resolved ``source`` dir so the UI can hint where a file
    lives. Fail-soft: an unreadable/missing dir contributes nothing rather
    than raising, so the list endpoint never 500s on a fresh install.
    """
    seen: dict[str, dict[str, Any]] = {}
    for source, directory in (
        ("primary", str(comfyui_paths.workflows_dir())),
        ("user", _user_workflows_dir()),
    ):
        try:
            entries = sorted(os.listdir(directory))
        except OSError:
            continue
        for fn in entries:
            if not fn.endswith(".json"):
                continue
            name = fn[: -len(".json")]
            if not _WORKFLOW_NAME_RE.fullmatch(name) or ".." in name:
                continue
            # Primary wins — don't let the fallback shadow it.
            seen.setdefault(name, {"name": name, "source": source})
    return list(seen.values())


# ---------------------------------------------------------------------------
# GET /workflows — list launchable workflows from the bind-mounted dirs
# ---------------------------------------------------------------------------


@router.get("/workflows")
async def comfyui_workflows() -> dict[str, Any]:
    """List launchable workflow templates discoverable on disk.

    Returns ``{"workflows": [{"name", "source"}, ...]}`` — the names the
    UI can hand to ``POST /workflows/{name}/launch``. Operators drop
    ``<name>.json`` files into the bind-mounted workflows dir and they
    appear here without a rebuild. Empty list on a fresh install (no dir
    yet) rather than an error.
    """
    return {"workflows": _list_workflow_names()}


# ---------------------------------------------------------------------------
# POST /render/cancel — clear queue + interrupt current render
# ---------------------------------------------------------------------------


@router.post("/render/cancel", status_code=202)
async def comfyui_render_cancel() -> JSONResponse:
    """Cancel current and queued renders.

    Issues POST /queue (clear: true) and POST /interrupt to the ComfyUI
    HTTP API. Fail-soft: network errors are suppressed — the cancel
    intent is best-effort and the render state is visible via /status.
    """
    base = _comfyui_base_url()
    client = _get_client()

    async def _post(path: str, body: dict[str, Any]) -> None:
        with contextlib.suppress(Exception):
            await client.post(f"{base}{path}", json=body)

    await asyncio.gather(
        _post("/queue", {"clear": True}),
        _post("/interrupt", {}),
    )
    return JSONResponse(status_code=202, content={"status": "cancel_requested"})


# ---------------------------------------------------------------------------
# POST /restart — restart the slot-managed img runtime
# ---------------------------------------------------------------------------


@router.post("/restart", status_code=202)
async def comfyui_restart(request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
    """Restart the slot-managed ComfyUI runtime.

    Runs in the background and returns 202 immediately — the container
    takes several seconds to come back up. Track readiness via /status.
    """
    manager = getattr(request.app.state, "slot_manager", None)
    if manager is None or not hasattr(manager, "restart"):
        return _arbiter_unavailable()

    async def _restart_img() -> None:
        try:
            await manager.restart("img")
        except Exception as exc:
            log.warning("comfyui.img_restart_failed", extra={"error": str(exc)})

    background_tasks.add_task(_restart_img)
    return JSONResponse(status_code=202, content={"status": "restart_requested"})


# ---------------------------------------------------------------------------
# GET /logs — tail container logs
# ---------------------------------------------------------------------------


@router.get("/logs")
async def comfyui_logs(request: Request, tail: int = 60) -> JSONResponse:
    """Return the last N lines of the ComfyUI slot's journal output.

    Post-D9 the img slot runs as a podman container under its ``hal0-slot@<token>``
    systemd unit with ``StandardOutput=journal`` (the container itself uses the
    ``none`` log driver, so ``podman logs`` yields nothing). We therefore read the
    journal — matching the regular slot logs endpoint — rather than
    ``podman/docker logs``.

    Returns ``{"lines": []}`` when ``journalctl`` is absent or the slot
    has no journal entries yet — never a 500.
    """
    if shutil.which("journalctl") is None:
        return JSONResponse(status_code=200, content={"lines": []})
    n = max(1, min(int(tail or 60), 5000))
    img_unit, _container = await _img_artefacts(request)
    try:
        proc = await asyncio.create_subprocess_exec(
            "journalctl",
            "-u",
            img_unit,
            "-n",
            str(n),
            "--no-pager",
            "-o",
            "short-iso",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _err = await asyncio.wait_for(proc.communicate(), timeout=10.0)
    except (TimeoutError, OSError):
        return JSONResponse(status_code=200, content={"lines": []})
    combined = out.decode("utf-8", "replace")
    lines = [ln for ln in combined.splitlines() if ln]
    # journalctl emits a "-- No entries --" placeholder when the unit has
    # never logged; normalise that to an empty list for the UI's contract.
    if len(lines) == 1 and lines[0].strip().startswith("-- No entries"):
        lines = []
    # Same scrubber as /api/logs: the img slot's journal is raw container
    # output, readable since hal0-api got journal read (#2435).
    lines = [redact_log_line(ln) for ln in lines]
    return JSONResponse(status_code=200, content={"lines": lines})


# ---------------------------------------------------------------------------
# POST /workflows/{name}/launch — quick-launch a curated workflow
# ---------------------------------------------------------------------------


@router.post("/workflows/{name}/launch", status_code=202)
async def comfyui_workflow_launch(name: str) -> JSONResponse:
    """Quick-launch a workflow by name from the bind-mounted workflows directory.

    Reads <name>.json from the primary workflows dir, falling back to the
    user/default/workflows path. Posts the API-format workflow JSON to
    ComfyUI's /prompt endpoint and returns 202 with the prompt_id.
    404 when the workflow file does not exist. A prompt the GPU gate refuses (its
    503) is recorded as the gate's latest refusal and answered as ``/v1`` answers it:
    503 ``image.gate_refused`` with the gate's reason.
    """
    workflow_path = _find_workflow(name)
    if workflow_path is None:
        return _workflow_not_found_response()
    try:
        with open(workflow_path) as fh:
            workflow = fh.read()
        workflow_data = __import__("json").loads(workflow)
    except (OSError, ValueError) as exc:
        log.warning("Failed to read ComfyUI workflow %r from %s: %s", name, workflow_path, exc)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "comfyui.workflow_read_error",
                    "message": "Workflow could not be read.",
                }
            },
        )
    base = _comfyui_base_url()
    refusal: str | None = None
    try:
        resp = await _get_client().post(
            f"{base}/prompt",
            json={"prompt": workflow_data},
        )
        if resp.status_code == 200:
            result = resp.json()
        else:
            result = {}
            refusal = _gate_refusal_of(resp)
    except (httpx.HTTPError, ValueError):
        result = {}
    if refusal is not None:
        record_gate_refusal(refusal)
        raise comfyui_provider.ImageGateRefused(refusal)
    prompt_id = result.get("prompt_id")
    return JSONResponse(
        status_code=202,
        content={"status": "queued", "prompt_id": prompt_id},
    )


@router.post("/workflows/{name:path}/launch", include_in_schema=False)
async def comfyui_invalid_workflow_launch(name: str) -> JSONResponse:
    """Reject malformed workflow names that include path separators."""
    return _workflow_not_found_response()


# ---------------------------------------------------------------------------
# GET /preview — proxy the latest output image from ComfyUI history
# ---------------------------------------------------------------------------


def _latest_output_image(history: dict[str, Any]) -> dict[str, str] | None:
    """Find the newest output image entry in ComfyUI's /history response.

    ComfyUI history is a dict keyed by prompt_id; each entry has an
    ``outputs`` dict with node-keyed image lists. We pick the entry with
    the highest timestamp (or first if none) and return the first image.
    """
    if not history:
        return None

    # Sort by timestamp if available, newest first
    def _ts(entry):
        return entry.get("timestamp", 0.0)

    candidates = sorted(history.values(), key=_ts, reverse=True)
    for entry in candidates:
        outputs = entry.get("outputs", {})
        for node_out in outputs.values():
            images = node_out.get("images", [])
            if images:
                return images[0]
    return None


@router.get("/preview")
async def comfyui_preview() -> Any:
    """Proxy the latest output image from the ComfyUI history.

    Queries /history for the most recent completed prompt, fetches the
    newest output image via /view?filename=...&type=output, and streams
    the bytes back with the correct content-type. Returns 404 when there
    is no output yet.
    """
    from fastapi.responses import Response

    history = await _fetch_json("/history")
    if not isinstance(history, dict):
        return JSONResponse(status_code=404, content={"error": {"code": "comfyui.no_output"}})
    img = _latest_output_image(history)
    if img is None:
        return JSONResponse(status_code=404, content={"error": {"code": "comfyui.no_output"}})
    filename = img.get("filename", "")
    subfolder = img.get("subfolder", "")
    img_type = img.get("type", "output")
    params = f"filename={filename}&type={img_type}"
    if subfolder:
        params += f"&subfolder={subfolder}"
    base = _comfyui_base_url()
    try:
        resp = await _get_client().get(f"{base}/view?{params}")
        if resp.status_code != 200:
            return JSONResponse(
                status_code=404, content={"error": {"code": "comfyui.image_fetch_failed"}}
            )
        content_type = resp.headers.get("content-type", "image/png")
        return Response(content=resp.content, media_type=content_type)
    except (httpx.HTTPError, OSError):
        return JSONResponse(
            status_code=404, content={"error": {"code": "comfyui.image_fetch_failed"}}
        )


__all__ = ["aclose_client", "router"]
