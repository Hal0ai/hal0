"""Qwen3TTSProvider — GPU (ROCm) multilingual TTS inference backend.

The GPU sibling of :class:`~hal0.providers.kokoro.KokoroProvider`.  Both
implement the same OpenAI ``/v1/audio/speech`` contract so a ``type=tts``
slot can be served by either engine; this one runs the multilingual
Qwen3-TTS CustomVoice model on the Strix Halo iGPU instead of Kokoro's
CPU ONNX path.

Image API surface (packaging/toolbox/qwen3tts.Dockerfile):
  ENTRYPOINT: python3 /opt/qwen3tts/qwen3tts_server.py
  CMD:        --help

  The server is the ENTRYPOINT, so ``container_spec.command`` carries
  flags only — no binary path or subcommand prefix.  Flags come from the
  resolved ``tts-qwen3`` profile (profiles.toml), which bakes
  ``--model_path`` plus the default voice/language.

GPU passthrough (mirrors the llama-server / agent slot path):
  Unlike Kokoro, Qwen3-TTS runs on ROCm, so the spec emits the GPU device
  nodes (/dev/kfd + /dev/dri/*) and the numeric render/video GIDs via the
  shared :mod:`hal0.providers._gpu` helpers — the same passthrough the
  GPU llama slots use.

  PERF GOTCHA: run on NATIVE gfx1151.  We deliberately do NOT set
  ``HSA_OVERRIDE_GFX_VERSION`` — overriding to gfx1100 forces slow MIOpen
  GEMM fallbacks (~9.5x realtime vs ~2.1x realtime native).

Writable cache mount:
  MIOpen's user kernel DB and the HuggingFace codec/tokenizer cache must be
  writable and survive restarts, so a host cache dir is bind-mounted at
  ``/cache`` (read-write) and the MIOpen env points at it.  The model store
  itself stays read-only (identical-path, same as every other slot).

Self-managed weights:
  Qwen3-TTS weights are operator-staged under /mnt/ai-models (not
  hal0-registry-managed), so the provider name is in the slot subsystem's
  ``SELF_MANAGED_PROVIDERS`` set.
"""

from __future__ import annotations

import os
import shlex
from typing import Any

import httpx

from hal0.config import store as model_store_module
from hal0.config.paths import model_store_root
from hal0.errors import Hal0Error
from hal0.providers._gpu import resolve_gpu_device_paths, resolve_gpu_group_ids
from hal0.providers.base import ContainerSpec, Mount, Provider
from hal0.runners import RUNNER_IMAGES

# Sourced from the runner-image registry (§7.1b / ML-4) so the literal tag
# lives in exactly one place (hal0.runners.RUNNER_IMAGES["qwen3tts"].image).
# Kept as a module attribute for back-compat imports.
_DEFAULT_QWEN3TTS_IMAGE = RUNNER_IMAGES["qwen3tts"].image

# Default profile name if the slot TOML omits one.
_DEFAULT_PROFILE = "tts-qwen3"

# Default slot port (slot TOML normally pins this; mirrors the live
# standalone deployment's 8095 so a profile-less default lands where the
# Hermes bridge already expects it).
_DEFAULT_PORT = 8095

# Host directory bind-mounted read-write at /cache inside the container for
# the MIOpen user kernel DB + HuggingFace cache. Overridable for dev/test.
_DEFAULT_CACHE_DIR = "/var/lib/hal0/qwen3tts-cache"

# Health/infer timeouts (GPU synth is slow — give the read leg headroom).
_HEALTH_TIMEOUT = httpx.Timeout(5.0)
_INFER_TIMEOUT = httpx.Timeout(connect=5.0, read=120.0, write=5.0, pool=5.0)


class Qwen3TTSHealthError(Hal0Error):
    """Qwen3-TTS health probe failed."""

    code = "slot.not_ready"
    status = 503


class Qwen3TTSInferError(Hal0Error):
    """Qwen3-TTS inference call failed."""

    code = "dispatch.upstream_failed"
    status = 502


def _cache_dir() -> str:
    """Resolve the host cache dir bind-mounted at /cache (env-overridable)."""
    return os.environ.get("HAL0_QWEN3TTS_CACHE", _DEFAULT_CACHE_DIR)


class Qwen3TTSProvider(Provider):
    """Provider for the GPU (ROCm) Qwen3-TTS CustomVoice backend.

    GPU passthrough + writable /cache mount, otherwise the same shape as
    :class:`~hal0.providers.kokoro.KokoroProvider`.  The container image
    wraps ``qwen3tts_server.py`` which implements OpenAI-compat
    ``POST /v1/audio/speech``, ``GET /v1/models``, and ``GET /health``.

    Primary deployment path is ``container_spec`` → ``_render_quadlet_from_plan``
    (same pattern as KokoroProvider / FLMProvider).  ``build_env`` /
    ``start_cmd`` are informational stubs kept for ABC compliance.
    """

    name = "qwen3tts"

    # ── Provider ABC stubs ─────────────────────────────────────────────────────

    def build_env(
        self,
        slot_cfg: dict[str, Any],
        model_info: dict[str, Any],
    ) -> dict[str, str]:
        """Informational env block (container is self-contained)."""
        return {
            "HAL0_SLOT": str(slot_cfg.get("name", "")),
            "HAL0_RUNTIME": "container",
            "HAL0_PROFILE": str(slot_cfg.get("profile") or _DEFAULT_PROFILE),
        }

    def start_cmd(self, env: dict[str, str]) -> list[str]:
        """Not applicable — systemd starts the container."""
        raise NotImplementedError("Qwen3TTSProvider uses systemd; start_cmd() is unused")

    # ── Image / container spec ─────────────────────────────────────────────────

    def image_ref(self, slot_cfg: dict[str, Any]) -> str:
        """Return the Qwen3-TTS toolbox image reference.

        Resolution (§7.1b / ML-4): ``slot_cfg["image"]`` (top-level or
        ``[slot]``-nested string override) → the runner registry
        (``HAL0_TOOLBOX_IMAGE_QWEN3TTS`` env override → the manifest digest
        pin → the bundled default) — see
        :func:`hal0.runners.resolve_runner_image`. Previously this was
        env-only; :meth:`container_spec` (the actual call site — this
        method has no live caller today) used ``profile.image`` directly
        and never consulted this method or ``slot.image`` at all.
        """
        override: Any = None
        if isinstance(slot_cfg, dict):
            override = slot_cfg.get("image")
            if not (isinstance(override, str) and override):
                nested = slot_cfg.get("slot")
                override = nested.get("image") if isinstance(nested, dict) else None
        if isinstance(override, str) and override:
            return override

        from hal0.runners import get_runner, resolve_runner_image

        return resolve_runner_image(get_runner("qwen3tts"))

    def container_spec(
        self,
        slot_cfg: dict[str, Any],
        model_info: dict[str, Any],
    ) -> ContainerSpec:
        """Build a ContainerSpec for the GPU Qwen3-TTS slot.

        The toolbox image ENTRYPOINT is ``python3 qwen3tts_server.py``, so
        ``command`` carries only flags (no binary path / subcommand).

        Flags come from the resolved profile (``tts-qwen3`` by default),
        which bakes ``--model_path`` + ``--default_voice`` + ``--default_language``.
        ``--host`` and ``--port`` are always appended so the operator cannot
        accidentally omit them (argparse last-wins, so the slot's --port beats
        any --port baked into profile flags).

        Unlike Kokoro this emits GPU device nodes + render/video GIDs (the
        same passthrough the llama GPU slots use), bakes the MIOpen cache env,
        and bind-mounts a writable /cache dir.  ``HSA_OVERRIDE_GFX_VERSION``
        is intentionally NOT set (native gfx1151 — see module docstring).

        Security opts (apparmor/seccomp=unconfined) are required for
        Proxmox LXC deployments (same rationale as the other providers).
        """
        from hal0.profiles import ProfileCatalog

        port = int(slot_cfg.get("port") or _DEFAULT_PORT)
        profile_name: str = str(slot_cfg.get("profile") or _DEFAULT_PROFILE)
        profile = ProfileCatalog().resolve(profile_name)
        # ``resolved_flags`` includes --model_path + voice/language defaults.
        flag_tokens = shlex.split(profile.resolved_flags) if profile.resolved_flags.strip() else []

        # command = profile flags + mandatory server binding args.
        command: list[str] = [
            *flag_tokens,
            "--host",
            "0.0.0.0",
            "--port",
            str(port),
        ]

        # Effective model-store root ([models].store / HAL0_MODEL_STORE,
        # default /mnt/ai-models), mounted identical-path read-only so the
        # profile-baked --model_path resolves inside the container.
        store_root = model_store_root()
        cache_dir = _cache_dir()

        return ContainerSpec(
            image=self.image_ref(slot_cfg),
            command=command,
            # MIOpen user DB + custom kernel cache on the writable /cache mount;
            # FAST find-mode avoids the multi-minute exhaustive GEMM search.
            env={
                "MIOPEN_USER_DB_PATH": "/cache/miopen",
                "MIOPEN_CUSTOM_CACHE_DIR": "/cache/miopen",
                "MIOPEN_FIND_MODE": "FAST",
            },
            mounts=[
                # Model store via the shared `mount_for` factory (ML-3) —
                # omits the SELinux relabel on NFS instead of unconditionally
                # appending ``:z`` (chcon ENOTSUP there).
                model_store_module.mount_for(store_root, read_only=True),
                # Writable cache: MIOpen DB + HF codec/tokenizer cache.
                Mount(cache_dir, "/cache", read_only=False, selinux="z"),
            ],
            # GPU passthrough mirrors the llama-server / agent slot path.
            devices=list(resolve_gpu_device_paths()),
            cap_add=[],
            # Required for Proxmox LXC container deployments.
            security_opt=["apparmor=unconfined", "seccomp=unconfined"],
            # Numeric render/video GIDs (toolbox images lack the group names).
            group_add=[str(g) for g in resolve_gpu_group_ids()],
            port=port,
            # Port-mapped (not host networking) so it can coexist with the
            # Kokoro TTS slot. the Quadlet renderer derives
            # --publish=127.0.0.1:<port>:<port> from spec.port.
            network_mode="",
            extra_args=[],
        )

    # ── Health / infer ─────────────────────────────────────────────────────────

    async def health(self, port: int) -> dict[str, Any]:
        """Probe GET /health on the qwen3tts-server port.

        NOTE: dead code in the container deployment path — slot health checks
        go through :meth:`ContainerProvider.health` (which implements the same
        ``model_loaded`` gating).  Kept because ``health`` is abstract on the
        Provider ABC: removing it would make Qwen3TTSProvider abstract and
        break the ``_spec_provider_for`` instantiation in container.py.

        qwen3tts_server.py returns {status: "ok", model_loaded: true} when
        ready.  Returns {"ok": bool, "status": str}.
        """
        url = f"http://127.0.0.1:{port}/health"
        try:
            async with httpx.AsyncClient(timeout=_HEALTH_TIMEOUT) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    body = resp.json()
                    loaded = bool(body.get("model_loaded"))
                    return {
                        "ok": loaded,
                        "status": "ready" if loaded else "loading",
                    }
                return {"ok": False, "status": f"http_{resp.status_code}"}
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            return {"ok": False, "status": str(exc)}
        except Exception as exc:
            return {"ok": False, "status": "exception", "detail": str(exc)}

    async def infer(self, port: int, body: dict[str, Any]) -> dict[str, Any]:
        """Passthrough /v1/audio/speech to qwen3tts-server."""
        url = f"http://127.0.0.1:{port}/v1/audio/speech"
        try:
            async with httpx.AsyncClient(timeout=_INFER_TIMEOUT) as client:
                resp = await client.post(url, json=body)
                resp.raise_for_status()
                return resp.json()  # type: ignore[no-any-return]
        except httpx.HTTPStatusError as exc:
            raise Qwen3TTSInferError(
                f"Qwen3-TTS returned HTTP {exc.response.status_code}",
                details={"port": port, "status_code": exc.response.status_code},
            ) from exc
        except httpx.HTTPError as exc:
            raise Qwen3TTSInferError(
                f"Qwen3-TTS transport error: {exc}",
                details={"port": port},
            ) from exc
