"""ComfyUIProvider — Stable-Diffusion-family image generation backend.

ComfyUI is a node-graph stable-diffusion runtime. We treat it as a
black box that speaks ``POST /prompt`` (submit a graph) + ``GET /history/<id>``
(poll for completion) + ``GET /view`` (download an output PNG). The
OpenAI-shaped ``POST /v1/images/generations`` request is translated to a
parametric workflow (see :mod:`hal0.providers.comfyui_workflows`) and the
result PNG bytes are unwrapped back into the OpenAI response shape.

Toolbox image:    docker.io/kyuz0/amd-strix-halo-comfyui (manifest digest pin
                  is the primary path; the :latest tag is the last-resort
                  fallback). The kyuz0 image ships ComfyUI checked out at
                  /opt/ComfyUI with its venv at /opt/venv — NOT /app.
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
import logging
import os
import time
import uuid
from typing import Any

import httpx

from hal0.errors import Hal0Error
from hal0.providers._gpu import resolve_gpu_device_paths, resolve_gpu_group_ids
from hal0.providers.base import ContainerSpec, Provider
from hal0.providers.comfyui_workflows import build_workflow

log = logging.getLogger(__name__)


# ── Toolbox image ──────────────────────────────────────────────────────────────
# Last-resort fallback only — the manifest.json digest pin is the primary
# path (see image_ref). Points at the kyuz0 Strix Halo build that the live
# CT105 deployment validated.
_HAL0_COMFYUI_IMAGE = "docker.io/kyuz0/amd-strix-halo-comfyui:latest"

# In-container app + working-data layout. The kyuz0 image has ComfyUI
# checked out at /opt/ComfyUI (venv at /opt/venv). Host-side working data
# (models / output / input / user / custom_nodes + extra_model_paths.yaml)
# lives under _COMFYUI_DATA_ROOT and is bind-mounted in so weights and
# custom nodes persist across container restarts.
_COMFYUI_APP_DIR = "/opt/ComfyUI"
_COMFYUI_BASE_DIR = "/var/lib/hal0/comfyui"

# Host data root for the bind mounts. Overridable for tests / non-standard
# installs via HAL0_COMFYUI_DATA_ROOT.
_COMFYUI_DATA_ROOT = "/mnt/ai-models/comfyui"

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


# ── The provider ──────────────────────────────────────────────────────────────


class ComfyUIProvider(Provider):
    """Provider for ComfyUI (Stable Diffusion family) image generation.

    Health probe: ``GET /system_stats`` returns 200 with ``python_version``
    in the body. The actual model is lazy-loaded on first ``/prompt`` —
    we don't sentinel-generate against it because that would burn 5+ GB
    of VRAM on every readiness check.
    """

    name = "comfyui"

    # ── Env / argv ─────────────────────────────────────────────────────────────

    def build_env(
        self,
        slot_cfg: dict[str, Any],
        model_info: dict[str, Any],
    ) -> dict[str, str]:
        port = slot_cfg.get("port") or slot_cfg.get("slot", {}).get("port") or _DEFAULT_PORT
        backend = slot_cfg.get("backend") or slot_cfg.get("slot", {}).get("backend") or "rocm"
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

        Resolution order (matches ``llama_server.image_ref``):
          1. ``slot_cfg["image"]`` — explicit override from slot TOML.
          2. ``HAL0_TOOLBOX_IMAGE_COMFYUI`` env var.
          3. ``manifest.json`` digest pin (primary path).
          4. The fallback tag ``docker.io/kyuz0/amd-strix-halo-comfyui:latest``.
        """
        override = slot_cfg.get("image") or slot_cfg.get("slot", {}).get("image")
        # Only a STRING is an image-ref override. In raw slot dicts the
        # [image] TOML section (#599 image-gen settings) shares this key —
        # treating that dict as a ref renders str(dict) into ExecStart and
        # podman fails with 'invalid reference format' (live CT105, Phase D).
        if isinstance(override, str) and override:
            return override
        env_override = os.environ.get("HAL0_TOOLBOX_IMAGE_COMFYUI", "").strip()
        if env_override:
            return env_override
        # Manifest pin — best-effort. Loader is wrapped to swallow any
        # IO/parse errors so a missing or stale manifest never breaks the
        # provider.
        try:
            from hal0.config.loader import manifest_image_ref

            pinned = manifest_image_ref("comfyui")
            if pinned:
                return pinned
        except Exception:
            pass
        return _HAL0_COMFYUI_IMAGE

    def _profile_flags(self, slot_cfg: dict[str, Any]) -> str:
        """Resolve the slot's profile to its flag bundle.

        Same lookup as the llama-server path in
        :mod:`hal0.providers.container` (resolve_profile +
        resolve_profile_flags). Falls back to the live-validated default
        bundle when the slot has no profile or the lookup fails — the img
        slot must always come up with the bench-tuned flags.
        """
        profile_name = str(slot_cfg.get("profile") or slot_cfg.get("slot", {}).get("profile") or "")
        if profile_name:
            try:
                from hal0.config.loader import resolve_profile
                from hal0.config.schema import resolve_profile_flags

                flags = resolve_profile_flags(resolve_profile(profile_name)).strip()
                if flags:
                    return flags
            except Exception:
                log.warning(
                    "comfyui.profile_lookup_failed; using default flags",
                    extra={"profile": profile_name},
                )
        return _DEFAULT_PROFILE_FLAGS

    def container_spec(
        self,
        slot_cfg: dict[str, Any],
        model_info: dict[str, Any],
    ) -> ContainerSpec:
        """Build a ContainerSpec replicating the live-validated CT105 deployment.

        Mirrors `docker inspect comfyui` on CT105 (migration = replicate
        what works):
          * argv: ``bash -lc 'cd /opt/ComfyUI && exec python main.py …'``
            — the kyuz0 image needs the login shell to activate /opt/venv.
          * mounts: ``<data_root>/models → /root/comfy-models`` plus
            output/input/user/custom_nodes into /opt/ComfyUI, and
            extra_model_paths.yaml read-only into the app dir.
          * ``--ipc=host --shm-size=8g`` — Wan/Hunyuan video models need
            shared memory.
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

        data_root = os.environ.get("HAL0_COMFYUI_DATA_ROOT", "").strip() or _COMFYUI_DATA_ROOT
        # ":ro" suffix on the dst is how ContainerSpec expresses read-only
        # mounts (_render_unit_from_spec emits --volume={src}:{dst} verbatim).
        mounts: list[tuple[str, str]] = [
            (f"{data_root}/models", "/root/comfy-models"),
            (f"{data_root}/output", f"{_COMFYUI_APP_DIR}/output"),
            (f"{data_root}/input", f"{_COMFYUI_APP_DIR}/input"),
            (f"{data_root}/user", f"{_COMFYUI_APP_DIR}/user"),
            (f"{data_root}/custom_nodes", f"{_COMFYUI_APP_DIR}/custom_nodes"),
            (
                f"{data_root}/extra_model_paths.yaml",
                f"{_COMFYUI_APP_DIR}/extra_model_paths.yaml:ro",
            ),
        ]

        # Numeric GIDs for the host's render+video groups (see _gpu.py
        # and llama_server's notes on stock-ubuntu /etc/group).
        group_add: list[str] = [str(gid) for gid in resolve_gpu_group_ids()]

        return ContainerSpec(
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
            extra_args=["--ipc=host", "--shm-size=8g"],
        )

    # ── Health ─────────────────────────────────────────────────────────────────

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
            async with httpx.AsyncClient(timeout=_HEALTH_TIMEOUT) as client:
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
            async with httpx.AsyncClient(timeout=_INFER_TIMEOUT) as client:
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
    "_HAL0_COMFYUI_IMAGE",
    "ComfyUIHealthError",
    "ComfyUIInferError",
    "ComfyUIProvider",
]
