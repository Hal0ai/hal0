"""Read-only ComfyUI "generation engine" status aggregator (+ gated switchover).

The dashboard models ComfyUI as ONE containerized generation engine, not a list
of per-model slots (a single run loads many cooperating models at once, and it
is mutually exclusive with the LLM stack on the single iGPU). The Image-Gen tab
renders that engine pane from ``GET /api/comfyui/status``, which folds together:

  - **docker** container state (``comfyui`` running / exited / absent),
  - **systemd** state of the LLM stack (``hal0-lemonade`` + ``hal0-agent@hermes``)
    so the pane can show which mode currently owns the GPU, and
  - **ComfyUI's own HTTP API** (``/system_stats`` for GTT/RAM, ``/queue`` for the
    running + pending job counts).

Every source degrades to a safe default — the pane polls this every few seconds
and a dead container must surface as "stopped", never a 500.

The switchover *write* path (``POST /api/comfyui/switchover``) runs the
root-owned script pairs in ``/opt/comfyui`` (``stop-inference.sh`` →
``comfy-up.sh`` for generation, ``comfy-down.sh`` → ``start-inference.sh`` for
inference) in the background behind a 202; the ``switchover`` block on /status
tracks the transition. Privilege-aware: as root (hal0-api on CT105 runs
``User=root``) the scripts exec directly, otherwise via ``sudo -n`` against the
narrow ``packaging/sudoers/hal0-comfyui`` grant. The whole path stays
feature-gated behind ``HAL0_COMFYUI_SWITCHOVER_ENABLED`` (501 when off).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse

router = APIRouter()

# iGPU memory ceilings on the Strix Halo target (GB). The host addresses ~80 GB
# of the 96 GB unified pool from the GPU, with zero swap — so memory pressure is
# the real constraint, surfaced when GTT crosses half the ceiling.
_GTT_CEIL_GB = 80
_RAM_CEIL_GB = 96
_PRESSURE_GB = 50

_LEMONADE_UNIT = "hal0-lemonade.service"
_HERMES_UNIT = "hal0-agent@hermes.service"

# Switchover script pairs, run in order from the scripts dir on the runtime
# host. ON hands the iGPU to ComfyUI; OFF hands it back to the LLM stack.
_SWITCH_PAIRS: dict[str, tuple[str, ...]] = {
    "generation": ("stop-inference.sh", "comfy-up.sh"),
    "inference": ("comfy-down.sh", "start-inference.sh"),
}

# Short connect so a dead engine surfaces fast; the read budget is modest because
# /system_stats and /queue are cheap snapshots.
_DEFAULT_TIMEOUT = httpx.Timeout(connect=1.5, read=4.0, write=2.0, pool=2.0)
_POOL_LIMITS = httpx.Limits(max_connections=4, max_keepalive_connections=2)

_client: httpx.AsyncClient | None = None

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
    """Return the docker container state: 'running', 'exited', or 'absent'.

    'absent' also covers "docker not installed" and any inspect failure — the
    pane treats all non-running states as stopped.
    """
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
    if container != "running":
        return "stopped"
    if not reachable:
        return "starting"  # container up but ComfyUI hasn't bound the port yet
    return "generating" if running_jobs > 0 else "running"


@router.get("/status")
async def comfyui_status(request: Request) -> dict[str, Any]:
    """Aggregate docker + systemd + ComfyUI HTTP into one engine-status object."""
    container_name = _comfyui_container()
    container, lemonade, hermes, stats, queue = await asyncio.gather(
        _container_state(container_name),
        _systemd_active(_LEMONADE_UNIT),
        _systemd_active(_HERMES_UNIT),
        _fetch_json("/system_stats"),
        _fetch_json("/queue"),
    )
    reachable = stats is not None
    counts = _queue_counts(queue)
    engine = _engine_state(container, reachable, counts["running"])
    return {
        "mode": "generation" if container == "running" else "inference",
        "reachable": reachable,
        "engine": engine,
        "container": {"name": container_name, "state": container},
        "endpoint": ":8188" if container == "running" else None,
        "memory": _parse_memory(stats),
        "queue": counts,
        "inference": {"lemonade": lemonade, "hermes": hermes},
        "inventory": _model_inventory(),
        "switchover": dict(_switch),
    }


def _scripts_dir() -> str:
    return os.environ.get("COMFYUI_SCRIPTS_DIR", "/opt/comfyui")


def _script_timeout() -> float:
    # comfy-up.sh on a FRESH container create waits for HTTP and pip-installs
    # custom-node deps — minutes, not seconds. The resume path is fast.
    try:
        return float(os.environ.get("COMFYUI_SCRIPT_TIMEOUT", "600"))
    except ValueError:
        return 600.0


def _script_argv(name: str) -> list[str]:
    """Argv for one switchover script, privilege-aware.

    Root (CT105 today: hal0-api runs as ``User=root``) execs the script
    directly. An unprivileged hal0-api goes through ``sudo -n`` against the
    narrow ``/etc/sudoers.d/hal0-comfyui`` grant (absolute paths only); ``-n``
    so a missing grant fails immediately instead of hanging on a password
    prompt.
    """
    path = os.path.join(_scripts_dir(), name)
    return [path] if os.geteuid() == 0 else ["sudo", "-n", path]


async def _run_script(name: str) -> None:
    """Execute one switchover script to completion; raises on failure."""
    argv = _script_argv(name)
    timeout = _script_timeout()
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        raise RuntimeError(f"{name} timed out after {timeout:.0f}s") from None
    if proc.returncode != 0:
        tail = out.decode("utf-8", "replace").strip().splitlines()[-5:]
        raise RuntimeError(f"{name} exited {proc.returncode}: {' | '.join(tail)}")


async def _run_switch(mode: str) -> None:
    """Run the script pair for ``mode``; record failure for /status to surface."""
    try:
        for name in _SWITCH_PAIRS[mode]:
            await _run_script(name)
    except Exception as exc:  # any script failure must land in /status, never raise
        _switch["error"] = f"{mode}: {exc}"
    finally:
        _switch["active"] = False
        _switch["target"] = None


@router.post("/switchover")
async def comfyui_switchover(request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
    """Flip the iGPU between LLM inference and ComfyUI generation.

    Body: ``{"mode": "generation" | "inference", "force": bool}``. Refuses while
    a switch is in flight (409), no-ops when already in the target mode (200),
    and refuses to drop a busy render queue without ``force`` (409). Otherwise
    answers 202 and runs the script pair in the background — track completion
    via the ``switchover`` block on ``GET /status``.

    Stays gated behind ``HAL0_COMFYUI_SWITCHOVER_ENABLED`` because the scripts
    take the messaging bots + memory extraction offline (an operator decision
    per host), not because the path is unwired.
    """
    if os.environ.get("HAL0_COMFYUI_SWITCHOVER_ENABLED", "0") != "1":
        return JSONResponse(
            status_code=501,
            content={
                "error": {
                    "code": "comfyui.switchover_disabled",
                    "message": (
                        "ComfyUI switchover is disabled on this host. It stops "
                        "the LLM stack (bots + memory extraction go dark) while "
                        "generation holds the iGPU; set "
                        "HAL0_COMFYUI_SWITCHOVER_ENABLED=1 on hal0-api to "
                        "enable it."
                    ),
                }
            },
        )
    try:
        body = await request.json()
    except ValueError:
        body = None
    mode = body.get("mode") if isinstance(body, dict) else None
    if mode not in _SWITCH_PAIRS:
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
    # Idempotency: derive the current mode the same way /status does (container
    # running ⇒ generation). Target inference additionally requires lemonade to
    # actually be up — if both stacks are down, the switch runs as a repair.
    container, lemonade = await asyncio.gather(
        _container_state(_comfyui_container()),
        _systemd_active(_LEMONADE_UNIT),
    )
    already_there = (
        container == "running" if mode == "generation" else container != "running" and lemonade
    )
    if already_there:
        return JSONResponse(status_code=200, content={"status": "noop", "mode": mode})
    # Mid-render guard: switching to inference stops the container, dropping any
    # running/pending renders. Refuse unless the caller forces it — the dashboard
    # confirm dialog states the blast radius and passes force on user confirm.
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
    # Dispatch in the background and answer 202 immediately — the pair takes
    # seconds to tens of seconds (service stop/start, container boot) and the
    # pane's /status poll tracks the transition via the switchover block.
    _switch.update(active=True, target=mode, error=None)
    background_tasks.add_task(_run_switch, mode)
    return JSONResponse(
        status_code=202,
        content={"status": "switching", "mode": mode, "scripts": list(_SWITCH_PAIRS[mode])},
    )


__all__ = ["aclose_client", "router"]
