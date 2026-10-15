"""KokoroProvider — CPU TTS inference backend (kokoro-onnx).

Image API surface:
  ENTRYPOINT: /usr/bin/tini -- /usr/local/bin/kokoro-server
  CMD:        --help
  Healthcheck: none baked in (hc=<nil> on ghcr.io/hal0ai/hal0-toolbox-kokoro:v1)

  Because the server is already the ENTRYPOINT, ``container_spec.command``
  carries flags only — no binary path or subcommand prefix needed.

Weights (#2739):
  The bundle is the registry pull of the curated ``kokoro-v1`` row:
  ``kokoro-v1.0.onnx`` plus ``voices-v1.0.bin`` in
  ``<store>/tts/kokoro-v1/`` (:func:`hal0.config.store.capability_model_dir`).
  The provider supplies ``--model_path`` from the configured store; the
  profile carries no path (ADR-0030). The store is bind-mounted read-only at
  the same absolute path, so the store-derived path resolves in-container
  without translation.

  The runner globs ``kokoro-*.onnx`` and ``voices*.bin`` under
  ``--model_path`` and, when either is missing, downloads ~354 MB from GitHub
  into its ephemeral container filesystem on every start. The spawn
  preflight (:func:`check_kokoro_bundle`) refuses to launch without the
  bundle so that fallback is never reached: the slot fails with
  ``slot.weights_missing`` naming ``hal0 model pull kokoro-v1``.

No devices / group_add:
  Kokoro runs ONNX inference entirely on CPU (no CUDA, ROCm, or NPU).
  No device nodes are required; group_add is empty.
"""

from __future__ import annotations

import logging
import shlex
from pathlib import Path
from typing import Any

import httpx

from hal0.config import store as model_store_module
from hal0.config.paths import model_store_root
from hal0.config.store import capability_model_dir
from hal0.errors import Hal0Error
from hal0.http_client import async_client
from hal0.providers.base import Provider, RuntimeLaunchPlan

log = logging.getLogger(__name__)

# Default profile name if the slot TOML omits one.
_DEFAULT_PROFILE = "kokoro"

#: The curated registry row whose pull is the kokoro bundle, and the
#: capability its pull is grouped under (``<store>/tts/kokoro-v1/``).
KOKORO_MODEL_ID = "kokoro-v1"
_KOKORO_CAPABILITY = "tts"

#: The names the runner globs under ``--model_path`` (kokoro_server.py
#: ``_resolve_paths``: ``rglob("kokoro-*.onnx")`` + ``rglob("voices*.bin")``).
_ONNX_GLOB = "kokoro-*.onnx"
_VOICES_GLOB = "voices*.bin"

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


class KokoroWeightsMissingError(Hal0Error):
    """The pulled Kokoro bundle (ONNX model + voices pack) is absent.

    Raised at container-spec time (spawn preflight) instead of letting the
    runner fall back to a GitHub download into its ephemeral filesystem.
    """

    code = "slot.weights_missing"
    status = 409


def _is_kokoro_bundle(directory: Path) -> bool:
    """True iff ``directory`` holds both files the runner globs for."""
    return any(directory.rglob(_ONNX_GLOB)) and any(directory.rglob(_VOICES_GLOB))


def kokoro_bundle_dir(model_info: dict[str, Any] | None = None) -> Path:
    """The directory to hand the runner as ``--model_path``.

    A registry-bound model path (the slot names ``kokoro-v1`` or another
    pulled bundle) wins when its directory holds a loadable bundle; otherwise
    the store-derived pull directory of the curated ``kokoro-v1`` row, which
    is where ``hal0 model pull kokoro-v1`` installs it.
    """
    registry_path = str((model_info or {}).get("path") or "")
    if registry_path:
        candidate = Path(registry_path)
        bundle = candidate if candidate.is_dir() else candidate.parent
        if bundle.is_dir() and _is_kokoro_bundle(bundle):
            return bundle
    return capability_model_dir(_KOKORO_CAPABILITY, KOKORO_MODEL_ID)


def check_kokoro_bundle(bundle_dir: Path) -> None:
    """Preflight the pulled bundle; raise a typed, named error."""
    if not bundle_dir.is_dir():
        raise KokoroWeightsMissingError(
            f"kokoro weights not found: {bundle_dir} does not exist — run "
            f"`hal0 model pull {KOKORO_MODEL_ID}` to install the ONNX model "
            "and voices pack into the model store",
            details={"model_path": str(bundle_dir), "model_id": KOKORO_MODEL_ID},
        )
    missing = [g for g in (_ONNX_GLOB, _VOICES_GLOB) if not any(bundle_dir.rglob(g))]
    if missing:
        raise KokoroWeightsMissingError(
            f"kokoro weights incomplete in {bundle_dir}: no {' or '.join(missing)} "
            f"— run `hal0 model pull {KOKORO_MODEL_ID}` to install the ONNX "
            "model and voices pack (a pre-1.4 pull left only model.gguf there, "
            "which the runner cannot load)",
            details={
                "model_path": str(bundle_dir),
                "model_id": KOKORO_MODEL_ID,
                "missing": missing,
            },
        )


def _strip_flag(tokens: list[str], flag: str) -> tuple[list[str], str]:
    """Remove ``flag`` (space or ``=`` form) from ``tokens``; return the last value."""
    out: list[str] = []
    value = ""
    skip = False
    for i, tok in enumerate(tokens):
        if skip:
            skip = False
            continue
        if tok == flag:
            value = tokens[i + 1] if i + 1 < len(tokens) else ""
            skip = True
            continue
        if tok.startswith(flag + "="):
            value = tok.split("=", 1)[1]
            continue
        out.append(tok)
    return out, value


class KokoroProvider(Provider):
    """Provider for the Kokoro ONNX TTS backend.

    CPU-only: no GPU devices, no group_add.  Weights are the registry pull
    of ``kokoro-v1`` under the model store.  The container image
    wraps ``kokoro_server.py`` which implements OpenAI-compat
    ``POST /v1/audio/speech``, ``GET /v1/models``, and ``GET /health``.

    Primary deployment path is ``container_spec`` → ``_render_quadlet_from_plan``
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
        """Return the Kokoro toolbox image reference.

        Resolution (#2234 — the same tier order as
        :func:`hal0.providers.container._resolve_image_ref`, shared via
        :func:`hal0.providers._image.resolve_family_image`):
        ``slot_cfg["image_pin"]`` (top-level or ``[slot]``-nested, honored
        verbatim) → ``[slots].default_images["kokoro"]`` (operator family
        default) → the runner registry (``HAL0_RUNNER_IMAGE_KOKORO`` env
        override → the ``manifest.json`` pin) — see
        :func:`hal0.runners.resolve_runner_image`.
        """
        from hal0.providers._image import resolve_family_image

        return resolve_family_image(slot_cfg, "kokoro")

    def container_spec(
        self,
        slot_cfg: dict[str, Any],
        model_info: dict[str, Any],
    ) -> RuntimeLaunchPlan:
        """Build a RuntimeLaunchPlan for the Kokoro TTS slot.

        The toolbox image ENTRYPOINT is ``tini -- kokoro-server``, so
        ``command`` carries only flags (no binary path / subcommand).

        Flags come from the resolved profile (``kokoro`` by default).
        ``--model_path`` is supplied here from the configured store
        (:func:`kokoro_bundle_dir`) and preflighted
        (:func:`check_kokoro_bundle`); a ``--model_path`` in the profile is
        dropped, because a path is a fact of the pulled bundle, not a tune
        (ADR-0030).  ``--host`` and ``--port`` are always appended so the
        operator cannot accidentally omit them.

        No devices or group_add are emitted — Kokoro is CPU-only.

        Security opts (apparmor/seccomp=unconfined) are required for
        Proxmox LXC deployments (same rationale as FLMProvider).
        """
        from hal0.providers.container import resolve_named_profile

        port = int(slot_cfg.get("port") or 8084)
        profile_name: str = str(slot_cfg.get("profile") or _DEFAULT_PROFILE)
        profile = resolve_named_profile(slot_cfg, profile_name)
        flag_tokens = shlex.split(profile.resolved_flags) if profile.resolved_flags.strip() else []
        flag_tokens, profile_path = _strip_flag(flag_tokens, "--model_path")
        if profile_path:
            log.warning(
                "kokoro.profile_model_path_ignored profile=%s path=%s",
                profile_name,
                profile_path,
            )

        # Spawn preflight: fail by name instead of letting the runner
        # re-download the bundle into its ephemeral filesystem (#2739).
        bundle_dir = kokoro_bundle_dir(model_info)
        check_kokoro_bundle(bundle_dir)

        # command = profile flags + mandatory server binding args.
        # The ENTRYPOINT (tini -- kokoro-server) receives these as argv.
        # slot port appended last: argparse last-wins, so the slot's --port
        # always beats any --port an operator put in profile flags
        command: list[str] = [
            *flag_tokens,
            "--model_path",
            str(bundle_dir),
            "--host",
            "0.0.0.0",
            "--port",
            str(port),
        ]

        # Effective model-store root ([models].store / HAL0_MODEL_STORE,
        # default /mnt/ai-models).
        store_root = model_store_root()

        return RuntimeLaunchPlan(
            image=self.image_ref(slot_cfg),
            command=command,
            env={},
            # Model store mounted read-only via the shared `mount_for`
            # factory (ML-3) — omits the SELinux relabel on NFS instead of
            # unconditionally appending ``:z`` (chcon ENOTSUP there).
            mounts=[model_store_module.mount_for(store_root, read_only=True)],
            # CPU-only: no GPU devices or supplementary groups required.
            devices=[],
            cap_add=[],
            # Required for Proxmox LXC container deployments.
            security_opt=["apparmor=unconfined", "seccomp=unconfined"],
            group_add=[],
            port=port,
            # Port-mapped (not host networking) so multiple CPU slots can
            # coexist.  the Quadlet renderer derives
            # --publish=127.0.0.1:<port>:<port> from spec.port.
            network_mode="",
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
            async with async_client(timeout=_HEALTH_TIMEOUT) as client:
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
            async with async_client(timeout=_INFER_TIMEOUT) as client:
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
