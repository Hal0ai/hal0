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

The switchover *write* path (``POST /api/comfyui/switchover``) runs root-owned
scripts (``stop-inference.sh`` / ``comfy-up.sh`` …) via systemctl + docker on the
shared runtime. hal0-api is unprivileged, so that path needs a narrowly-scoped
sudoers rule / root helper wired in a separate, explicitly-confirmed step. Until
then it is feature-gated behind ``HAL0_COMFYUI_SWITCHOVER_ENABLED`` and refuses.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
from typing import Any

import httpx
from fastapi import APIRouter, Request
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

# Short connect so a dead engine surfaces fast; the read budget is modest because
# /system_stats and /queue are cheap snapshots.
_DEFAULT_TIMEOUT = httpx.Timeout(connect=1.5, read=4.0, write=2.0, pool=2.0)
_POOL_LIMITS = httpx.Limits(max_connections=4, max_keepalive_connections=2)

_client: httpx.AsyncClient | None = None


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
    """Drop the shared client. For test isolation only."""
    global _client
    _client = None


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
    }


@router.post("/switchover")
async def comfyui_switchover(request: Request) -> JSONResponse:
    """Flip the iGPU between LLM inference and ComfyUI generation.

    Gated: the underlying scripts need root (systemctl + docker) and take the
    messaging bots + memory extraction offline, so the privileged path is wired
    only behind ``HAL0_COMFYUI_SWITCHOVER_ENABLED`` in a separate confirmed step.
    """
    if os.environ.get("HAL0_COMFYUI_SWITCHOVER_ENABLED", "0") != "1":
        return JSONResponse(
            status_code=501,
            content={
                "error": {
                    "code": "comfyui.switchover_disabled",
                    "message": (
                        "ComfyUI switchover is not enabled. It runs root-owned "
                        "scripts on the shared runtime; set "
                        "HAL0_COMFYUI_SWITCHOVER_ENABLED=1 only once a scoped "
                        "sudoers/root-helper path is in place."
                    ),
                }
            },
        )
    # Flag on, but the privileged execution path is intentionally not wired yet.
    return JSONResponse(
        status_code=503,
        content={
            "error": {
                "code": "comfyui.switchover_unimplemented",
                "message": (
                    "switchover is enabled but the privileged root path has not "
                    "been provisioned on this host yet."
                ),
            }
        },
    )


__all__ = ["aclose_client", "router"]
