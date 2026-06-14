"""KokoroProvider — CPU TTS inference backend (kokoro-onnx).

Image API surface:
  ENTRYPOINT: /usr/bin/tini -- /usr/local/bin/kokoro-server
  CMD:        --help
  Healthcheck: none baked in (hc=<nil> on ghcr.io/hal0ai/hal0-toolbox-kokoro:v1)

  Because the server is already the ENTRYPOINT, ``container_spec.command``
  carries flags only — no binary path or subcommand prefix needed.
  Flags come from the resolved ``tts`` profile (profiles.toml), which
  bakes ``--model_path /mnt/ai-models/local/kokoro-v1/kokoro-onnx``.

Weights:
  Kokoro weights live under /mnt/ai-models (read-only NFS/local NVMe mount).
  The model store is bind-mounted at the same absolute path inside the
  container (identical-path convention, same as llama-server GGUFs) so the
  ``--model_path`` flag the profile bakes in resolves correctly without
  path translation.

  The mount dst string carries the ``:ro`` suffix directly because
  ``_render_unit_from_spec`` renders mounts as ``--volume={src}:{dst}``
  verbatim — there is no separate read-only flag on ContainerSpec.

No devices / group_add:
  Kokoro runs ONNX inference entirely on CPU (no CUDA, ROCm, or NPU).
  No device nodes are required; group_add is empty.

Self-managed weights:
  Kokoro is in SELF_MANAGED_PROVIDERS — the model store is operator-managed
  (weights pre-downloaded to /mnt/ai-models) rather than hal0-registry-managed.
"""

from __future__ import annotations

import shlex
from typing import Any

import httpx

from hal0.config.paths import model_store_root
from hal0.errors import Hal0Error
from hal0.providers.base import ContainerSpec, Mount, Provider

# Default image tag (overridable via HAL0_TOOLBOX_IMAGE_KOKORO for dev/test).
_DEFAULT_KOKORO_IMAGE = "ghcr.io/hal0ai/hal0-toolbox-kokoro:v1"

# Default profile name if the slot TOML omits one.
_DEFAULT_PROFILE = "tts"

# Model store is mounted identical-path so the profile-baked
# ``--model_path <store>/local/kokoro-v1/kokoro-onnx`` resolves inside the
# container without translation. The root is resolved per-render via
# model_store_root(); read-only + SELinux relabel are first-class Mount flags.
# NOTE: the weights path is still profile-baked under the default store
# (profiles.toml); a non-default [models].store moves the mount but not that
# flag yet — tracked as a follow-up.

# Providers whose model weights are pre-staged by the operator (not hal0 registry).
SELF_MANAGED_PROVIDERS: frozenset[str] = frozenset({"kokoro", "comfyui"})

# Health/infer timeouts.
_HEALTH_TIMEOUT = httpx.Timeout(5.0)
_INFER_TIMEOUT = httpx.Timeout(connect=5.0, read=60.0, write=5.0, pool=5.0)


class KokoroHealthError(Hal0Error):
    """Kokoro health probe failed."""

    code = "slot.not_ready"
    status = 503


class KokoroInferError(Hal0Error):
    """Kokoro inference call failed."""

    code = "dispatch.upstream_failed"
    status = 502


class KokoroProvider(Provider):
    """Provider for the Kokoro ONNX TTS backend.

    CPU-only: no GPU devices, no group_add.  Weights are operator-staged
    under /mnt/ai-models (``SELF_MANAGED_PROVIDERS``).  The container image
    wraps ``kokoro_server.py`` which implements OpenAI-compat
    ``POST /v1/audio/speech``, ``GET /v1/models``, and ``GET /health``.

    Primary deployment path is ``container_spec`` → ``_render_unit_from_spec``
    (same pattern as FLMProvider).  ``build_env`` / ``start_cmd`` are
    informational stubs kept for ABC compliance and debug shells.
    """

    name = "kokoro"

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
        raise NotImplementedError("KokoroProvider uses systemd; start_cmd() is unused")

    # ── Image / container spec ─────────────────────────────────────────────────

    def image_ref(self, slot_cfg: dict[str, Any]) -> str:
        """Return the Kokoro toolbox image reference."""
        import os

        return os.environ.get("HAL0_TOOLBOX_IMAGE_KOKORO", _DEFAULT_KOKORO_IMAGE)

    def container_spec(
        self,
        slot_cfg: dict[str, Any],
        model_info: dict[str, Any],
    ) -> ContainerSpec:
        """Build a ContainerSpec for the Kokoro TTS slot.

        The toolbox image ENTRYPOINT is ``tini -- kokoro-server``, so
        ``command`` carries only flags (no binary path / subcommand).

        Flags come from the resolved profile (``tts`` by default),
        which bakes ``--model_path /mnt/ai-models/local/kokoro-v1/kokoro-onnx``
        plus any future bench-tuned additions.  ``--host`` and ``--port`` are
        always appended so the operator cannot accidentally omit them.

        No devices or group_add are emitted — Kokoro is CPU-only.

        Security opts (apparmor/seccomp=unconfined) are required for
        Proxmox LXC deployments (same rationale as FLMProvider).
        """
        from hal0.profiles import ProfileCatalog

        port = int(slot_cfg.get("port") or 8084)
        profile_name: str = str(slot_cfg.get("profile") or _DEFAULT_PROFILE)
        profile = ProfileCatalog().resolve(profile_name)
        # ``resolved_flags`` is already MTP-expanded; these include --model_path.
        flag_tokens = shlex.split(profile.resolved_flags) if profile.resolved_flags.strip() else []

        # command = profile flags + mandatory server binding args.
        # The ENTRYPOINT (tini -- kokoro-server) receives these as argv.
        # slot port appended last: argparse last-wins, so the slot's --port
        # always beats any --port an operator put in profile flags
        command: list[str] = [
            *flag_tokens,
            "--host",
            "0.0.0.0",
            "--port",
            str(port),
        ]

        # Effective model-store root ([models].store / HAL0_MODEL_STORE,
        # default /mnt/ai-models).
        store_root = model_store_root()

        return ContainerSpec(
            image=profile.image,
            command=command,
            env={},
            # Model store mounted read-only with an SELinux relabel — both are
            # first-class Mount flags (no target-string smuggling).
            mounts=[Mount(store_root, store_root, read_only=True, selinux="z")],
            # CPU-only: no GPU devices or supplementary groups required.
            devices=[],
            cap_add=[],
            # Required for Proxmox LXC container deployments.
            security_opt=["apparmor=unconfined", "seccomp=unconfined"],
            group_add=[],
            port=port,
            # Port-mapped (not host networking) so multiple CPU slots can
            # coexist.  _render_unit_from_spec derives
            # --publish=127.0.0.1:<port>:<port> from spec.port.
            network_mode="",
            extra_args=[],
        )

    # ── Health / infer ─────────────────────────────────────────────────────────

    async def health(self, port: int) -> dict[str, Any]:
        """Probe GET /health on the kokoro-server port.

        NOTE: dead code in the container deployment path — slot health checks
        go through :meth:`ContainerProvider.health` (which implements the same
        ``model_loaded`` gating).  Kept because ``health`` is abstract on the
        Provider ABC: removing it would make KokoroProvider abstract and break
        the ``_spec_provider_for`` instantiation in container.py.

        kokoro_server.py returns {status: "ok", model_loaded: true} when
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
        """Passthrough /v1/audio/speech to kokoro-server."""
        url = f"http://127.0.0.1:{port}/v1/audio/speech"
        try:
            async with httpx.AsyncClient(timeout=_INFER_TIMEOUT) as client:
                resp = await client.post(url, json=body)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as exc:
            raise KokoroInferError(
                f"Kokoro returned HTTP {exc.response.status_code}",
                details={"port": port, "status_code": exc.response.status_code},
            ) from exc
        except httpx.HTTPError as exc:
            raise KokoroInferError(
                f"Kokoro transport error: {exc}",
                details={"port": port},
            ) from exc
