"""ComfyUIProvider — Stable-Diffusion-family image generation backend.

ComfyUI is a node-graph stable-diffusion runtime. We treat it as a
black box that speaks ``POST /prompt`` (submit a graph) + ``GET /history/<id>``
(poll for completion) + ``GET /view`` (download an output PNG). The
OpenAI-shaped ``POST /v1/images/generations`` request is translated to a
parametric workflow (see :mod:`hal0.providers.comfyui_workflows`) and the
result PNG bytes are unwrapped back into the OpenAI response shape.

Runner image:     ghcr.io/hal0ai/hal0-runner-comfyui, pinned in manifest.json
                  ``runners.comfyui``. The image ships ComfyUI checked out at /opt/ComfyUI with
                  its venv at /opt/venv — NOT /app (layout mirrors the kyuz0
                  Strix Halo build it replaced, by construction).
Backend default:  rocm  (Strix Halo iGPU is the v1 first-class target).

Endpoints we touch on the upstream:
    GET  /system_stats          — health probe ("python_version" present).
    POST /prompt                — submit a workflow JSON.
    GET  /history/<prompt_id>   — poll for completion + outputs metadata.
    GET  /view?filename=...     — fetch an output PNG.

# NOTE: ComfyUI's /system_stats endpoint is the closest thing to a
# llama.cpp-style /health probe. It returns 200 once the server has
# initialised the Python runtime; the absence of an expensive sentinel
# (e.g. running a 1-step generation) is intentional — the model isn't
# loaded until first inference, and that lazy-load is fine because
# /v1/images/generations is high-latency by design.
"""

from __future__ import annotations

import asyncio
import importlib.resources
import json
import logging
import os
import re
import reprlib
import tempfile
import time
import urllib.parse
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from websockets.asyncio.client import connect as _websocket_connect

from hal0.comfyui import gate_credential, progress
from hal0.comfyui import paths as comfyui_paths
from hal0.comfyui.gate_reason import SOCKET_ID_MAX, SOCKET_LOG_MAX, clean_reason
from hal0.config import paths as cfg_paths
from hal0.errors import Hal0Error
from hal0.http_client import async_client
from hal0.install.perms import ensure_shared_dir
from hal0.providers._gpu import resolve_gpu_device_paths, resolve_gpu_group_ids
from hal0.providers.base import Mount, Provider, RuntimeLaunchPlan
from hal0.providers.comfyui_workflows import build_workflow

log = logging.getLogger(__name__)


# In-container app + working-data layout. The image has ComfyUI
# checked out at /opt/ComfyUI (venv at /opt/venv). Host-side working data
# (models / output / input / user / custom_nodes + extra_model_paths.yaml)
# lives under the data root owned by hal0.comfyui.paths and is bind-mounted in
# so weights and custom nodes persist across container restarts.
_COMFYUI_APP_DIR = "/opt/ComfyUI"
_COMFYUI_BASE_DIR = "/var/lib/hal0/comfyui"
# Where the host's models dir is mounted inside the container; also the
# base_path extra_model_paths.yaml points ComfyUI at.
_COMFYUI_MODELS_MOUNT = "/root/comfy-models"
# The GPU-gate custom node, shipped as a package asset (hal0.comfyui.assets)
# and synced into custom_nodes/ once per process (see provision_data_root).
_GPU_GATE_NODE = "hal0_gpu_gate.py"

# Live-validated flag bundle (matches the "comfyui" seed profile). Used
# when the slot has no resolvable profile.
_DEFAULT_PROFILE_FLAGS = "--disable-mmap --bf16-vae --cache-none"

# Default port — ComfyUI's stock listen port.
_DEFAULT_PORT = 8188

# The hal0_gpu_gate custom node's settings, read inside the container at import
# (see hal0/comfyui/assets/hal0_gpu_gate.py): where hal0-api answers, and
# whether to let prompts through while it is unreachable instead of refusing.
_GATE_STATUS_URL_ENV = "HAL0_COMFYUI_STATUS_URL"
_GATE_SWITCHOVER_URL_ENV = "HAL0_COMFYUI_SWITCHOVER_URL"
_GATE_HANDOFF_URL_ENV = "HAL0_COMFYUI_HANDOFF_URL"
_GATE_REFUSAL_URL_ENV = "HAL0_COMFYUI_REFUSAL_URL"
_GATE_FAIL_OPEN_ENV = "HAL0_COMFYUI_GATE_FAIL_OPEN"
# The gate's own credential (#3058): its directory is mounted read-only here,
# and the gate reads the token file named by this variable on every request.
_GATE_TOKEN_FILE_ENV = "HAL0_COMFYUI_GATE_TOKEN_FILE"
_GATE_TOKEN_MOUNT = "/opt/hal0-gate"
_TRUTHY = ("1", "true", "yes", "on")
_DEFAULT_API_PORT = 8080
# A bind host that is safe to write into a unit file's Environment= line.
_HOST_RE = re.compile(r"^[A-Za-z0-9.:%-]+$")


# ── Timeouts ──────────────────────────────────────────────────────────────────
_HEALTH_TIMEOUT = httpx.Timeout(5.0)
# Submit / view are quick HTTP exchanges; the actual diffusion runs
# server-side and we poll history for it. Allow generous read budgets
# anyway because ComfyUI can spend tens of seconds doing the first
# checkpoint load on a cold start.
_INFER_TIMEOUT = httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=10.0)
# Polling cap for history. SDXL Turbo: ~3-30s on the iGPU. SD 1.5: ~10-60s.
# 10 minutes is the absolute ceiling — if we're past that something is
# stuck and the operator needs to investigate.
_HISTORY_POLL_TIMEOUT_S = 600.0
_HISTORY_POLL_INTERVAL_S = 0.5
# The progress websocket (see _follow_progress). How long ``infer`` waits for it to be
# open before it submits anyway: a loopback handshake takes milliseconds, so this only
# ever bounds a ComfyUI too busy to answer. Progress is a read-out, never worth a stall.
_WS_READY_WAIT_S = 2.0
# Give up on the handshake itself, and on the close handshake when ``infer`` is done.
_WS_OPEN_TIMEOUT_S = 5.0
_WS_CLOSE_TIMEOUT_S = 2.0
# Preview frames are binary images and are ignored, but a frame over the library's 1 MiB
# default would close the socket, and with it the progress read-out.
_WS_MAX_FRAME_BYTES = 16 * 1024 * 1024


# ── Typed errors ──────────────────────────────────────────────────────────────


class ComfyUIHealthError(Hal0Error):
    """ComfyUI health probe failed."""

    code = "slot.not_ready"
    status = 503


class ComfyUIInferError(Hal0Error):
    """ComfyUI inference call failed."""

    code = "dispatch.upstream_failed"
    status = 502


class ImageGateRefused(Hal0Error):
    """ComfyUI's GPU gate refused the prompt hal0 submitted.

    The gate (``hal0_gpu_gate``) answers ``/prompt`` with a 503 when hal0 did not
    hand it the GPU. The message is the gate's reason. It is written inside the img
    container, so it is passed through :func:`hal0.comfyui.gate_reason.clean_reason`
    (encodable, at most 500 characters) before it reaches an envelope, a log or Sentry.
    """

    code = "image.gate_refused"
    status = 503

    def __init__(
        self, message: str, details: dict[str, Any] | None = None, *, code: str | None = None
    ) -> None:
        super().__init__(clean_reason(message), details, code=code)


#: ``error.type`` of the body the gate answers a refused prompt with
#: (``refused_prompt_body`` in the ``hal0_gpu_gate`` asset, which a test runs this
#: against). The shape identifies the gate; the words of its reason change.
_GATE_REFUSAL_TYPE = "hal0_gpu_not_ready"
_GATE_REFUSAL_DEFAULT = "ComfyUI's GPU gate did not accept the prompt"


def gate_refusal_reason(status_code: int, body: Any) -> str | None:
    """The gate's reason when a ``/prompt`` answer is its refusal, else ``None``.

    A refusal is a 503 whose JSON body has the gate's shape: an ``error`` object
    typed ``hal0_gpu_not_ready`` beside a ``node_errors`` object. The reason is the
    body's ``details``, the text the gate was given, cleaned and cut to 500 characters
    (:func:`hal0.comfyui.gate_reason.clean_reason`: the body comes from inside the container).
    """
    if status_code != 503 or not isinstance(body, dict):
        return None
    error = body.get("error")
    if not isinstance(error, dict) or error.get("type") != _GATE_REFUSAL_TYPE:
        return None
    if not isinstance(body.get("node_errors"), dict):
        return None
    for key in ("details", "message"):
        value = error.get(key)
        if isinstance(value, str) and value:
            return clean_reason(value)
    return _GATE_REFUSAL_DEFAULT


def _json_or_none(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return None


# ── Step progress over ComfyUI's websocket ────────────────────────────────────


def _ws_connect(url: str) -> Any:
    """Open ComfyUI's websocket at ``url``; use as ``async with _ws_connect(url) as socket``.

    The seam the tests replace, so no test dials out. Keepalive pings are off: ComfyUI can
    starve its own event loop for longer than a ping timeout while it loads a checkpoint,
    and that must not cost the progress read-out. The listener is cancelled when the
    render ends, which is what closes the connection. Compression is off too: this is
    loopback, and the frames that matter are a few dozen bytes.
    """
    return _websocket_connect(
        url,
        compression=None,
        ping_interval=None,
        open_timeout=_WS_OPEN_TIMEOUT_S,
        close_timeout=_WS_CLOSE_TIMEOUT_S,
        max_size=_WS_MAX_FRAME_BYTES,
    )


def _ws_url(base_url: str, client_id: str) -> str:
    """``base_url`` (the one ``/prompt`` is posted to) with ``http`` swapped for ``ws``."""
    return f"ws{base_url.removeprefix('http')}/ws?clientId={client_id}"


def _as_count(value: object) -> int | None:
    """``value`` if it is an integer (a JSON ``true`` is not one), else ``None``."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


class _ProgressRelay:
    """Carries one render's websocket frames into :mod:`hal0.comfyui.progress`.

    The socket is open before ``/prompt`` answers, so a frame can arrive before the prompt
    id is known. Until :meth:`claim` supplies it, only the newest progress frame is kept,
    and :meth:`claim` applies it. Every frame on the socket belongs to this render (ComfyUI
    sends progress only to the ``client_id`` that submitted the prompt), but a frame that
    names a different prompt is still ignored.
    """

    def __init__(self, model: str) -> None:
        self.model = model
        self.prompt_id: str | None = None
        self._node: str | None = None
        self._early: tuple[str | None, int, int, str | None] | None = None

    def claim(self, prompt_id: str) -> None:
        """ComfyUI accepted the prompt: start tracking it, with any frame heard already."""
        self.prompt_id = prompt_id
        progress.start(prompt_id, self.model)
        early, self._early = self._early, None
        if early is not None:
            self._apply(*early)

    def release(self) -> None:
        """The render is over, however it ended."""
        if self.prompt_id is not None:
            progress.finish(self.prompt_id)

    def feed(self, frame: str | bytes) -> None:
        """Take one websocket frame. Raises ``ValueError`` for one that cannot be read.

        Binary frames are previews and are ignored, as are message types other than
        ``executing`` (which names the node) and ``progress``.
        """
        if not isinstance(frame, str):
            return
        message = json.loads(frame)
        if not isinstance(message, dict):
            raise ValueError(f"frame is {type(message).__name__}, not an object")
        data = message.get("data")
        kind = message.get("type")
        if kind == "executing":
            node = data.get("node") if isinstance(data, dict) else None
            if node is not None:
                self._node = clean_reason(node, SOCKET_ID_MAX)
        elif kind == "progress":
            if not isinstance(data, dict):
                raise ValueError("progress frame has no data object")
            step, total = _as_count(data.get("value")), _as_count(data.get("max"))
            if step is None or total is None:
                raise ValueError(
                    "progress frame has no integer value/max: "
                    f"value={reprlib.repr(data.get('value'))} max={reprlib.repr(data.get('max'))}"
                )
            node = data.get("node")
            frame_prompt = data.get("prompt_id")
            args = (
                None if frame_prompt is None else str(frame_prompt),
                step,
                total,
                clean_reason(node, SOCKET_ID_MAX) if node is not None else self._node,
            )
            if self.prompt_id is None:
                self._early = args
            else:
                self._apply(*args)

    def _apply(self, frame_prompt: str | None, step: int, total: int, node: str | None) -> None:
        # The frame's prompt id is absent on ComfyUI builds that predate it.
        if self.prompt_id is None or frame_prompt not in (None, self.prompt_id):
            return
        progress.update(self.prompt_id, step=step, total=total, node=node)


async def _follow_progress(url: str, relay: _ProgressRelay, ready: asyncio.Event) -> None:
    """Feed ``relay`` from ComfyUI's websocket at ``url`` until cancelled.

    ``ready`` is set once the socket is open, or once it cannot be opened, so the caller
    never waits on a listener that already failed. A connection that fails or a socket the
    server closes mid-render logs one ``comfyui.progress_listener_failed`` warning and ends
    the listener. A frame that cannot be read is skipped and the listener carries on; the
    first such frame of a render logs one ``comfyui.progress_frame_skipped`` warning, the
    rest are quiet. None of this is an error for the render, which finishes on ``/history``
    alone. Cancelling the listener is silent.
    """
    skipped = False
    try:
        async with _ws_connect(url) as socket:
            ready.set()
            async for frame in socket:
                try:
                    relay.feed(frame)
                except (ValueError, RecursionError) as exc:
                    # One odd frame (a float step, a build whose shapes differ) is not a
                    # reason to lose the read-out for the rest of the render.
                    if not skipped:
                        skipped = True
                        log.warning(
                            "comfyui.progress_frame_skipped",
                            extra={"url": url, "reason": _socket_error_text(exc)},
                        )
        reason = "websocket closed by ComfyUI"
    except Exception as exc:
        reason = _socket_error_text(exc)
    finally:
        ready.set()
    log.warning("comfyui.progress_listener_failed", extra={"url": url, "reason": reason})


def _socket_error_text(exc: BaseException) -> str:
    """``exc`` as one log-safe line: it can quote what ComfyUI sent, so it is cleaned and cut."""
    return clean_reason(f"{type(exc).__name__}: {exc}", SOCKET_LOG_MAX)


async def _wait_until_open(ready: asyncio.Event) -> None:
    """Give the listener a moment to open its socket; carry on without it after that."""
    try:
        await asyncio.wait_for(ready.wait(), _WS_READY_WAIT_S)
    except TimeoutError:
        log.debug("comfyui.progress_listener_slow", extra={"wait_s": _WS_READY_WAIT_S})


# ── Data-root provisioning ────────────────────────────────────────────────────


def render_extra_model_paths() -> str:
    """The ``extra_model_paths.yaml`` that maps ComfyUI's model types onto the mounted models dir.

    One ``<type>: <dir>`` line per :data:`hal0.comfyui.paths.SUBDIRS` entry, in
    sorted order, under ``base_path`` (the in-container models mount).
    """
    entries = (f"    {key}: {comfyui_paths.SUBDIRS[key]}" for key in sorted(comfyui_paths.SUBDIRS))
    return "\n".join(["comfyui:", f"    base_path: {_COMFYUI_MODELS_MOUNT}", *entries]) + "\n"


# Provisioning runs on every container_spec render, and the slot-drift probe the
# dashboard polls renders every 30 s. The file writes are therefore memoised per
# data root, and each distinct failure logs once per process.
_provisioned: set[tuple[str, str]] = set()
_warned: set[tuple[str, ...]] = set()


def _reset_provision_memo() -> None:
    """Forget what this process provisioned and warned about (test hook)."""
    _provisioned.clear()
    _warned.clear()


def _warn_once(key: tuple[str, ...], message: str, *args: object) -> None:
    if key in _warned:
        return
    _warned.add(key)
    log.warning(message, *args)


def _ensure_data_dirs() -> None:
    """Create every directory the container spec bind-mounts (and ``workflows``).

    The bind-mount sources come first, and each mkdir fails on its own: one
    blocked directory must not leave another bind-mount source missing. A
    directory that exists is left alone (this runs on every render), and one
    that is created lands at the shared-directory mode whatever the umask.
    """
    root = comfyui_paths.data_root()
    models = comfyui_paths.models_root()
    dirs = [
        models,
        root / "output",
        root / "input",
        comfyui_paths.user_dir(),
        comfyui_paths.custom_nodes_dir(),
        comfyui_paths.workflows_dir(),
        *(models / sub for sub in comfyui_paths.SUBDIRS.values()),
    ]
    for directory in dirs:
        if directory.is_dir():
            continue
        try:
            ensure_shared_dir(directory)
        except OSError as exc:
            _warn_once(
                ("mkdir", str(directory)),
                "comfyui.data_root_provision_failed step=mkdir path=%s err=%s",
                directory,
                exc,
            )


def _ensure_extra_model_paths() -> bool:
    """Write the model-paths file unless the operator already put content in it.

    An empty file hides every model from ComfyUI, so absent or blank (after
    strip) means "write ours"; anything else is the operator's and stays.
    Returns True once the file is settled.
    """
    path = comfyui_paths.extra_model_paths_file()
    try:
        if path.read_bytes().strip():
            return True
    except FileNotFoundError:
        pass
    path.write_text(render_extra_model_paths(), encoding="utf-8")
    return True


def _write_atomically(dest: Path, data: bytes) -> None:
    """Stage ``data`` beside ``dest`` and rename it into place.

    A reader (ComfyUI importing its custom nodes) never sees a half-written
    file, and replacing a file only needs write access to the directory, not
    to a root-owned file inside it.
    """
    fd, tmp_name = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        tmp.chmod(0o644)
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _sync_gpu_gate_node() -> bool:
    """Copy the packaged GPU-gate node into ``custom_nodes/`` when it is missing or differs.

    Returns True once the node is current, False when the directory is not
    writable (already reported, once per process, with the remedy).
    """
    shipped = importlib.resources.files("hal0.comfyui.assets").joinpath(_GPU_GATE_NODE)
    wanted = shipped.read_bytes()
    dest = comfyui_paths.custom_nodes_dir() / _GPU_GATE_NODE
    try:
        if dest.read_bytes() == wanted:
            return True
    except FileNotFoundError:
        pass
    try:
        _write_atomically(dest, wanted)
    except PermissionError:
        # A box whose installer copied the node as root, with a custom_nodes
        # directory the hal0 user cannot write. The node stays as it is.
        _warn_once(
            ("node-permission", str(dest)),
            "cannot update %s: make %s writable by the hal0 user or group "
            "(e.g. chgrp <hal0 group> + chmod 2775), or replace the file as root",
            dest,
            dest.parent,
        )
        return False
    return True


def provision_data_root() -> None:
    """Make the ComfyUI data root ready to bind-mount; idempotent and best-effort.

    Podman refuses a bind mount whose host path is absent (statfs, exit 125),
    which crash-looped the img slot on any box where the tree hadn't been
    provisioned (fresh install, or a store root that moved after install). So
    this creates the mounted directories, writes ``extra_model_paths.yaml`` if
    it is absent or empty, and syncs the GPU-gate custom node.

    Directories are checked on every render, since they are cheap and podman
    needs each one. The file writes happen at most once per process per data
    root: a step that succeeded is not repeated, and one that failed is retried
    on a later render (so fixing a permission heals without a restart) but logs
    only once. Each step, and each directory within the mkdir step, fails on
    its own: a read-only store logs and degrades to podman reporting the missing
    path, and one failure never skips the rest.
    """
    root_key = str(comfyui_paths.data_root())
    _run_step(_ensure_data_dirs, root_key)
    for step in (_ensure_extra_model_paths, _sync_gpu_gate_node):
        memo = (root_key, step.__name__)
        if memo not in _provisioned and _run_step(step, root_key):
            _provisioned.add(memo)


def _run_step(step: Callable[[], bool | None], root_key: str) -> bool:
    """Run one provisioning step; True when it settled, False after a failure.

    An OSError is logged (once per distinct failure) rather than raised.
    """
    try:
        settled = step()
    except OSError as exc:
        _warn_once(
            (step.__name__, root_key, type(exc).__name__),
            "comfyui.data_root_provision_failed step=%s root=%s err=%s",
            step.__name__,
            root_key,
            exc,
        )
        return False
    return settled is not False


# ── The provider ──────────────────────────────────────────────────────────────


def _slot_backend(slot_cfg: dict[str, Any]) -> str:
    """The GPU backend token for a ComfyUI slot, from its ``device``.

    The slot's ``device`` is the hardware truth (a converted slot file has no
    ``backend`` key, ADR-0029); a hand-built dict with only ``backend`` keeps
    it, and ComfyUI's one supported backend (ROCm) is the floor.
    """
    from hal0.model_meta import device_to_backend

    nested = slot_cfg.get("slot") if isinstance(slot_cfg.get("slot"), dict) else {}
    device = slot_cfg.get("device") or nested.get("device")
    if isinstance(device, str) and device:
        _recipe, backend = device_to_backend(device)
        if backend:
            return backend
    return str(slot_cfg.get("backend") or nested.get("backend") or "rocm")


def _api_env_setting(key: str) -> str:
    """hal0-api's setting *key*: its environment, else ``/etc/hal0/api.env``.

    hal0-api has ``api.env`` in its environment (systemd ``EnvironmentFile=``),
    but the installer and ``hal0 update`` re-render slot units from processes
    that do not, so the file is read too. The last assignment wins, as in
    systemd. Empty when neither sets it.
    """
    value = os.environ.get(key, "").strip()
    if value:
        return value
    try:
        text = cfg_paths.api_env().read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    found = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, val = line.partition("=")
        if name.strip() != key:
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        found = val.strip()
    return found


def _hal0_api_base() -> str:
    """hal0-api's base URL as seen from the img container (host networking).

    Built from ``HAL0_PORT`` and ``HAL0_BIND_HOST``, the settings hal0-api's
    unit binds with: loopback for a wildcard or loopback bind, the address
    itself for a specific one.
    """
    try:
        port = int(_api_env_setting("HAL0_PORT") or _DEFAULT_API_PORT)
    except ValueError:
        port = _DEFAULT_API_PORT
    if not 0 < port < 65536:
        port = _DEFAULT_API_PORT
    host = _api_env_setting("HAL0_BIND_HOST")
    if host in ("", "0.0.0.0", "127.0.0.1", "localhost") or not _HOST_RE.match(host):
        host = "127.0.0.1"
    elif host in ("::", "::1"):
        host = "[::1]"
    elif ":" in host:
        host = f"[{host}]"
    return f"http://{host}:{port}"


def gate_env() -> dict[str, str]:
    """The ComfyUI container's environment for the hal0_gpu_gate node.

    Where hal0-api answers (its port is an installer option), and the
    fail-open opt-out when the operator set it in ``api.env``. Only a
    normalised ``1`` reaches the unit file, never the raw value.
    """
    base = _hal0_api_base()
    env = {
        _GATE_STATUS_URL_ENV: f"{base}/api/comfyui/arbiter",
        _GATE_SWITCHOVER_URL_ENV: f"{base}/api/comfyui/switchover",
        _GATE_HANDOFF_URL_ENV: f"{base}/api/comfyui/prompt-handoff",
        _GATE_REFUSAL_URL_ENV: f"{base}/api/comfyui/gate-refusal",
    }
    if _api_env_setting(_GATE_FAIL_OPEN_ENV).lower() in _TRUTHY:
        env[_GATE_FAIL_OPEN_ENV] = "1"
    return env


def gate_dial_host() -> str:
    """The host the img container's gate dials hal0-api at, as :func:`gate_env` writes it.

    Loopback for a loopback or wildcard bind, the address itself for a specific
    one (a host name stays a name). It is where the gate's requests come from, so
    it is the peer the auth middleware judges them by.
    """
    return urllib.parse.urlsplit(_hal0_api_base()).hostname or "127.0.0.1"


def gate_sends_credential() -> bool:
    """True iff the gate would present its token on its next request.

    The token is on disk, and the URLs :func:`gate_env` gives the gate name an
    address: the gate holds the token back from a host name. An img container
    started before hal0 minted the token has no mount and sends none either,
    which hal0 cannot see from here (the gate's version header says that).
    """
    return gate_credential.credential_present() and gate_credential.names_an_address(
        gate_env()[_GATE_STATUS_URL_ENV]
    )


def _gate_credential_mount() -> Mount | None:
    """The read-only mount carrying the gate's token, minted on first use.

    ``None`` when the token cannot be written: the gate then runs without a
    credential, as before #3058, which works wherever hal0-api does not
    enforce auth on its requests. The token itself never enters the unit.
    """
    try:
        gate_credential.ensure_token()
    except OSError as exc:
        log.warning(
            "ComfyUI's GPU gate gets no credential: cannot write %s (%s)",
            gate_credential.token_dir(),
            exc,
        )
        return None
    return Mount(str(gate_credential.token_dir()), _GATE_TOKEN_MOUNT, read_only=True)


class ComfyUIProvider(Provider):
    """Provider for ComfyUI (Stable Diffusion family) image generation.

    Health probe: ``GET /system_stats`` returns 200 with ``python_version``
    in the body. The actual model is lazy-loaded on first ``/prompt`` —
    we don't sentinel-generate against it because that would burn 5+ GB
    of VRAM on every readiness check.
    """

    name = "comfyui"

    #: The hal0-runner-comfyui image is a PyTorch-**ROCm** build (see the module
    #: docstring's "Backend default: rocm", ``RUNNER_IMAGES["comfyui"]
    #: .supported_backends == ("rocm",)``, and ``container_spec`` forwarding
    #: ``resolve_gpu_device_paths()`` — which includes ``/dev/kfd``). The
    #: catalog labels img slots ``gpu-vulkan``, but that is the picker's GPU
    #: row, not the image's backend: without the compute node this slot has no
    #: GPU at all (#1941).
    gpu_runtime_needs_rocm = True

    # ── Env / argv ─────────────────────────────────────────────────────────────

    def build_env(
        self,
        slot_cfg: dict[str, Any],
        model_info: dict[str, Any],
    ) -> dict[str, str]:
        port = slot_cfg.get("port") or slot_cfg.get("slot", {}).get("port") or _DEFAULT_PORT
        backend = _slot_backend(slot_cfg)
        # model_info["path"] is the on-disk checkpoint path. The workflow
        # translator picks the checkpoint from the template (see
        # comfyui_workflows.build_workflow). We pass the path through
        # HAL0_COMFYUI_MODEL_PATH for diagnostics / future custom-node
        # integrations.
        model_path = model_info.get("path", "")
        return {
            "HAL0_PORT": str(port),
            "HAL0_BACKEND": str(backend),
            "HAL0_COMFYUI_MODEL_PATH": str(model_path),
            "HAL0_COMFYUI_BASE_DIR": _COMFYUI_BASE_DIR,
            "HAL0_COMFYUI_APP_DIR": _COMFYUI_APP_DIR,
        }

    def start_cmd(self, env: dict[str, str]) -> list[str]:
        """Bare-process argv for ComfyUI invocation outside containers."""
        return [
            "python",
            "main.py",
            "--listen",
            "0.0.0.0",
            "--port",
            env["HAL0_PORT"],
            "--base-directory",
            env.get("HAL0_COMFYUI_BASE_DIR", _COMFYUI_BASE_DIR),
        ]

    # ── Image / container spec ─────────────────────────────────────────────────

    def image_ref(self, slot_cfg: dict[str, Any]) -> str:
        """Resolve the ComfyUI toolbox image.

        Resolution order (§7.1b / ML-4 — now shared with every other
        provider via the runner registry):
          1. ``slot_cfg["image_pin"]`` (top-level or ``[slot]``-nested) —
             the single canonical per-slot escape hatch
             (spec-hw-slot-ownership §3, same contract as
             :func:`hal0.providers.container._resolve_image_ref` and the
             flm/kokoro/moonshine/qwen3tts providers). A non-empty string
             is honored verbatim, never re-resolved (#2172 — this provider
             previously ignored the pin, so a pinned img slot silently
             launched the resolver default).
          2. ``slot_cfg["image"]`` — the pre-pin explicit override from
             slot TOML (still honored; the migration lane folds it into
             ``image_pin``).
          3. ``[slots].default_images["comfyui"]`` — the operator family
             default, the same middle tier
             :func:`hal0.providers.container._resolve_image_ref` honors
             (#2234 — previously skipped here). Below the two per-slot
             tiers above: a box-wide default never outranks a slot's own
             override.
          4. :func:`hal0.runners.resolve_runner_image` on the ``comfyui``
             runner: ``HAL0_RUNNER_IMAGE_COMFYUI`` env var →
             the ``manifest.json`` ``runners.comfyui`` pin.

        """
        from hal0.providers._image import resolve_family_image, slot_image_pin

        pin = slot_image_pin(slot_cfg)
        if pin is not None:
            return pin

        override = slot_cfg.get("image") or slot_cfg.get("slot", {}).get("image")
        # Only a STRING is an image-ref override. In raw slot dicts the
        # [image] TOML section (#599 image-gen settings) shares this key —
        # treating that dict as a ref renders str(dict) into ExecStart and
        # podman fails with 'invalid reference format' (live CT105, Phase D).
        if isinstance(override, str) and override:
            return override

        return resolve_family_image(slot_cfg, "comfyui")

    def _profile_flags(self, slot_cfg: dict[str, Any]) -> str:
        """Resolve the slot's profile to its flag bundle.

        A slot that names a profile gets that profile's flags (``resolved_flags``
        is already MTP-expanded). A name the catalog lacks refuses to launch
        (``slot.profile_missing``, as every llama-server slot does — ADR-0029):
        a typo must not quietly launch a different tune. Only a slot that names
        no profile, or whose profile carries no flags, gets the live-validated
        default bundle.
        """
        profile_name = str(slot_cfg.get("profile") or slot_cfg.get("slot", {}).get("profile") or "")
        if not profile_name:
            return _DEFAULT_PROFILE_FLAGS
        from hal0.providers.container import resolve_named_profile

        flags = resolve_named_profile(slot_cfg, profile_name).resolved_flags.strip()
        return flags or _DEFAULT_PROFILE_FLAGS

    def container_spec(
        self,
        slot_cfg: dict[str, Any],
        model_info: dict[str, Any],
    ) -> RuntimeLaunchPlan:
        """Build a RuntimeLaunchPlan replicating the live-validated CT105 deployment.

        Mirrors `docker inspect comfyui` on CT105 (migration = replicate
        what works):
          * argv: ``bash -lc 'cd /opt/ComfyUI && exec python main.py …'``
            — the image needs the login shell to activate /opt/venv.
          * mounts: ``<data_root>/models → /root/comfy-models`` plus
            output/input/user/custom_nodes into /opt/ComfyUI, and
            extra_model_paths.yaml read-only into the app dir.
          * ``--ipc=host`` — Wan/Hunyuan video models need shared memory;
            host IPC means the container uses the host's /dev/shm directly.
            No ``--shm-size``: podman exits 125 on that combo ("cannot set
            shmsize when running in the host IPC Namespace"), unlike docker,
            which silently ignored it (live CT105, Phase D).
          * host networking — the LXC is the host. The quadlet renderer flips
            the ``--listen 0.0.0.0`` below to ``127.0.0.1`` under host
            networking (``container._loopback_fence_command``), so ComfyUI
            answers on loopback :8188 only; browsers reach its editor through
            hal0 at ``/comfyui/`` (``api/routes/comfyui_proxy.py``, #3049).
          * devices from :func:`resolve_gpu_device_paths` — explicit nodes,
            podman doesn't recurse /dev/dri (same fix class as #674).
        """
        env = self.build_env(slot_cfg, model_info)
        port = int(env["HAL0_PORT"])

        flags = self._profile_flags(slot_cfg)
        payload = (
            f"cd {_COMFYUI_APP_DIR} && exec python main.py --listen 0.0.0.0 --port {port} {flags}"
        ).strip()
        command: list[str] = ["bash", "-lc", payload]

        # ComfyUI's working data tracks the configured model store; hal0.comfyui.paths
        # owns the root (and the HAL0_COMFYUI_DATA_ROOT override), resolved per render.
        provision_data_root()
        data_root = comfyui_paths.data_root()
        # read_only is a first-class Mount flag; extra_model_paths.yaml is the
        # only read-only mount (the renderer appends ":ro").
        mounts: list[Mount] = [
            Mount(str(comfyui_paths.models_root()), _COMFYUI_MODELS_MOUNT),
            Mount(str(data_root / "output"), f"{_COMFYUI_APP_DIR}/output"),
            Mount(str(data_root / "input"), f"{_COMFYUI_APP_DIR}/input"),
            Mount(str(comfyui_paths.user_dir()), f"{_COMFYUI_APP_DIR}/user"),
            Mount(str(comfyui_paths.custom_nodes_dir()), f"{_COMFYUI_APP_DIR}/custom_nodes"),
            Mount(
                str(comfyui_paths.extra_model_paths_file()),
                f"{_COMFYUI_APP_DIR}/extra_model_paths.yaml",
                read_only=True,
            ),
        ]

        env = gate_env()
        gate_mount = _gate_credential_mount()
        if gate_mount is not None:
            mounts.append(gate_mount)
            env[_GATE_TOKEN_FILE_ENV] = f"{_GATE_TOKEN_MOUNT}/{gate_credential.TOKEN_FILE_NAME}"

        # Numeric GIDs for the host's render+video groups (see _gpu.py
        # and llama_server's notes on stock-ubuntu /etc/group).
        group_add: list[str] = [str(gid) for gid in resolve_gpu_group_ids()]

        return RuntimeLaunchPlan(
            image=self.image_ref(slot_cfg),
            command=command,
            env=env,
            mounts=mounts,
            devices=resolve_gpu_device_paths(),
            cap_add=[],
            security_opt=["seccomp=unconfined", "apparmor=unconfined", "label=disable"],
            group_add=group_add,
            port=port,
            network_mode="host",
            # Host /dev/shm for Wan/Hunyuan video (see the docstring above).
            ipc="host",
        )

    # ── Health ─────────────────────────────────────────────────────────────────

    async def wait_ready(self, port: int) -> None:
        """Poll ``/system_stats`` until healthy or the container deadline passes.

        Mirrors :meth:`ContainerProvider.wait_ready`'s loop but through THIS
        provider's health probe: ComfyUI ships no llama-style ``/health``, so
        the generic container waiter 404-polled for the full 180 s and the
        slot wedged in WARMING with a perfectly healthy server behind it
        (every ``/v1/images/generations`` then 409'd on warming → starting).
        """
        from hal0.providers.container import _HEALTH_POLL_INTERVAL_S, _HEALTH_TIMEOUT_S

        deadline = asyncio.get_event_loop().time() + _HEALTH_TIMEOUT_S
        while asyncio.get_event_loop().time() < deadline:
            h = await self.health(port)
            if h.get("ok"):
                return
            await asyncio.sleep(_HEALTH_POLL_INTERVAL_S)
        raise TimeoutError(f"comfyui slot port {port} did not become healthy")

    async def health(self, port: int) -> dict[str, Any]:
        """Health probe: GET /system_stats returns 200 with python_version.

        We deliberately skip a sentinel-generation probe — it would burn
        VRAM on every readiness check and Stable Diffusion checkpoint
        loads are expensive (5-10 s for SDXL on Strix Halo). The first
        ``/v1/images/generations`` call eats the cold-load cost; from
        there it's warm.
        """
        url = f"http://127.0.0.1:{port}/system_stats"
        try:
            async with async_client(timeout=_HEALTH_TIMEOUT) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return {
                        "ok": False,
                        "status": f"http_{resp.status_code}",
                        "detail": resp.text[:200],
                    }
                try:
                    body = resp.json()
                except Exception:
                    return {"ok": False, "status": "system_stats_unparseable"}
                # ComfyUI's /system_stats body: {"system": {"python_version": "...", ...}, ...}.
                # Older versions return a flatter shape — accept either.
                python_version = ""
                if isinstance(body, dict):
                    sys_block = body.get("system")
                    if isinstance(sys_block, dict):
                        python_version = str(sys_block.get("python_version", "") or "")
                    if not python_version:
                        python_version = str(body.get("python_version", "") or "")
                if not python_version:
                    return {
                        "ok": False,
                        "status": "system_stats_missing_python_version",
                        "detail": str(body)[:200],
                    }
                return {"ok": True, "status": "ready", "python_version": python_version}
        except httpx.HTTPError as exc:
            return {"ok": False, "status": "http_error", "detail": str(exc)}
        except Exception as exc:
            return {"ok": False, "status": "exception", "detail": str(exc)}

    # ── Inference ──────────────────────────────────────────────────────────────

    async def infer(self, port: int, body: dict[str, Any]) -> dict[str, Any]:
        """Translate the OpenAI body to a ComfyUI workflow, run it, return PNG bytes.

        Returned envelope::

            {
                "images": [
                    {"png": <bytes>, "filename": "...", "subfolder": "", "type": "output"},
                    ...
                ],
                "meta": {... debug params from the translator ...}
            }

        The ``/v1/images/generations`` route adapts this envelope to the
        OpenAI response shape (``url`` vs ``b64_json``).

        The body must carry hal0-injected fields:
          * ``_hal0_template``       — workflow template stem to render.
          * ``_hal0_ckpt_override``  — optional checkpoint filename, for the
            templates that let the caller choose one.
          * ``_hal0_model``          — the id the route resolved the request's
            ``model`` to (a catalog id or ``ckpt/<path>``). It only labels the
            render in :mod:`hal0.comfyui.progress`; absent, the request's own
            ``model`` (trimmed) is used, then the template stem.

        These are stripped from what we send to ComfyUI and are how the
        v1 route hands the resolved template down without making this
        provider re-look-up the catalog.

        While the workflow runs, a websocket listener follows the render's step
        progress into :mod:`hal0.comfyui.progress` (shown on
        ``/api/comfyui/status``). It connects before the prompt is submitted, so no
        early frame is missed, and is cancelled when this method leaves. It never
        decides the outcome: the render succeeds or fails on ``/history`` alone.
        """
        template = body.pop("_hal0_template", None)
        ckpt_override = body.pop("_hal0_ckpt_override", None)
        resolved_model = body.pop("_hal0_model", None)
        if ckpt_override is not None and not isinstance(ckpt_override, str):
            raise ComfyUIInferError(
                "comfyui.infer: _hal0_ckpt_override must be a filename string",
                details={"port": port},
            )
        if not isinstance(template, str) or not template:
            raise ComfyUIInferError(
                "comfyui.infer requires _hal0_template to be set "
                "(passed by the /v1/images/generations route from the catalog entry)",
                details={"port": port},
            )

        request_tag = f"hal0-{uuid.uuid4().hex[:12]}"
        graph, debug_meta = build_workflow(
            body=body,
            template=template,
            ckpt_override=ckpt_override or None,
            request_tag=request_tag,
        )
        client_id = uuid.uuid4().hex
        prompt_payload = {"prompt": graph, "client_id": client_id}

        base_url = f"http://127.0.0.1:{port}"
        # ComfyUI sends a render's progress only to the client_id that submitted it, so the
        # socket is opened first (under that id) and the prompt submitted after.
        relay = _ProgressRelay(
            resolved_model
            if isinstance(resolved_model, str) and resolved_model
            else str(body.get("model") or "").strip() or template
        )
        socket_ready = asyncio.Event()
        listener = asyncio.create_task(
            _follow_progress(_ws_url(base_url, client_id), relay, socket_ready),
            name=f"comfyui-progress-{client_id[:8]}",
        )
        try:
            await _wait_until_open(socket_ready)
            async with async_client(timeout=_INFER_TIMEOUT) as client:
                # 1. Submit workflow.
                submit_resp = await client.post(f"{base_url}/prompt", json=prompt_payload)
                if submit_resp.status_code != 200:
                    # The GPU gate in front of ComfyUI refuses a prompt hal0 did not hand
                    # the GPU to, with a 503 in its own shape: that is hal0's to report.
                    refusal = gate_refusal_reason(
                        submit_resp.status_code, _json_or_none(submit_resp)
                    )
                    if refusal is not None:
                        raise ImageGateRefused(refusal, details={"port": port})
                    raise ComfyUIInferError(
                        f"comfyui POST /prompt returned HTTP {submit_resp.status_code}",
                        details={
                            "port": port,
                            "status_code": submit_resp.status_code,
                            "body": submit_resp.text[:500],
                        },
                    )
                submitted = submit_resp.json()
                prompt_id = submitted.get("prompt_id")
                if not prompt_id:
                    raise ComfyUIInferError(
                        "comfyui POST /prompt returned no prompt_id",
                        details={"port": port, "response": submitted},
                    )
                relay.claim(str(prompt_id))

                # 2. Poll history for completion.
                outputs = await self._await_history(client, base_url, str(prompt_id))
                # ComfyUI is done with it: a newer render must not read as behind this one
                # while the images download. The finally below still covers every failure.
                relay.release()

                # 3. Fetch output PNG bytes.
                images: list[dict[str, Any]] = []
                for node_id, node_outputs in outputs.items():
                    for img in node_outputs.get("images", []) or []:
                        if not isinstance(img, dict):
                            continue
                        filename = img.get("filename")
                        if not filename:
                            continue
                        subfolder = img.get("subfolder", "") or ""
                        img_type = img.get("type", "output") or "output"
                        png_bytes = await self._fetch_view(
                            client,
                            base_url,
                            filename=str(filename),
                            subfolder=str(subfolder),
                            type_=str(img_type),
                        )
                        images.append(
                            {
                                "png": png_bytes,
                                "filename": str(filename),
                                "subfolder": str(subfolder),
                                "type": str(img_type),
                                "node_id": str(node_id),
                            }
                        )
                if not images:
                    raise ComfyUIInferError(
                        "comfyui completed the workflow but produced no images",
                        details={"port": port, "prompt_id": prompt_id, "outputs": outputs},
                    )
                return {"images": images, "meta": debug_meta, "prompt_id": prompt_id}
        except ComfyUIInferError:
            raise
        except httpx.HTTPStatusError as exc:
            raise ComfyUIInferError(
                f"comfyui returned HTTP {exc.response.status_code}",
                details={"port": port, "status_code": exc.response.status_code},
            ) from exc
        except httpx.HTTPError as exc:
            raise ComfyUIInferError(
                f"comfyui transport error: {exc}",
                details={"port": port},
            ) from exc
        finally:
            # However the render ended, it is no longer live. The tracker entry goes first
            # (synchronously), then the listener is cancelled and awaited so no task
            # outlives the request. gather() keeps a cancellation of infer itself intact.
            relay.release()
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)

    async def _await_history(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        prompt_id: str,
    ) -> dict[str, Any]:
        """Poll GET /history/<prompt_id> until the entry exists and is complete.

        Returns the ``outputs`` dict from the history entry. Raises
        :class:`ComfyUIInferError` on workflow validation failure (the
        history entry carries a ``status`` block with ``status_str``
        and ``messages`` describing the failure) or on the global
        timeout.
        """
        deadline = time.monotonic() + _HISTORY_POLL_TIMEOUT_S
        url = f"{base_url}/history/{prompt_id}"
        last_status: str = ""
        while time.monotonic() < deadline:
            resp = await client.get(url)
            if resp.status_code != 200:
                # /history returns 200 + {} until the prompt registers; a
                # non-200 here means ComfyUI itself fell over.
                raise ComfyUIInferError(
                    f"comfyui GET /history returned HTTP {resp.status_code}",
                    details={"prompt_id": prompt_id, "status_code": resp.status_code},
                )
                # (no fallthrough; raise above)
            history = resp.json()
            entry = history.get(prompt_id) if isinstance(history, dict) else None
            if isinstance(entry, dict):
                status = entry.get("status") or {}
                last_status = str(status.get("status_str", "") or "")
                completed = bool(status.get("completed", False))
                if last_status == "error":
                    messages = status.get("messages") or []
                    raise ComfyUIInferError(
                        "comfyui workflow execution failed",
                        details={
                            "prompt_id": prompt_id,
                            "status_str": last_status,
                            "messages": messages,
                        },
                    )
                if completed:
                    outputs = entry.get("outputs") or {}
                    if not isinstance(outputs, dict):
                        raise ComfyUIInferError(
                            "comfyui history entry has malformed outputs",
                            details={"prompt_id": prompt_id, "outputs": outputs},
                        )
                    return outputs
            await asyncio.sleep(_HISTORY_POLL_INTERVAL_S)
        raise ComfyUIInferError(
            f"comfyui workflow {prompt_id} did not complete within "
            f"{int(_HISTORY_POLL_TIMEOUT_S)}s (last status={last_status!r})",
            details={"prompt_id": prompt_id, "timeout_s": _HISTORY_POLL_TIMEOUT_S},
        )

    async def _fetch_view(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        *,
        filename: str,
        subfolder: str,
        type_: str,
    ) -> bytes:
        """GET /view?filename=...&subfolder=...&type=output → PNG bytes."""
        params = {"filename": filename, "subfolder": subfolder, "type": type_}
        resp = await client.get(f"{base_url}/view", params=params)
        if resp.status_code != 200:
            raise ComfyUIInferError(
                f"comfyui GET /view returned HTTP {resp.status_code}",
                details={
                    "filename": filename,
                    "subfolder": subfolder,
                    "type": type_,
                    "status_code": resp.status_code,
                    "body": resp.text[:200],
                },
            )
        return resp.content


__all__ = [
    "ComfyUIHealthError",
    "ComfyUIInferError",
    "ComfyUIProvider",
    "ImageGateRefused",
    "gate_dial_host",
    "gate_refusal_reason",
    "gate_sends_credential",
]
