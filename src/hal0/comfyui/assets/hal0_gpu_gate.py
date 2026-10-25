"""hal0 GPU prepare hook — asks hal0 to enter image mode before job submit.

The ComfyUI container is RESIDENT on hal0 (its web UI stays up in both GPU
modes so users can build workflows/prompts any time), and queueing a prompt is
now allowed from either mode. When the web UI's own "Queue Prompt"
(``POST /prompt`` / ``POST /api/prompt``) arrives while hal0 still reports
inference mode, this middleware asks hal0-api's GpuArbiter to switch to image
mode and waits briefly for the switch to complete before allowing ComfyUI to
continue.

Deployment: this single file ships as a hal0 package asset
(``hal0.comfyui.assets``); the ComfyUI provider copies it into the
host-mounted ``custom_nodes`` dir (``hal0.comfyui.paths.custom_nodes_dir()``)
the first time it builds the container spec after hal0-api starts, replacing a
stale copy — no image rebuild. ComfyUI imports it at startup; ``_install()`` appends an aiohttp
middleware to the PromptServer app (the same mechanism ComfyUI-Login uses).
The container runs with host networking, so hal0-api is on loopback.

hal0 reports ``arbiter.switching`` while it restores the LLM set or enters
image mode; the hook waits it out, so a prompt never renders under LLM weights
that are still loading or not yet unloaded. A running ComfyUI picks up this
file when the img container restarts (the provider copies it when it builds
the spec).

If hal0 reports inference again after the hook asked for image mode (an LLM
request took the GPU back before the prompt reached ComfyUI's queue), the hook
asks again, a bounded number of times. When hal0 answers but has not handed
the GPU to ComfyUI by the deadline, or keeps it in inference mode after those
requests, the hook refuses the prompt: ComfyUI's editor shows its prompt-error
dialog with hal0's reason, and the user queues the prompt again. A prompt never
renders on top of the loaded LLM set.

Fail-open otherwise: if hal0-api is unreachable, reports no arbiter, or rejects
the switch request, the hook allows the prompt — a broken hal0 must never brick
standalone ComfyUI use. The decision logic (``prepare_image_mode``) is
unit-tested in the hal0 repo (tests/comfyui/test_hal0_gpu_gate.py, and against
the real arbiter in tests/slots/test_gpu_arbiter.py); the aiohttp wiring is
exercised by a live check on a test box.
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
#: Switch requests per prompt. A request after the first only follows a status
#: that shows inference with no switch running, and waits twice as many polls
#: as the one before it.
_SWITCH_MAX_POSTS = 3


class GpuGateClosed(Exception):
    """hal0 answered but did not hand the GPU to ComfyUI: the prompt must not run."""


def refused_prompt_body(reason: str) -> dict:
    """The ``/prompt`` answer for a refused prompt, in ComfyUI's prompt-error shape.

    ComfyUI's editor turns a non-200 answer of this shape into its prompt-error
    dialog and shows ``message: details``.
    """
    return {
        "error": {
            "type": "hal0_gpu_not_ready",
            "message": "hal0 did not hand the GPU to ComfyUI",
            "details": reason,
            "extra_info": {},
        },
        "node_errors": {},
    }


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


def _arbiter_switching(status: "dict | None") -> bool:
    """True iff hal0 reports its GPU arbiter mid-switch (``arbiter.switching``).

    hal0 holds the switch lock while it enters image mode (it persists the
    generation mode before the LLM set is unloaded) and while it restores the
    LLM set (the mode reads generation until the reload is done), so neither
    mode is final until this clears.
    """
    if not isinstance(status, dict):
        return False
    arbiter = status.get("arbiter")
    return isinstance(arbiter, dict) and bool(arbiter.get("switching"))


def _switchover_active(status: "dict | None") -> bool:
    """True iff hal0's switchover route has accepted a switch that has not finished."""
    if not isinstance(status, dict):
        return False
    switchover = status.get("switchover")
    return isinstance(switchover, dict) and bool(switchover.get("active"))


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
    """Hand the GPU to ComfyUI before forwarding /prompt.

    Waits out a switch hal0 is already running, then asks for image mode when
    hal0 reports inference and waits until the switch has finished. If hal0
    reports inference again after a switch (an LLM request took the GPU back
    before the prompt reached the queue), it asks again, at most
    ``_SWITCH_MAX_POSTS`` times in all.

    Returns to let the prompt through: in generation mode, or failing open
    when hal0 is unreachable, reports no arbiter, or rejects a switch request.
    Raises :class:`GpuGateClosed` when hal0 answers but keeps the GPU in
    inference mode after those requests, or has not finished by the deadline.
    """
    deadline = time.monotonic() + max(0.0, _SWITCH_TIMEOUT_S)
    poll_s = max(0.05, _SWITCH_POLL_S)
    posts = 0
    polls = 0
    next_post_poll = 0
    while True:
        status = _fetch_status()
        if not isinstance(status, dict):
            return  # hal0 unreachable: fail open
        if not (_arbiter_switching(status) or _switchover_active(status)):
            if status.get("mode") == "generation":
                return
            if not should_prepare_image_mode("POST", "/prompt", status):
                return  # no arbiter answer, so no known mode: fail open
            if posts >= _SWITCH_MAX_POSTS:
                raise GpuGateClosed(_inference_reason(status, posts))
            if polls >= next_post_poll:
                if not _post_switchover():
                    return  # hal0 rejected the request: fail open
                posts += 1
                next_post_poll = polls + 2**posts
        if time.monotonic() >= deadline:
            raise GpuGateClosed(
                f"hal0 did not finish switching the GPU to image generation within "
                f"{_SWITCH_TIMEOUT_S:.0f}s, so the prompt was not queued; queue it again"
            )
        time.sleep(poll_s)
        polls += 1


def _inference_reason(status: dict, posts: int) -> str:
    switchover = status.get("switchover")
    error = switchover.get("error") if isinstance(switchover, dict) else None
    reason = (
        f"hal0 kept the GPU in inference mode after {posts} switch request(s), "
        "so the prompt was not queued; queue it again"
    )
    return f"{reason} (last switch error: {error})" if error else reason


def _install() -> None:
    """Attach the prepare middleware to ComfyUI's PromptServer (fail-soft)."""
    try:
        import asyncio

        from aiohttp import web
        from server import PromptServer

        @web.middleware
        async def hal0_gpu_gate_middleware(request, handler):
            if _is_prompt_submit(request.method, request.path):
                try:
                    await asyncio.to_thread(prepare_image_mode)
                except GpuGateClosed as exc:
                    return web.json_response(refused_prompt_body(str(exc)), status=503)
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
