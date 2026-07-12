"""hal0 GPU prepare hook — asks hal0 to enter image mode before job submit.

The ComfyUI container is RESIDENT on hal0 (its web UI stays up in both GPU
modes so users can build workflows/prompts any time), and queueing a prompt is
now allowed from either mode. When the web UI's own "Queue Prompt"
(``POST /prompt`` / ``POST /api/prompt``) arrives while hal0 still reports
inference mode, this middleware asks hal0-api's GpuArbiter to switch to image
mode and waits briefly for the switch to complete before allowing ComfyUI to
continue.

Deployment: this single file is dropped into the host-mounted
``custom_nodes`` dir (``/mnt/ai-models/comfyui/custom_nodes/``) — no image
rebuild. ComfyUI imports it at startup; ``_install()`` appends an aiohttp
middleware to the PromptServer app (the same mechanism ComfyUI-Login uses).
The container runs with host networking, so hal0-api is on loopback.

Fail-open by design: if hal0-api is unreachable or the switch request fails,
the hook allows the prompt — a broken hal0 must never brick standalone ComfyUI
use. The pure decision logic (``should_prepare_image_mode``) is unit-tested in the hal0 repo
(tests/comfyui/test_hal0_gpu_gate.py); the aiohttp wiring is exercised by
the CT105 live verification.
"""

import json
import os
import time
import urllib.error
import urllib.request

#: hal0-api status aggregator; its top-level ``mode`` is arbiter-truth
#: ("generation" | "inference").
HAL0_STATUS_URL = os.environ.get(
    "HAL0_COMFYUI_STATUS_URL", "http://127.0.0.1:8080/api/comfyui/status"
)
HAL0_SWITCHOVER_URL = os.environ.get(
    "HAL0_COMFYUI_SWITCHOVER_URL", "http://127.0.0.1:8080/api/comfyui/switchover"
)
_STATUS_TIMEOUT_S = 2.0


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


_SWITCH_TIMEOUT_S = _float_env("HAL0_COMFYUI_SWITCH_TIMEOUT_S", 120.0)
_SWITCH_POLL_S = _float_env("HAL0_COMFYUI_SWITCH_POLL_S", 0.5)

#: Job-submission routes — and ONLY those. Everything the editor needs
#: (/object_info, /queue GET, workflow save/load, uploads) always passes.
_BLOCK_PATHS = frozenset({"/prompt", "/api/prompt"})

def _is_prompt_submit(method: str, path: str) -> bool:
    return method == "POST" and path in _BLOCK_PATHS


def should_prepare_image_mode(method: str, path: str, status: "dict | None") -> bool:
    """True iff this prompt submit should ask hal0 to enter image mode.

    ``status`` is hal0-api's /api/comfyui/status JSON (or None when
    unreachable / unparseable → fail-open).
    """
    if not _is_prompt_submit(method, path):
        return False
    if not isinstance(status, dict):
        return False
    return status.get("mode") == "inference"


def should_block(method: str, path: str, status: "dict | None") -> bool:
    """Backward-compatible name for older tests/imports.

    Prompt submission is no longer blocked by this custom node; it prepares
    image mode best-effort, then lets ComfyUI handle the prompt.
    """
    return False


def _fetch_status() -> "dict | None":
    """Blocking status fetch via urllib (run in a thread by the middleware).

    stdlib-only on purpose: custom nodes can't assume extra deps in the
    ComfyUI venv.
    """
    try:
        with urllib.request.urlopen(HAL0_STATUS_URL, timeout=_STATUS_TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data if isinstance(data, dict) else None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _post_switchover() -> bool:
    """Ask hal0-api to enter generation mode; True means accepted/noop.

    stdlib-only on purpose: custom nodes can't assume extra deps in the
    ComfyUI venv.
    """
    body = json.dumps({"mode": "generation"}).encode("utf-8")
    req = urllib.request.Request(
        HAL0_SWITCHOVER_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_STATUS_TIMEOUT_S) as resp:
            return resp.status in (200, 202)
    except urllib.error.HTTPError as exc:
        # 409 can mean a switch is already in flight. Poll status below.
        return exc.code == 409
    except (urllib.error.URLError, OSError, ValueError):
        return False


def prepare_image_mode() -> None:
    """Best-effort inference → generation handoff before forwarding /prompt.

    Fail-open: exceptions and timeouts return without blocking the prompt.
    """
    status = _fetch_status()
    if not should_prepare_image_mode("POST", "/prompt", status):
        return
    if not _post_switchover():
        return

    deadline = time.monotonic() + max(0.0, _SWITCH_TIMEOUT_S)
    while time.monotonic() < deadline:
        status = _fetch_status()
        if not isinstance(status, dict):
            return
        if status.get("mode") == "generation":
            return
        sw = status.get("switchover")
        if isinstance(sw, dict) and sw.get("error"):
            return
        time.sleep(max(0.05, _SWITCH_POLL_S))


def _install() -> None:
    """Attach the prepare middleware to ComfyUI's PromptServer (fail-soft)."""
    try:
        import asyncio

        from aiohttp import web
        from server import PromptServer

        @web.middleware
        async def hal0_gpu_gate_middleware(request, handler):
            if _is_prompt_submit(request.method, request.path):
                await asyncio.to_thread(prepare_image_mode)
            return await handler(request)

        PromptServer.instance.app.middlewares.append(hal0_gpu_gate_middleware)
        print(
            "[hal0_gpu_gate] /prompt prepares hal0 image mode "
            f"({HAL0_STATUS_URL} → {HAL0_SWITCHOVER_URL})"
        )
    except Exception as exc:  # outside ComfyUI (unit tests) or API drift
        print(f"[hal0_gpu_gate] not installed: {exc}")


_install()

# ComfyUI custom-node import contract — this node adds no graph nodes.
NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}
