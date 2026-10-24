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
``POST /api/comfyui/pin`` toggles the arbiter's manual pin (blocks idle-restore).

The image-model routes (ADR-0037) sit beside the engine ones: ``/models`` is the
catalog with each model's install state, ``/models/{id}/install`` and
``DELETE /models/{id}`` change what is on disk, ``/jobs`` tracks the installs, and
``/files`` lists every weight file under the models tree. An install is a
:class:`~hal0.registry.pull.PullJob` kept on ``app.state.image_install_jobs``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import shutil
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from hal0.api._redact import redact_log_line
from hal0.api.middleware.error_codes import Conflict, NotFound, UnprocessableEntity
from hal0.api.routes.power import _probe_power
from hal0.comfyui import install as comfyui_install
from hal0.comfyui import inventory
from hal0.comfyui import paths as comfyui_paths
from hal0.comfyui.catalog import (
    CATALOG,
    ImageModel,
    get_model,
    install_state,
    load_template,
    models_using,
    recommended_model_id,
)
from hal0.config.loader import load_hardware_info
from hal0.hardware import gpu_view
from hal0.http_client import async_client
from hal0.install.agent_model import _pool_gb
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

# Serialises the install route's check-then-start with a removal that is running in a
# worker thread: an install must not start (and skip a file as present) while the files it
# would skip are being deleted.
_TREE_LOCK = asyncio.Lock()

# Switchover target modes. "generation" hands the iGPU to ComfyUI
# (arbiter.ensure_img); "inference" hands it back to the LLM stack
# (arbiter.restore_llm).
_MODES = ("generation", "inference")
_WORKFLOW_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# Short connect so a dead engine surfaces fast; the read budget is modest because
# /system_stats and /queue are cheap snapshots.
_DEFAULT_TIMEOUT = httpx.Timeout(connect=1.5, read=4.0, write=2.0, pool=2.0)
_POOL_LIMITS = httpx.Limits(max_connections=4, max_keepalive_connections=2)

_client: httpx.AsyncClient | None = None

# Keep strong refs to fire-and-forget tasks so they aren't GC'd mid-flight (RUF006).
_BG_TASKS: set[asyncio.Task[Any]] = set()

# In-flight switchover tracker. Module-global on purpose: there is exactly one
# iGPU, so there is exactly one switch — /status surfaces it so the pane's poll
# can render "switching…" and any error from the last attempt.
_SWITCH_IDLE: dict[str, Any] = {"active": False, "target": None, "error": None}
_switch: dict[str, Any] = dict(_SWITCH_IDLE)


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
    """Drop the shared client + switch tracker. For test isolation only."""
    global _client
    _client = None
    _switch.clear()
    _switch.update(_SWITCH_IDLE)


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


async def _list_files_or_none() -> list[dict[str, Any]] | None:
    """The models-tree listing for ``/status``; ``None`` when the scan fails.

    The pane polls ``/status`` and a failed scan must cost it the inventory counts, not
    the whole answer. ``None``, not an empty listing, so the counts are hidden rather than
    shown as zeros nobody can vouch for.
    """
    try:
        return await asyncio.to_thread(inventory.list_files)
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


def _parse_memory(stats: dict[str, Any] | None) -> dict[str, Any] | None:
    """Fold ComfyUI's /system_stats into the pane's GTT + RAM gauges.

    GTT comes from the first reported device's vram_total/vram_free; RAM from the
    system block. Any missing field becomes ``None`` rather than a guessed value.
    """
    if not stats:
        return None
    gtt_used = gtt_ceil = None
    devices = stats.get("devices")
    if isinstance(devices, list) and devices and isinstance(devices[0], dict):
        dev = devices[0]
        total = dev.get("vram_total")
        free = dev.get("vram_free")
        gtt_ceil = _bytes_to_gb(total) or _GTT_CEIL_GB
        if isinstance(total, (int, float)) and isinstance(free, (int, float)):
            gtt_used = _bytes_to_gb(total - free)
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


# Arbiter GPU mode → the API's dashboard mode vocabulary.
_ARBITER_TO_API_MODE = {"img": "generation", "llm": "inference"}


def _arbiter_api_mode(arbiter: Any) -> str | None:
    """Arbiter-truth current mode ("generation"|"inference"), or None.

    None means "no arbiter / arbiter broken" — the mode is then unknown (the
    resident img unit stays up in inference mode, so it cannot stand in).
    """
    if arbiter is None:
        return None
    try:
        return _ARBITER_TO_API_MODE.get(arbiter.status().get("mode"))
    except Exception:
        return None


@router.get("/status")
async def comfyui_status(request: Request) -> dict[str, Any]:
    """Aggregate systemd + ComfyUI HTTP into one engine-status object."""
    img_unit, img_container = await _img_artefacts(request)
    container, hermes, stats, queue, files = await asyncio.gather(
        _container_state(img_unit),
        _systemd_active(_HERMES_UNIT),
        _fetch_json("/system_stats"),
        _fetch_json("/queue"),
        _list_files_or_none(),
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
    # Mode: the arbiter is the only source of truth (img → generation, llm →
    # inference). The resident img unit stays up in inference mode too, so its
    # state is NOT mode evidence: with no arbiter answer the mode is ``None``.
    mode = (
        _ARBITER_TO_API_MODE.get(arbiter_block.get("mode"))
        if isinstance(arbiter_block, dict)
        else None
    )
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
        "it_s": None,
        "eta": None,
        "step": None,
        "inference": {"hermes": hermes},
        # Verified counts of the weight files on disk, never faked; None without a models
        # tree or when the scan failed.
        "inventory": None if files is None else inventory.status_counts(files),
        "switchover": dict(_switch),
        "arbiter": arbiter_block,
    }


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


def _arbiter_unavailable() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "error": {
                "code": "comfyui.arbiter_unavailable",
                "message": "slot manager / GPU arbiter is not wired on this app",
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


@router.post("/switchover")
async def comfyui_switchover(
    request: Request, background_tasks: BackgroundTasks, body: SwitchoverBody | None = None
) -> JSONResponse:
    """Flip the iGPU between LLM inference and ComfyUI generation.

    Body: ``{"mode": "generation" | "inference", "force": bool, "pin": bool}``
    (``pin`` only matters for generation: hold image mode against idle-restore).
    Refuses while a switch is in flight (409), no-ops when already in the
    target mode (200), and refuses to drop a busy render queue without
    ``force`` (409). Otherwise answers 202 and drives the GpuArbiter in the
    background — track completion via the ``switchover`` block on
    ``GET /status``.

    The endpoint is always available when the SlotManager/GpuArbiter is wired:
    ComfyUI prompt submission can call it as the implicit handoff before
    enqueueing a render.
    """
    if body is None:
        body = SwitchoverBody()
    mode = body.mode
    if mode not in _MODES:
        raise UnprocessableEntity(
            "body must be {'mode': 'generation' | 'inference'}",
            code="comfyui.invalid_mode",
        )
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
    arbiter = _get_arbiter(request)
    current = _arbiter_api_mode(arbiter)
    if current is None:
        return _arbiter_unavailable()
    if current == mode:
        return JSONResponse(status_code=200, content={"status": "noop", "mode": mode})
    # Mid-render guard: switching to inference frees ComfyUI's models from GPU
    # memory, killing any running/pending renders (the container itself stays
    # up). Refuse unless the caller forces it — the dashboard confirm dialog
    # states the blast radius and passes force on user confirm.
    force = body.force
    if mode == "inference" and not force:
        counts = _queue_counts(await _fetch_json("/queue"))
        busy = counts["running"] + counts["pending"]
        if busy:
            raise Conflict(
                f"{busy} render job(s) running or queued would be dropped; "
                "retry with {'force': true} to switch anyway.",
                code="comfyui.busy",
                details={"queue": counts},
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
    """Toggle the arbiter's manual pin (holds image mode against idle-restore).

    Body: ``{"pinned": bool}``. Pinning disables idle auto-restore while image
    mode is active.
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
    arbiter.set_pin(pinned)
    return JSONResponse(status_code=200, content={"pinned": pinned})


# ---------------------------------------------------------------------------
# Image models: catalog, installs, jobs, files (ADR-0037)
# ---------------------------------------------------------------------------


def _recommended_id() -> str:
    """The catalog's starter pick for this box's memory pool.

    Read per request: it is one small JSON file. A pool that cannot be read, or that
    reads as zero because the box was never probed, is passed on as unknown (``None``).
    """
    try:
        pool_gb: float | None = _pool_gb(load_hardware_info())
    except Exception:  # an unreadable hardware.json must not take the catalog down
        pool_gb = None
    return recommended_model_id(pool_gb if pool_gb else None)


def _template_defaults(model: ImageModel) -> dict[str, Any]:
    """The default sampler parameters the model's workflow template declares, if any."""
    meta = load_template(model.template).get("_meta")
    defaults = meta.get("defaults") if isinstance(meta, dict) else None
    return dict(defaults) if isinstance(defaults, dict) else {}


def _model_row(model: ImageModel, *, recommended: str, job: PullJob | None) -> dict[str, Any]:
    """One catalog model with its install state, as ``GET /models`` lists it.

    ``missing`` names each absent weight file relative to the models root, like the
    paths ``DELETE /models/{id}`` reports. ``job`` is the model's latest install, if any.
    """
    state = install_state(model)
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
        "approx_gb": model.approx_gb,
        "missing_gb": state.missing_gb,
        "state": state.state,
        "missing": [f"{c.kind.subdir}/{c.relpath}" for c in state.missing],
        "workflow": model.workflow_filename,
        "job": job.as_dict() if job is not None else None,
    }


def _install_jobs(request: Request) -> dict[str, PullJob]:
    jobs: dict[str, PullJob] = request.app.state.image_install_jobs
    return jobs


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

    Returns ``{"models": [...]}``, in catalog order. Exactly one row is
    ``recommended``: the starter pick for this box's memory pool.
    """
    jobs = _install_jobs(request)
    recommended = _recommended_id()
    return {
        "models": [
            _model_row(model, recommended=recommended, job=jobs.get(model.id))
            for model in CATALOG.values()
        ]
    }


def _schedule_install(request: Request, model: ImageModel, job: PullJob) -> None:
    """Run the install as a detached task tracked for shutdown, never a BackgroundTask.

    Same reasoning as ``hal0.registry.pull_jobs.schedule_pull_task`` (#1225): a
    background task would hold the request's connection open for the whole download
    and block a graceful restart. ``app.state.image_install_tasks`` is what the lifespan
    shutdown cancels.
    """
    task = asyncio.create_task(
        comfyui_install.run_install(job, model, hf_token=hf_token_from_env())
    )
    tasks: dict[str, asyncio.Task[None]] = request.app.state.image_install_tasks
    tasks[model.id] = task

    def _untrack(finished: asyncio.Task[None], _model_id: str = model.id) -> None:
        if tasks.get(_model_id) is finished:
            tasks.pop(_model_id, None)

    task.add_done_callback(_untrack)


@router.post("/models/{model_id}/install", status_code=202)
async def comfyui_model_install(model_id: str, request: Request) -> JSONResponse:
    """Start downloading what the model is missing; 202 with the new install job.

    Refused before any job exists with 404 ``comfyui.model_unknown``, 409
    ``comfyui.install_running`` (its job rides in ``error.details.job``), 409
    ``comfyui.model_installed``, 409 ``comfyui.staging_cross_device`` (download staging
    and models tree on two filesystems), 409 ``comfyui.models_dir_not_writable`` (a
    destination directory the hal0 user cannot write; ``error.details.path``) and 507
    ``comfyui.insufficient_disk``. The job then runs in the background; follow it on
    ``GET /jobs``. A finished job stays listed until the next install of the model
    replaces it.
    """
    model = _require_model(model_id)
    jobs = _install_jobs(request)
    async with _TREE_LOCK:  # waits only while a removal is running
        existing = jobs.get(model_id)
        if existing is not None and existing.state in _ACTIVE_JOB_STATES:
            raise _install_running(model, existing)
        if install_state(model).state == "complete":
            raise Conflict(
                f"{model.label} is already installed",
                code="comfyui.model_installed",
                details={"model_id": model_id},
            )
        # Past the complete check something is always missing, so every guard applies.
        comfyui_install.require_one_filesystem()
        comfyui_install.require_writable_destinations(model)
        comfyui_install.preflight_disk(model)
        job = make_job(model_id)
        jobs[model_id] = job
        _schedule_install(request, model, job)
    return JSONResponse(status_code=202, content={"job": job.as_dict()})


@router.delete("/models/{model_id}")
async def comfyui_model_remove(model_id: str, request: Request) -> dict[str, list[str]]:
    """Delete the model's weight files and workflow; ``{"removed": [...]}`` lists them.

    A file another complete model loads stays. 409 ``comfyui.install_running`` while
    the model's install is queued or running, or while an install of a model that shares
    a weight file with it is (``error.details.busy_model_id`` names that model). 409
    ``comfyui.remove_failed`` when a file cannot be deleted (a read-only directory):
    ``error.details`` lists what was removed before it and the path that failed.
    """
    model = _require_model(model_id)
    async with _TREE_LOCK:
        job = _blocking_install(model, _install_jobs(request))
        if job is not None:
            raise _install_running(CATALOG[job.model_id], job, removing=model)
        # The delete runs off the event loop (hundreds of unlinks on a slow store); the
        # lock keeps an install from starting under it.
        removed = await asyncio.to_thread(comfyui_install.remove_model, model)
    return {"removed": removed}


@router.get("/jobs")
async def comfyui_jobs(request: Request) -> dict[str, Any]:
    """The latest install job of every model that has had one, finished or not."""
    return {"jobs": [job.as_dict() for job in _install_jobs(request).values()]}


@router.post("/jobs/{model_id}/cancel", status_code=202)
async def comfyui_job_cancel(model_id: str, request: Request) -> JSONResponse:
    """Ask the model's install to stop at the next chunk; 202 with the job.

    Files that already landed stay, so a later install carries on from there. A job
    that already finished is returned unchanged. 404 ``comfyui.job_not_found`` when
    the model has no job.
    """
    job = _install_jobs(request).get(model_id)
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
    """Every weight file under the models tree, with the catalog models that own it."""
    files = await asyncio.to_thread(inventory.list_files)
    return {"root": str(comfyui_paths.models_root()), "files": files}


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
    404 when the workflow file does not exist.
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
    try:
        resp = await _get_client().post(
            f"{base}/prompt",
            json={"prompt": workflow_data},
        )
        result = resp.json() if resp.status_code == 200 else {}
    except (httpx.HTTPError, ValueError):
        result = {}
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
