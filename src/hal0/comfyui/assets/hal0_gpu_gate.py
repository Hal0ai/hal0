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
The container runs with host networking, so hal0-api is on loopback (or on
the one address it binds).

hal0-api's gate routes are ADMIN routes. The gate presents a token of its own
(``HAL0_COMFYUI_GATE_TOKEN_FILE``, a file in a directory the img slot mounts
read-only, read on every request), which hal0-api admits on the gate's own
requests and nothing else, so a box that enforces auth keeps its guard. It
never holds the admin key.

Every request carries the gate's version (``X-Hal0-Gate-Version``), so hal0 can
tell an engine still running an older gate: a running ComfyUI keeps the gate it
started with until the img container restarts. When the hook refuses a prompt
it also tells hal0 why (``POST /api/comfyui/gate-refusal``), so the dashboard
can show it. That report is best effort (2 s, every error swallowed) and is
skipped when hal0 is unreachable, since there is nobody to tell.

hal0 reports ``arbiter.switching`` while it restores the LLM set or enters
image mode; the hook waits it out, so a prompt never renders under LLM weights
that are still loading or not yet unloaded. A running ComfyUI picks up this
file when the img container restarts (the provider copies it when it builds
the spec).

If hal0 reports inference again after the hook asked for image mode (an LLM
request took the GPU back before the prompt reached ComfyUI's queue), the hook
asks again, a bounded number of times.

A prompt never renders on top of the loaded LLM set. The hook refuses it,
answering the prompt with a 503 that carries hal0's reason, when:

  - hal0 rejects the switch request right after reporting inference;
  - hal0 keeps the GPU in inference mode after those requests;
  - hal0 stops answering after the switch request, or has not finished the
    switch by the deadline;
  - hal0 is unreachable (or reports no GPU mode) for ``_UNREACHABLE_RETRY_S``
    from the first read: with hal0-api down the GPU most likely holds the LLM
    set, which is its resting state;
  - hal0 answers 401 or 403 (the gate's credential is missing or stale: a
    container started before hal0 minted it, or one whose token hal0 no longer
    holds), at once, with its own reason.

``HAL0_COMFYUI_GATE_FAIL_OPEN=1`` in the container's environment lets prompts
through in the last two cases only, as v1.6.5 did (the img slot passes it on
from hal0-api's environment).

After a rollback to an older hal0 (1.6.x), which has no ``/api/comfyui/arbiter``
or ``/api/comfyui/prompt-handoff``, the hook reads ``/api/comfyui/status``
instead and lets a prompt in generation mode through without the hand-off, as
the 1.6.x gate did for a healthy hal0. It remembers the older hal0 once
``/status`` has answered like one, and announces every prompt.

The decision logic (``prepare_image_mode``) is
unit-tested in the hal0 repo (tests/comfyui/test_hal0_gpu_gate.py, and against
the real arbiter in tests/slots/test_gpu_arbiter.py); the aiohttp wiring is
exercised by a live check on a test box.
"""

import http.client
import ipaddress
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

#: hal0-api's arbiter-only status (``mode``, ``arbiter``, ``switchover``), cheap
#: to answer: the full ``/status`` aggregate runs probes that can outlast the
#: read budget. The img slot sets these URLs from hal0-api's own port.
HAL0_STATUS_URL = os.environ.get(
    "HAL0_COMFYUI_STATUS_URL", "http://127.0.0.1:8080/api/comfyui/arbiter"
)
HAL0_SWITCHOVER_URL = os.environ.get(
    "HAL0_COMFYUI_SWITCHOVER_URL", "http://127.0.0.1:8080/api/comfyui/switchover"
)
#: Where the hook announces a prompt it lets through in generation mode.
HAL0_HANDOFF_URL = os.environ.get(
    "HAL0_COMFYUI_HANDOFF_URL", "http://127.0.0.1:8080/api/comfyui/prompt-handoff"
)
#: Where the hook tells hal0 why it refused a prompt (best effort).
HAL0_REFUSAL_URL = os.environ.get(
    "HAL0_COMFYUI_REFUSAL_URL", "http://127.0.0.1:8080/api/comfyui/gate-refusal"
)
#: The gate's version, sent as ``X-Hal0-Gate-Version`` on every request. The same
#: literal as ``hal0.comfyui.gate_version.GATE_VERSION``, which this file cannot
#: import; a test holds the two together.
GATE_VERSION = "2"
#: The gate's own credential (#3058): a file in a directory the img slot mounts
#: read-only. hal0-api admits this token on the gate's own requests and
#: nothing else. Read on every request, so a replaced token needs no restart;
#: unset or unusable, the gate sends no credential, as before.
HAL0_GATE_TOKEN_FILE = os.environ.get("HAL0_COMFYUI_GATE_TOKEN_FILE", "")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{43}")


def _legacy_status_url(url: str) -> str:
    """The full ``/status`` route next to the ``/arbiter`` one *url* names."""
    if url.endswith("/arbiter"):
        return url[: -len("/arbiter")] + "/status"
    return url


#: An older hal0 (1.6.x, after a rollback) has neither ``/arbiter`` nor
#: ``/prompt-handoff``. The hook then reads ``/status`` the way the 1.6.x gate
#: did (its top-level ``mode``) and counts an announced prompt as held, since
#: that hal0 has no hand-off. A 404 is the only answer that means "older hal0";
#: everything else keeps its own meaning (unreachable, auth, refused).
HAL0_LEGACY_STATUS_URL = _legacy_status_url(HAL0_STATUS_URL)

#: Set once ``/arbiter`` answered 404 and ``/status`` then answered like a hal0
#: (a GPU mode of generation or inference), and kept for the life of the
#: process (ComfyUI loads the gate once): later reads skip the ``/arbiter``
#: probe. A 404 that ``/status`` cannot back up is not remembered.
_legacy = {"status": False}
_STATUS_TIMEOUT_S = 2.0


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


#: How long one prompt waits for hal0 to hand over the GPU before it is refused.
#: Longer than the arbiter's drain (_DRAIN_TIMEOUT_S = 120 s, waiting for running
#: LLM requests) plus the unloads, and longer than an LLM request's own wait for
#: the GPU (_LLM_DEMAND_WAIT_S = 120 s), so a prompt that arrives during a long
#: generation is not refused just before its switch finishes.
_SWITCH_TIMEOUT_S = _float_env("HAL0_COMFYUI_SWITCH_TIMEOUT_S", 180.0)
_SWITCH_POLL_S = _float_env("HAL0_COMFYUI_SWITCH_POLL_S", 0.5)
#: How long an unreachable hal0 (no answer since this prompt arrived) is retried
#: before the prompt is refused: long enough to ride out an api restart.
_UNREACHABLE_RETRY_S = _float_env("HAL0_COMFYUI_UNREACHABLE_RETRY_S", 5.0)
#: Let prompts through while hal0 is unreachable, or while it requires
#: authentication the gate has no credential for (the gate's v1.6.5
#: behaviour), for a ComfyUI that must keep working then. It never covers a
#: hal0 that answered and refused.
_FAIL_OPEN = os.environ.get("HAL0_COMFYUI_GATE_FAIL_OPEN", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
#: Switch requests per prompt. A request after the first only follows a status
#: that shows inference with no switch running, and waits twice as many polls
#: as the one before it.
_SWITCH_MAX_POSTS = 3


class GpuGateClosed(Exception):
    """hal0 did not hand the GPU to ComfyUI: the prompt must not run."""


class _HalUnreachable(GpuGateClosed):
    """hal0 did not answer (or said nothing about the GPU): there is nobody to report to."""


class _NotFound(Exception):
    """hal0-api answered 404: a route this gate expects is missing (an older hal0)."""


class _AuthRequired(Exception):
    """hal0-api answered 401 or 403: it requires a credential the gate does not have."""

    def __init__(self, code: int) -> None:
        super().__init__(f"HTTP {code}")
        self.code = code


#: hal0-api's answers when it requires authentication (``hal0 auth require on``
#: gates loopback too, and the gate sends no credential).
_AUTH_CODES = (401, 403)


def _auth_reason(code: int) -> str:
    who = (
        "hal0 did not accept ComfyUI's GPU gate credential"
        if _read_token() and _direct_host(HAL0_STATUS_URL)
        else "hal0 requires authentication and ComfyUI's GPU gate has no credential"
    )
    return (
        f"{who} (HTTP {code}), so the prompt was not queued. Run `hal0 slot restart img` "
        "to give the gate its current credential, or set HAL0_COMFYUI_GATE_FAIL_OPEN=1 "
        "to let prompts through unguarded, as v1.6.5 did"
    )


_UNREACHABLE_REASON = (
    "hal0 is unreachable (or did not say which mode holds the GPU), so ComfyUI "
    "cannot be sure the GPU is free of the LLM set; the prompt was not queued. "
    "Queue it again once hal0-api is up, or set HAL0_COMFYUI_GATE_FAIL_OPEN=1 to "
    "let prompts through while hal0 is down"
)


def refused_prompt_body(reason: str) -> dict:
    """The ``/prompt`` answer for a refused prompt, in ComfyUI's prompt-error shape.

    The hook answers the prompt with a 503 that carries this body, so the
    reason reaches whoever submitted it.
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

    ``status`` is hal0-api's /api/comfyui/status JSON, or None when it could
    not be read; only a status that reports inference asks for a switch.
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


#: Failed exchanges with hal0-api: connection errors and timeouts (OSError,
#: URLError), a garbled body (ValueError), and HTTP protocol errors such as
#: IncompleteRead or BadStatusLine, which are not OSErrors.
_READ_ERRORS = (urllib.error.URLError, OSError, ValueError, http.client.HTTPException)


def _read_token() -> "str | None":
    """The gate's token, or None when there is no usable one."""
    if not HAL0_GATE_TOKEN_FILE:
        return None
    try:
        with open(HAL0_GATE_TOKEN_FILE, encoding="ascii") as fh:
            token = fh.read(256).strip()
    except (OSError, ValueError):
        return None
    return token if _TOKEN_RE.fullmatch(token) else None


def _direct_host(url: str) -> bool:
    """True iff *url* names hal0-api by address (or ``localhost``), not by a name.

    The gate speaks plain HTTP, and a name is resolved on every request: a
    spoofed answer would collect the token. hal0 writes loopback or the
    address hal0-api binds into the gate's URLs.
    """
    try:
        host = urllib.parse.urlsplit(url).hostname
    except ValueError:
        return False
    if host == "localhost":
        return True
    try:
        ipaddress.ip_address(host or "")
    except ValueError:
        return False
    return True


def _hal0_request(url: str, body: "bytes | None" = None) -> urllib.request.Request:
    """A request to hal0-api carrying the gate's version and token, a POST when *body* is set.

    The version is an ordinary header: it says nothing worth hiding. The token
    is an unredirected header (urllib copies ``headers`` to a redirect's URL,
    never ``unredirected_hdrs``), sent only to an address.
    """
    if body is None:
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
    req.add_header("X-Hal0-Gate-Version", GATE_VERSION)
    token = _read_token()
    if token and _direct_host(url):
        req.add_unredirected_header("Authorization", f"Bearer {token}")
    return req


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """hal0-api's gate routes never redirect: a 3xx is answered as an HTTP error."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


#: hal0-api is reached directly: no proxy from the container's environment
#: (the token would cross it), no redirects.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def _urlopen(req: urllib.request.Request, timeout: float):
    return _OPENER.open(req, timeout=timeout)


def _fetch_status() -> "dict | None":
    """Blocking status fetch via urllib (run in a thread by the middleware).

    Reads hal0's arbiter-only ``/arbiter`` route, or, once it has answered 404
    (an older hal0), the full ``/status`` route for the rest of the process.
    stdlib-only on purpose: custom nodes can't assume extra deps in the
    ComfyUI venv.
    """
    if not _legacy["status"]:
        try:
            return _read_status(HAL0_STATUS_URL)
        except _NotFound:
            pass
    try:
        data = _read_status(HAL0_LEGACY_STATUS_URL)
    except _NotFound:
        return None
    if isinstance(data, dict) and data.get("mode") in ("generation", "inference"):
        _legacy["status"] = True
    return data


def _read_status(url: str) -> "dict | None":
    """One status read: the JSON object, or None for any failed exchange.

    Raises :class:`_NotFound` on a 404 and :class:`_AuthRequired` on a 401 or
    403, the two answers the hook treats as their own cases.
    """
    try:
        with _urlopen(_hal0_request(url), timeout=_STATUS_TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data if isinstance(data, dict) else None
    except urllib.error.HTTPError as exc:
        if exc.code in _AUTH_CODES:
            raise _AuthRequired(exc.code) from exc
        if exc.code == 404:
            raise _NotFound(url) from exc
        return None
    except _READ_ERRORS:
        return None


#: Why this thread's last switch request failed (the middleware runs each
#: prompt's hook in its own worker thread).
_last_post_error = threading.local()


def _post_switchover() -> bool:
    """Ask hal0-api to enter generation mode; True means accepted/noop.

    On False, :func:`_post_error` says why. stdlib-only on purpose: custom
    nodes can't assume extra deps in the ComfyUI venv.
    """
    _last_post_error.reason = None
    body = json.dumps({"mode": "generation"}).encode("utf-8")
    req = _hal0_request(HAL0_SWITCHOVER_URL, body)
    try:
        with _urlopen(req, timeout=_STATUS_TIMEOUT_S) as resp:
            if resp.status in (200, 202):
                return True
            _last_post_error.reason = f"HTTP {resp.status}"
            return False
    except urllib.error.HTTPError as exc:
        if exc.code in _AUTH_CODES:
            raise _AuthRequired(exc.code) from exc
        # 409 can mean a switch is already in flight. Poll status below.
        if exc.code == 409:
            return True
        _last_post_error.reason = f"HTTP {exc.code}: {_http_error_message(exc)}"
        return False
    except _READ_ERRORS as exc:
        _last_post_error.reason = f"{type(exc).__name__}: {getattr(exc, 'reason', None) or exc}"
        return False


def _announce_prompt() -> "bool | None":
    """Tell hal0 this prompt is about to be queued in generation mode.

    hal0 then holds the GPU for it until ComfyUI's queue can show it. True:
    held. False: the GPU is not ComfyUI's any more (a restore beat the
    prompt) or hal0 is mid-switch; the hook re-reads the status. None: no
    usable answer. An older hal0 without the route (404) has no hand-off, so
    this prompt counts as held, as the 1.6.x gate would let it through; the
    next prompt is announced again.
    """
    req = _hal0_request(HAL0_HANDOFF_URL, b"{}")
    try:
        with _urlopen(req, timeout=_STATUS_TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in _AUTH_CODES:
            raise _AuthRequired(exc.code) from exc
        if exc.code == 404:
            return True
        return None
    except _READ_ERRORS:
        return None
    held = data.get("held") if isinstance(data, dict) else None
    return held if isinstance(held, bool) else None


def _http_error_message(exc: "urllib.error.HTTPError") -> str:
    """hal0's error message from the response body, else the HTTP reason."""
    try:
        data = json.loads(exc.read(4096).decode("utf-8"))
        message = data["error"]["message"]
        if isinstance(message, str) and message:
            return message
    except Exception:
        pass
    return str(exc.reason)


def _post_error() -> str:
    return getattr(_last_post_error, "reason", None) or "no reason given"


#: The longest reason sent to hal0 (hal0 cuts what it keeps to the same length).
_REFUSAL_REASON_MAX = 500


def _report_refusal(reason: str) -> None:
    """Tell hal0 why a prompt was refused, for the dashboard. Best effort.

    A 2 s budget and no error escapes: the prompt is refused the same way
    whether or not hal0 hears why. On a box that enforces auth and a gate
    without a credential this is refused too, and hal0 records the refusal
    itself from the render it failed.
    """
    try:
        body = json.dumps({"reason": reason[:_REFUSAL_REASON_MAX]}).encode("utf-8")
        with _urlopen(_hal0_request(HAL0_REFUSAL_URL, body), timeout=_STATUS_TIMEOUT_S):
            pass
    except Exception:
        return


def prepare_image_mode() -> None:
    """Hand the GPU to ComfyUI before forwarding /prompt (see :func:`_prepare`).

    A 401 or 403 from hal0 refuses the prompt at once with its own reason, or,
    with ``HAL0_COMFYUI_GATE_FAIL_OPEN``, lets it through as v1.6.5 did. Every
    refusal but an unreachable hal0's is reported to hal0 (:func:`_report_refusal`).
    """
    try:
        _prepare()
    except _AuthRequired as exc:
        if _FAIL_OPEN:
            return
        reason = _auth_reason(exc.code)
        _report_refusal(reason)
        raise GpuGateClosed(reason) from exc
    except _HalUnreachable:
        raise
    except GpuGateClosed as exc:
        _report_refusal(str(exc))
        raise


def _prepare() -> None:
    """Hand the GPU to ComfyUI before forwarding /prompt.

    Waits out a switch hal0 is already running, then asks for image mode when
    hal0 reports inference and waits until the switch has finished. If hal0
    reports inference again after a switch (an LLM request took the GPU back
    before the prompt reached the queue), it asks again, at most
    ``_SWITCH_MAX_POSTS`` times in all.

    Returns to let the prompt through once hal0 reports generation mode with
    no switch running and confirms it holds the GPU for the prompt (or, with
    ``HAL0_COMFYUI_GATE_FAIL_OPEN``, while hal0 is unreachable). Otherwise
    raises :class:`GpuGateClosed`; see the module docstring for the cases.
    """
    started = time.monotonic()
    deadline = started + max(0.0, _SWITCH_TIMEOUT_S)
    poll_s = max(0.05, _SWITCH_POLL_S)
    answered = False  # hal0 has reported a GPU mode for this prompt
    silent = False  # the last read, or the last announce, got no answer
    posts = 0
    polls = 0
    next_post_poll = 0
    while True:
        status = _fetch_status()
        mode = status.get("mode") if isinstance(status, dict) else None
        silent = mode not in ("generation", "inference")
        if silent:
            # No answer, or none about the GPU. Before hal0 has answered, this
            # is an unreachable hal0: retry briefly, then refuse (or, opted
            # out, let the prompt through). After it answered, hal0 may be
            # mid-switch with the LLM set still loaded: keep polling.
            if not answered and time.monotonic() - started >= _UNREACHABLE_RETRY_S:
                if _FAIL_OPEN:
                    return
                raise _HalUnreachable(_UNREACHABLE_REASON)
        else:
            answered = True
            if not (_arbiter_switching(status) or _switchover_active(status)):
                if mode == "generation":
                    # Announce the prompt: hal0 holds the GPU for it until
                    # ComfyUI's queue shows it, so no restore frees it first.
                    held = _announce_prompt()
                    if held:
                        return
                    silent = held is None  # False: re-read the status
                elif posts >= _SWITCH_MAX_POSTS:
                    raise GpuGateClosed(_inference_reason(status, posts))
                elif polls >= next_post_poll:
                    if not _post_switchover():
                        raise GpuGateClosed(
                            "hal0 reports the GPU in inference mode and refused the switch "
                            f"to image generation ({_post_error()}), so the prompt was not "
                            "queued; queue it again"
                        )
                    posts += 1
                    next_post_poll = polls + 2**posts
        if time.monotonic() >= deadline:
            if not answered:
                if _FAIL_OPEN:
                    return
                raise _HalUnreachable(_UNREACHABLE_REASON)
            if silent:
                raise GpuGateClosed(
                    "hal0 stopped answering while it switched the GPU to image "
                    "generation, so the prompt was not queued; queue it again"
                )
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
