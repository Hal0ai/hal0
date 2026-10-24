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
import logging
import time
import uuid
from typing import Any

import httpx

from hal0.comfyui import paths as comfyui_paths
from hal0.errors import Hal0Error
from hal0.http_client import async_client
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
# and synced into custom_nodes/ at every container launch.
_GPU_GATE_NODE = "hal0_gpu_gate.py"

# Live-validated flag bundle (matches the "comfyui" seed profile). Used
# when the slot has no resolvable profile.
_DEFAULT_PROFILE_FLAGS = "--disable-mmap --bf16-vae --cache-none"

# Default port — ComfyUI's stock listen port.
_DEFAULT_PORT = 8188


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


# ── Typed errors ──────────────────────────────────────────────────────────────


class ComfyUIHealthError(Hal0Error):
    """ComfyUI health probe failed."""

    code = "slot.not_ready"
    status = 503


class ComfyUIInferError(Hal0Error):
    """ComfyUI inference call failed."""

    code = "dispatch.upstream_failed"
    status = 502


# ── Data-root provisioning ────────────────────────────────────────────────────


def render_extra_model_paths() -> str:
    """The ``extra_model_paths.yaml`` that maps ComfyUI's model types onto the mounted models dir.

    One ``<type>: <dir>`` line per :data:`hal0.comfyui.paths.SUBDIRS` entry, in
    sorted order, under ``base_path`` (the in-container models mount).
    """
    entries = (f"    {key}: {comfyui_paths.SUBDIRS[key]}" for key in sorted(comfyui_paths.SUBDIRS))
    return "\n".join(["comfyui:", f"    base_path: {_COMFYUI_MODELS_MOUNT}", *entries]) + "\n"


def _ensure_data_dirs() -> None:
    """Create every directory the container spec bind-mounts (and ``workflows``).

    The bind-mount sources come first, and each mkdir fails on its own: one
    blocked directory must not leave another bind-mount source missing.
    """
    root = comfyui_paths.data_root()
    dirs = [
        comfyui_paths.models_root(),
        root / "output",
        root / "input",
        comfyui_paths.user_dir(),
        comfyui_paths.custom_nodes_dir(),
        comfyui_paths.workflows_dir(),
        *(comfyui_paths.models_root() / sub for sub in comfyui_paths.SUBDIRS.values()),
    ]
    for directory in dirs:
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log.warning(
                "comfyui.data_root_provision_failed step=mkdir path=%s err=%s", directory, exc
            )


def _ensure_extra_model_paths() -> None:
    """Write the model-paths file unless the operator already put content in it.

    An empty file hides every model from ComfyUI, so absent or blank (after
    strip) means "write ours"; anything else is the operator's and stays.
    """
    path = comfyui_paths.extra_model_paths_file()
    try:
        if path.read_bytes().strip():
            return
    except FileNotFoundError:
        pass
    path.write_text(render_extra_model_paths(), encoding="utf-8")


def _sync_gpu_gate_node() -> None:
    """Copy the packaged GPU-gate node into ``custom_nodes/`` when it is missing or differs."""
    shipped = importlib.resources.files("hal0.comfyui.assets").joinpath(_GPU_GATE_NODE)
    wanted = shipped.read_bytes()
    dest = comfyui_paths.custom_nodes_dir() / _GPU_GATE_NODE
    try:
        if dest.read_bytes() == wanted:
            return
    except FileNotFoundError:
        pass
    dest.write_bytes(wanted)


def provision_data_root() -> None:
    """Make the ComfyUI data root ready to bind-mount; idempotent and best-effort.

    Podman refuses a bind mount whose host path is absent (statfs, exit 125),
    which crash-looped the img slot on any box where the tree hadn't been
    provisioned (fresh install, or a store root that moved after install). So
    this creates the mounted directories, writes ``extra_model_paths.yaml`` if
    it is absent or empty, and syncs the GPU-gate custom node. Each step, and
    each directory within the mkdir step, fails on its own: a read-only store
    logs and degrades to podman reporting the missing path, and one failure
    never skips the rest.
    """
    for step in (_ensure_data_dirs, _ensure_extra_model_paths, _sync_gpu_gate_node):
        try:
            step()
        except OSError as exc:
            log.warning(
                "comfyui.data_root_provision_failed step=%s root=%s err=%s",
                step.__name__,
                comfyui_paths.data_root(),
                exc,
            )


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
        # model_info["path"] is the on-disk checkpoint path. The actual
        # filename gets picked up by the workflow translator (see
        # comfyui_workflows.build_workflow's ckpt_filename arg). We pass
        # the path through HAL0_COMFYUI_MODEL_PATH for diagnostics /
        # future custom-node integrations.
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
          * host networking — the LXC is the host; the ComfyUI web UI must
            stay LAN-reachable on :8188 (pre-migration behavior).
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

        # Numeric GIDs for the host's render+video groups (see _gpu.py
        # and llama_server's notes on stock-ubuntu /etc/group).
        group_add: list[str] = [str(gid) for gid in resolve_gpu_group_ids()]

        return RuntimeLaunchPlan(
            image=self.image_ref(slot_cfg),
            command=command,
            env={},
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
          * ``_hal0_model_class``    — curated entry's model_class.
          * ``_hal0_ckpt_filename``  — filename inside ComfyUI's checkpoints dir.

        These are stripped from what we send to ComfyUI and are how the
        v1 route hands the curated metadata down without making this
        provider re-look-up the registry.
        """
        model_class = body.pop("_hal0_model_class", None)
        ckpt_filename = body.pop("_hal0_ckpt_filename", None)
        if not isinstance(ckpt_filename, str) or not ckpt_filename:
            raise ComfyUIInferError(
                "comfyui.infer requires _hal0_ckpt_filename to be set "
                "(passed by the /v1/images/generations route from the curated entry)",
                details={"port": port},
            )

        request_tag = f"hal0-{uuid.uuid4().hex[:12]}"
        graph, debug_meta = build_workflow(
            body=body,
            model_class=model_class if isinstance(model_class, str) else None,
            ckpt_filename=ckpt_filename,
            request_tag=request_tag,
        )
        client_id = uuid.uuid4().hex
        prompt_payload = {"prompt": graph, "client_id": client_id}

        base_url = f"http://127.0.0.1:{port}"
        try:
            async with async_client(timeout=_INFER_TIMEOUT) as client:
                # 1. Submit workflow.
                submit_resp = await client.post(f"{base_url}/prompt", json=prompt_payload)
                if submit_resp.status_code != 200:
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

                # 2. Poll history for completion.
                outputs = await self._await_history(client, base_url, str(prompt_id))

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
]
