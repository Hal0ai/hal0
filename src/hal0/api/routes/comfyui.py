"""ComfyUI "generation engine" status aggregator + control routes.

The dashboard models ComfyUI as ONE containerized generation engine, not a list
of per-model slots (a single run loads many cooperating models at once, and it
is mutually exclusive with the LLM stack on the single iGPU). The Image-Gen tab
renders that engine pane from ``GET /api/comfyui/status``, which folds together:

  - **docker** container state (``comfyui`` running / exited / absent),
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
transition. The API no longer shells out — the ``/opt/comfyui`` control scripts
stay on disk for manual ops only. ``POST /api/comfyui/pin`` toggles the
arbiter's manual pin (blocks idle-restore).
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
from pydantic import BaseModel

import hal0.comfyui.fetch as _fetch_module
from hal0.api.routes.power import _probe_power
from hal0.comfyui.selection import auto_selections, variant_for
from hal0.hardware import gpu_view

router = APIRouter()
log = logging.getLogger(__name__)

# iGPU memory ceilings on the Strix Halo target (GB). The host addresses ~80 GB
# of the 96 GB unified pool from the GPU, with zero swap — so memory pressure is
# the real constraint, surfaced when GTT crosses half the ceiling.
_GTT_CEIL_GB = 80
_RAM_CEIL_GB = 96
_PRESSURE_GB = 50

_HERMES_UNIT = "hal0-agent@hermes.service"
_IMG_UNIT = "hal0-slot@img.service"

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


def _comfyui_container() -> str:
    return os.environ.get("COMFYUI_CONTAINER", "comfyui")


def _build_client(timeout: httpx.Timeout) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, limits=_POOL_LIMITS)


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


async def _container_state(name: str) -> str:
    """Return the generation container state: 'running', 'exited', or 'absent'.

    Podman-first (#710): post-Phase-D the img slot runs as the
    ``hal0-slot@img`` systemd unit — probe it the way the slots page
    does. ``docker inspect`` survives only as the legacy fallback for
    pre-migration installs. 'absent' also covers "docker not installed"
    and any inspect failure — the pane treats all non-running states as
    stopped.
    """
    if await _systemd_active(_IMG_UNIT):
        return "running"
    docker = shutil.which("docker")
    if not docker:
        return "absent"
    try:
        proc = await asyncio.create_subprocess_exec(
            docker,
            "inspect",
            "-f",
            "{{.State.Status}}",
            name,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=2.0)
    except (TimeoutError, OSError):
        return "absent"
    if proc.returncode != 0:
        return "absent"
    state = out.decode("utf-8", "replace").strip()
    return state or "absent"


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


# Model categories surfaced in the pane's "models on share" card, mapped to the
# share subdirs. ``diffusion`` folds the standalone diffusion/video model dirs.
_INVENTORY_DIRS: dict[str, tuple[str, ...]] = {
    "checkpoints": ("checkpoints",),
    "diffusion": ("diffusion_models", "unet"),
    "loras": ("loras",),
    "vae": ("vae",),
    "controlnet": ("controlnet",),
    "upscale": ("upscale_models",),
    "text_encoders": ("text_encoders", "clip"),
}
# Model weight extensions worth counting; ignore configs, indexes, dotfiles.
_MODEL_EXTS = (".safetensors", ".ckpt", ".pt", ".pth", ".gguf", ".bin", ".sft")


def _comfyui_models_dir() -> str:
    return os.environ.get("COMFYUI_MODELS_DIR", "/mnt/ai-models/comfyui/models")


def _count_models(base: str, subdirs: tuple[str, ...]) -> int:
    total = 0
    for sub in subdirs:
        path = os.path.join(base, sub)
        try:
            with os.scandir(path) as it:
                total += sum(1 for e in it if e.is_file() and e.name.lower().endswith(_MODEL_EXTS))
        except OSError:
            continue
    return total


def _model_inventory() -> dict[str, int] | None:
    """Count weight files per category on the model share — VERIFIED, never faked.

    Returns ``None`` when the share root is absent (dev box / fresh install) so
    the pane hides the counts rather than rendering zeros it can't stand behind.
    """
    base = _comfyui_models_dir()
    if not os.path.isdir(base):
        return None
    return {cat: _count_models(base, subdirs) for cat, subdirs in _INVENTORY_DIRS.items()}


def _engine_state(container: str, reachable: bool, running_jobs: int) -> str:
    # A reachable ComfyUI IS the engine truth — the docker name probe can't
    # see the podman hal0-slot-img container (post-D9 it reports "absent"
    # forever), and the resident container must not render as "stopped".
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

    None means "no arbiter / arbiter broken" — callers fall back to the legacy
    docker container probe. Post-migration (D9 removed the docker container)
    the legacy probe lies, so the arbiter wins whenever it answers.
    """
    if arbiter is None:
        return None
    try:
        return _ARBITER_TO_API_MODE.get(arbiter.status().get("mode"))
    except Exception:
        return None


@router.get("/status")
async def comfyui_status(request: Request) -> dict[str, Any]:
    """Aggregate docker + systemd + ComfyUI HTTP into one engine-status object."""
    container_name = _comfyui_container()
    container, hermes, stats, queue = await asyncio.gather(
        _container_state(container_name),
        _systemd_active(_HERMES_UNIT),
        _fetch_json("/system_stats"),
        _fetch_json("/queue"),
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
    # Mode: arbiter is the source of truth (img → generation, llm → inference);
    # the docker-derived mode is only the legacy fallback for arbiter-less apps.
    arb_mode = (
        _ARBITER_TO_API_MODE.get(arbiter_block.get("mode"))
        if isinstance(arbiter_block, dict)
        else None
    )
    mode = arb_mode or ("generation" if container == "running" else "inference")
    return {
        "mode": mode,
        "reachable": reachable,
        "engine": engine,
        "container": {"name": container_name, "state": container},
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
        "inventory": _model_inventory(),
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


@router.post("/switchover")
async def comfyui_switchover(request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
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
    try:
        body = await request.json()
    except ValueError:
        body = None
    mode = body.get("mode") if isinstance(body, dict) else None
    if mode not in _MODES:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "comfyui.invalid_mode",
                    "message": "body must be {'mode': 'generation' | 'inference'}",
                }
            },
        )
    # One switch at a time — racing systemctl/docker pairs is never right. Checked
    # before the noop probe so a mid-flight flip is reported as in-progress, not
    # as "already there" based on a half-transitioned snapshot.
    if _switch["active"]:
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": "comfyui.switch_in_progress",
                    "message": f"a switch to {_switch['target']} is already running",
                }
            },
        )
    # Idempotency: the arbiter is the source of truth for the current mode
    # (D7) — the docker-era container probe is ONLY the legacy fallback for
    # arbiter-less apps. Post-migration the docker container is gone, so the
    # fallback would report "already in inference" forever (= restore_llm
    # never invokable, pinned img mode would be a permanent lockout) — hence
    # the arbiter wins whenever it answers.
    arbiter = _get_arbiter(request)
    current = _arbiter_api_mode(arbiter)
    if current is not None:
        already_there = current == mode
    else:
        container = await _container_state(_comfyui_container())
        already_there = container == "running" if mode == "generation" else container != "running"
    if already_there:
        return JSONResponse(status_code=200, content={"status": "noop", "mode": mode})
    # Mid-render guard: switching to inference frees ComfyUI's models from GPU
    # memory, killing any running/pending renders (the container itself stays
    # up). Refuse unless the caller forces it — the dashboard confirm dialog
    # states the blast radius and passes force on user confirm.
    force = bool(body.get("force")) if isinstance(body, dict) else False
    if mode == "inference" and not force:
        counts = _queue_counts(await _fetch_json("/queue"))
        busy = counts["running"] + counts["pending"]
        if busy:
            return JSONResponse(
                status_code=409,
                content={
                    "error": {
                        "code": "comfyui.busy",
                        "message": (
                            f"{busy} render job(s) running or queued would be dropped; "
                            "retry with {'force': true} to switch anyway."
                        ),
                        "queue": counts,
                    }
                },
            )
    if arbiter is None:
        return _arbiter_unavailable()
    pin = bool(body.get("pin")) if isinstance(body, dict) else False
    # Dispatch in the background and answer 202 immediately — the drain/reload
    # takes seconds to tens of seconds (slot unloads, container boot) and the
    # pane's /status poll tracks the transition via the switchover block.
    _switch.update(active=True, target=mode, error=None)
    background_tasks.add_task(_run_switch, arbiter, mode, pin=pin, force=force)
    return JSONResponse(status_code=202, content={"status": "switching", "mode": mode})


@router.post("/pin")
async def comfyui_pin(request: Request) -> JSONResponse:
    """Toggle the arbiter's manual pin (holds image mode against idle-restore).

    Body: ``{"pinned": bool}``. Pinning disables idle auto-restore while image
    mode is active.
    """
    try:
        body = await request.json()
    except ValueError:
        body = None
    pinned = body.get("pinned") if isinstance(body, dict) else None
    if not isinstance(pinned, bool):
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "comfyui.invalid_pin",
                    "message": "body must be {'pinned': true | false}",
                }
            },
        )
    arbiter = _get_arbiter(request)
    if arbiter is None:
        return _arbiter_unavailable()
    arbiter.set_pin(pinned)
    return JSONResponse(status_code=200, content={"pinned": pinned})


# ---------------------------------------------------------------------------
# POST /models/fetch — deferred model pull trigger (Task 3.5)
# ---------------------------------------------------------------------------


class _SelectionItem(BaseModel):
    capability: str
    family: str


class _FetchBody(BaseModel):
    auto: bool | None = None
    selections: list[_SelectionItem] | None = None


@router.post("/models/fetch", status_code=202)
async def comfyui_models_fetch(body: _FetchBody) -> JSONResponse:
    """Trigger deferred model pulls for ComfyUI capabilities.

    Body (one of):
      {"auto": true}
          Fetches the default variant for every capability (5 total).
      {"selections": [{"capability": "txt2img", "family": "sdxl"}, ...]}
          Fetches the named variant(s) explicitly.

    Returns 202 with {"jobs": [job_id, ...]} immediately; each job runs in the
    background via fetch.fetch_model (subprocess, non-blocking).

    This is the DEFERRED post-install pull path — install runs with --no-pull
    and this endpoint is called by the dashboard after setup completes.
    """
    if body.auto is None and not body.selections:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "comfyui.fetch.invalid_body",
                    "message": "body must be {'auto': true} or {'selections': [...]}",
                }
            },
        )

    if body.auto:
        variants = auto_selections()
    else:
        # Resolve explicit selections; surface unknown cap/family as 422.
        variants = []
        for item in body.selections:  # type: ignore[union-attr]
            try:
                v = variant_for(item.capability, item.family)
            except KeyError as exc:
                return JSONResponse(
                    status_code=422,
                    content={
                        "error": {
                            "code": "comfyui.fetch.unknown_variant",
                            "message": str(exc),
                        }
                    },
                )
            variants.append(v)

    job_ids = [_fetch_module.fetch_model(v) for v in variants]
    return JSONResponse(status_code=202, content={"jobs": job_ids})


def _comfyui_workflows_dir() -> str:
    """Primary workflow directory — env override for tests; default is the bind-mount path."""
    return os.environ.get("COMFYUI_WORKFLOWS_DIR", "/mnt/ai-models/comfyui/workflows")


def _comfyui_data_dir() -> str:
    """Root of the ComfyUI data directory (for fallback user/default/workflows path)."""
    return os.environ.get("COMFYUI_DATA_DIR", "/mnt/ai-models/comfyui")


def _find_workflow(name: str) -> str | None:
    """Locate <name>.json, trying primary then user/default fallback. None if absent."""
    if not _WORKFLOW_NAME_RE.fullmatch(name) or ".." in name:
        return None
    primary = os.path.join(_comfyui_workflows_dir(), f"{name}.json")
    if os.path.isfile(primary):
        return primary
    fallback = os.path.join(_comfyui_data_dir(), "user", "default", "workflows", f"{name}.json")
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
async def comfyui_logs(tail: int = 60) -> JSONResponse:
    """Return the last N lines of the ComfyUI slot's journal output.

    Post-D9 the img slot runs as the podman ``hal0-slot-img`` container
    under the ``hal0-slot@img`` systemd unit with ``StandardOutput=journal``
    (the container itself uses the ``none`` log driver, so ``podman logs``
    yields nothing). We therefore read the journal — matching the regular
    slot logs endpoint — rather than ``podman/docker logs``.

    Returns ``{"lines": []}`` when ``journalctl`` is absent or the slot
    has no journal entries yet — never a 500.
    """
    if shutil.which("journalctl") is None:
        return JSONResponse(status_code=200, content={"lines": []})
    n = max(1, min(int(tail or 60), 5000))
    try:
        proc = await asyncio.create_subprocess_exec(
            "journalctl",
            "-u",
            _IMG_UNIT,
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
