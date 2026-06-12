"""hal0 GPU gate — ComfyUI custom node that 403-blocks job submission while
the Strix Halo iGPU is serving the LLM stack.

The ComfyUI container is RESIDENT on hal0 (its web UI stays up in both GPU
modes so users can build workflows/prompts any time), but GPU memory is
exclusive: running a generation while the LLM slots hold GTT would OOM or
evict them. hal0-api's GpuArbiter guards its own dispatch path, and this
middleware closes the remaining door — the web UI's own "Queue Prompt"
(``POST /prompt`` / ``POST /api/prompt``), which goes straight to ComfyUI.

Deployment: this single file is dropped into the host-mounted
``custom_nodes`` dir (``/mnt/ai-models/comfyui/custom_nodes/``) — no image
rebuild. ComfyUI imports it at startup; ``_install()`` appends an aiohttp
middleware to the PromptServer app (the same mechanism ComfyUI-Login uses).
The container runs with host networking, so hal0-api is on loopback.

Fail-open by design: if hal0-api is unreachable the gate allows the prompt —
a broken hal0 must never brick standalone ComfyUI use. The pure decision
logic (``should_block``) is unit-tested in the hal0 repo
(tests/comfyui/test_hal0_gpu_gate.py); the aiohttp wiring is exercised by
the CT105 live verification.
"""

import json
import os
import urllib.error
import urllib.request

#: hal0-api status aggregator; its top-level ``mode`` is arbiter-truth
#: ("generation" | "inference").
HAL0_STATUS_URL = os.environ.get(
    "HAL0_COMFYUI_STATUS_URL", "http://127.0.0.1:8080/api/comfyui/status"
)
_STATUS_TIMEOUT_S = 2.0

#: Job-submission routes — and ONLY those. Everything the editor needs
#: (/object_info, /queue GET, workflow save/load, uploads) always passes.
_BLOCK_PATHS = frozenset({"/prompt", "/api/prompt"})

#: Mirrors ComfyUI's own /prompt error envelope so the frontend renders the
#: message instead of a generic failure toast.
GATE_BODY = {
    "error": {
        "type": "hal0_gpu_gate",
        "message": (
            "The GPU is in inference mode (LLM slots loaded). Flip the "
            "Image Gen switch in the hal0 dashboard, then queue again."
        ),
        "details": "hal0 GpuArbiter mode is 'inference'; generation is gated.",
        "extra_info": {},
    },
    "node_errors": {},
}


def should_block(method: str, path: str, status: "dict | None") -> bool:
    """True iff this request is a job submission while the GPU serves LLMs.

    ``status`` is hal0-api's /api/comfyui/status JSON (or None when
    unreachable / unparseable → fail-open).
    """
    if method != "POST" or path not in _BLOCK_PATHS:
        return False
    if not isinstance(status, dict):
        return False
    return status.get("mode") == "inference"


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


def _install() -> None:
    """Attach the gate middleware to ComfyUI's PromptServer (fail-soft)."""
    try:
        import asyncio

        from aiohttp import web
        from server import PromptServer

        @web.middleware
        async def hal0_gpu_gate_middleware(request, handler):
            if request.method == "POST" and request.path in _BLOCK_PATHS:
                status = await asyncio.to_thread(_fetch_status)
                if should_block(request.method, request.path, status):
                    return web.json_response(GATE_BODY, status=403)
            return await handler(request)

        PromptServer.instance.app.middlewares.append(hal0_gpu_gate_middleware)
        print(f"[hal0_gpu_gate] /prompt gated on hal0 GPU mode ({HAL0_STATUS_URL})")
    except Exception as exc:  # outside ComfyUI (unit tests) or API drift
        print(f"[hal0_gpu_gate] not installed: {exc}")


_install()

# ComfyUI custom-node import contract — this node adds no graph nodes.
NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}
