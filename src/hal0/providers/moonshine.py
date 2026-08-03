"""MoonshineProvider — CPU STT inference backend (moonshine-onnx).

The STT sibling of :class:`~hal0.providers.kokoro.KokoroProvider`: a
``type=transcription`` slot on ``device=cpu`` runs this engine, while the
same slot on ``device=npu`` runs whisper through the FLM trio — the
device-keyed engine switch, same shape as the tts kokoro⇄qwen3tts pair.

Image API surface (ghcr.io/hal0ai/hal0-toolbox-moonshine:v1; server source
packaging/toolbox/moonshine/moonshine_server.py):
  ENTRYPOINT: the FastAPI server — ``container_spec.command`` carries flags
  only (no binary path or subcommand prefix).
  Flags: ``--model_path <dir> --model_arch <arch> --port N --host H``.
  Endpoints:
    GET  /health                  -> {status, model_loaded, model_arch, model_id}
    GET  /v1/models               -> {data: [{id: "moonshine-<arch>-en"}]}
    POST /v1/audio/transcriptions -> OpenAI-compat multipart upload
    WS   /v1/audio/stream         -> live PCM16 @ 16kHz mono (in-container
                                     only — NOT routed by the dispatcher)

Weights:
  Moonshine ships a multi-file ONNX bundle (encoder/decoder ``.ort``/``.onnx``
  + tokenizer JSON) that the single-file curated pull schema can't express,
  so the weights are operator-staged under the model store and the provider
  is in ``SELF_MANAGED_PROVIDERS``. The store is bind-mounted identical-path
  read-only so the profile-baked ``--model_path`` resolves in-container.
  :meth:`MoonshineProvider.container_spec` preflights that path and raises a
  typed error NAMING the missing directory — a container that starts and
  500s on first request is not an acceptable failure mode.

No devices / group_add:
  The upstream moonshine wheel ships only the ONNX CPU execution provider
  (see ``_RUNTIME_TO_HOST_BACKENDS`` in capabilities/catalog.py), so no GPU
  passthrough is emitted.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

import httpx

from hal0.config import store as model_store_module
from hal0.config.paths import model_store_root
from hal0.errors import Hal0Error
from hal0.providers.base import ContainerSpec, Provider
from hal0.runners import RUNNER_IMAGES

# Sourced from the runner-image registry (§7.1b / ML-4) so the literal tag
# lives in exactly one place (hal0.runners.RUNNER_IMAGES["moonshine"].image).
# Kept as a module attribute for back-compat imports.
_DEFAULT_MOONSHINE_IMAGE = RUNNER_IMAGES["moonshine"].image

# Default profile name if the slot TOML omits one.
_DEFAULT_PROFILE = "moonshine"

# Default slot port (slot TOML normally pins this; matches the server's own
# argparse default so a profile-less dev run lands on the same port).
_DEFAULT_PORT = 8089

# moonshine_server.py only accepts these arch tokens.
_VALID_ARCHS = {"tiny", "tiny_streaming", "base", "small", "small_streaming"}

_DEFAULT_MODEL_ARCH = "small_streaming"

# Health/infer timeouts. Transcription of a long upload is slow on CPU —
# give the read leg headroom.
_HEALTH_TIMEOUT = httpx.Timeout(5.0)
_INFER_TIMEOUT = httpx.Timeout(connect=5.0, read=120.0, write=10.0, pool=5.0)


class MoonshineHealthError(Hal0Error):
    """Moonshine health probe failed."""

    code = "slot.not_ready"
    status = 503


class MoonshineInferError(Hal0Error):
    """Moonshine inference call failed."""

    code = "dispatch.upstream_failed"
    status = 502


class MoonshineWeightsMissingError(Hal0Error):
    """The operator-staged Moonshine ONNX bundle is absent.

    Raised at container-spec time (spawn preflight) so the failure names the
    artifact instead of surfacing as a container that starts and 500s.
    """

    code = "slot.weights_missing"
    status = 409


def _derive_arch_from_variant(variant: str) -> str:
    """Map a registry ``metadata.variant`` to a moonshine_server arch.

    Variants look like ``base-en`` / ``small-streaming-en``. The arch enum
    drops the ``-en`` suffix and uses underscores for the streaming flag
    (``small-streaming-en`` → ``small_streaming``). Returns "" when the
    variant doesn't map cleanly so callers fall back to the default.
    """
    if not variant:
        return ""
    norm = variant.strip().lower().removesuffix("-en").replace("-", "_")
    return norm if norm in _VALID_ARCHS else ""


def _resolve_model_leaf(model_path: str, variant: str) -> str:
    """Pick the directory the moonshine ONNX loader actually wants.

    The staged layout keeps weights under ``<root>/quantized/<variant>/``
    (decoder/encoder/tokenizer files). A --model_path pointing at ``<root>``
    would make the in-container loader fall back to downloading from HF.
    Prefer the leaf when it exists; otherwise return the input unchanged.
    """
    if not model_path:
        return ""
    candidate = Path(model_path)
    if not candidate.is_dir():
        return model_path
    if any(candidate.glob("*.ort")) or any(candidate.glob("*.onnx")):
        return str(candidate)
    if variant:
        leaf = candidate / "quantized" / variant
        if leaf.is_dir() and (any(leaf.glob("*.ort")) or any(leaf.glob("*.onnx"))):
            return str(leaf)
    return model_path


def check_moonshine_weights(model_path: str) -> None:
    """Preflight the operator-staged bundle; raise a typed, named error.

    Shared by :meth:`MoonshineProvider.container_spec` (spawn preflight) and
    the ``hal0 doctor`` voice check so both fail with the same message. An
    EMPTY path is allowed — the server then auto-downloads from HuggingFace,
    which is a legitimate (if slow) first-run path.
    """
    if not model_path:
        return
    root = Path(model_path)
    if not root.is_dir():
        raise MoonshineWeightsMissingError(
            f"moonshine weights directory not found: {model_path} — stage the "
            "multi-file ONNX bundle (encoder/decoder + tokenizer) there, or "
            "clear --model_path from the moonshine profile to allow the "
            "in-container HuggingFace fallback",
            details={"model_path": model_path},
        )
    has_weights = any(root.rglob("*.ort")) or any(root.rglob("*.onnx"))
    if not has_weights:
        raise MoonshineWeightsMissingError(
            f"moonshine weights directory {model_path} contains no .ort/.onnx "
            "files — expected the staged bundle at <root>/quantized/<variant>/ "
            "(e.g. quantized/small-streaming-en/)",
            details={"model_path": model_path},
        )


def _flag_value(tokens: list[str], flag: str) -> str:
    """Return the value following ``flag`` in an argv token list, or ""."""
    for i, tok in enumerate(tokens[:-1]):
        if tok == flag:
            return tokens[i + 1]
    return ""


class MoonshineProvider(Provider):
    """Provider for the Moonshine ONNX STT backend.

    CPU-only: no GPU devices, no group_add. Weights are operator-staged
    under the model store (``SELF_MANAGED_PROVIDERS``). The container image
    wraps ``moonshine_server.py`` which implements OpenAI-compat
    ``POST /v1/audio/transcriptions``, ``GET /v1/models``, and ``GET /health``.

    Primary deployment path is ``container_spec`` → ``_render_quadlet_from_plan``
    (same pattern as KokoroProvider / Qwen3TTSProvider). ``build_env`` /
    ``start_cmd`` are informational stubs kept for ABC compliance.
    """

    name = "moonshine"

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
        raise NotImplementedError("MoonshineProvider uses systemd; start_cmd() is unused")

    # ── Image / container spec ─────────────────────────────────────────────────

    def image_ref(self, slot_cfg: dict[str, Any]) -> str:
        """Return the Moonshine toolbox image reference.

        Resolution (§7.1b / ML-4): ``slot_cfg["image_pin"]`` (top-level or
        ``[slot]``-nested string override) → the runner registry
        (``HAL0_TOOLBOX_IMAGE_MOONSHINE`` env override → the manifest digest
        pin → the bundled default) — see
        :func:`hal0.runners.resolve_runner_image`.
        """
        override: Any = None
        if isinstance(slot_cfg, dict):
            override = slot_cfg.get("image_pin")
            if not (isinstance(override, str) and override):
                nested = slot_cfg.get("slot")
                override = nested.get("image_pin") if isinstance(nested, dict) else None
        if isinstance(override, str) and override:
            return override

        from hal0.runners import get_runner, resolve_runner_image

        return resolve_runner_image(get_runner("moonshine"))

    def container_spec(
        self,
        slot_cfg: dict[str, Any],
        model_info: dict[str, Any],
    ) -> ContainerSpec:
        """Build a ContainerSpec for the Moonshine STT slot.

        The toolbox image ENTRYPOINT is the FastAPI server, so ``command``
        carries only flags. Flags come from the resolved profile
        (``moonshine`` by default), which bakes ``--model_path`` +
        ``--model_arch``. ``--host`` and ``--port`` are always appended
        (argparse last-wins, so the slot's --port beats any --port baked
        into profile flags).

        A registry-bound model (``model_info["path"]``) beats the
        profile-baked --model_path, with the ``quantized/<variant>`` leaf
        resolved so the in-container loader doesn't fall back to a network
        download. The effective path is preflighted via
        :func:`check_moonshine_weights` — missing weights fail HERE, loudly
        and by name, not as a 500 on first request.

        No devices or group_add are emitted — Moonshine is CPU-only.
        Security opts (apparmor/seccomp=unconfined) are required for
        Proxmox LXC deployments (same rationale as the other providers).
        """
        from hal0.profiles import ProfileCatalog

        port = int(slot_cfg.get("port") or _DEFAULT_PORT)
        profile_name: str = str(slot_cfg.get("profile") or _DEFAULT_PROFILE)
        profile = ProfileCatalog().resolve(profile_name)
        flag_tokens = shlex.split(profile.resolved_flags) if profile.resolved_flags.strip() else []

        # Registry-bound model path (self-managed slots usually have none)
        # beats the profile-baked --model_path.
        metadata = model_info.get("metadata") or {}
        variant = str(metadata.get("variant", ""))
        registry_path = str(model_info.get("path") or "")
        if registry_path:
            model_path = _resolve_model_leaf(registry_path, variant)
            arch = (
                str(model_info.get("model_arch") or "")
                or _derive_arch_from_variant(variant)
                or _flag_value(flag_tokens, "--model_arch")
                or _DEFAULT_MODEL_ARCH
            )
            # Strip the profile's --model_path/--model_arch pair; the
            # registry-resolved values are appended below (argparse
            # last-wins would also work, but a single occurrence keeps the
            # rendered Quadlet legible).
            cleaned: list[str] = []
            skip = False
            for tok in flag_tokens:
                if skip:
                    skip = False
                    continue
                if tok in ("--model_path", "--model_arch"):
                    skip = True
                    continue
                cleaned.append(tok)
            flag_tokens = [*cleaned, "--model_path", model_path, "--model_arch", arch]
        else:
            model_path = _flag_value(flag_tokens, "--model_path")

        # Spawn preflight: fail by artifact name, not by first-request 500.
        check_moonshine_weights(model_path)

        command: list[str] = [
            *flag_tokens,
            "--host",
            "0.0.0.0",
            "--port",
            str(port),
        ]

        store_root = model_store_root()

        return ContainerSpec(
            image=self.image_ref(slot_cfg),
            command=command,
            env={},
            # Model store mounted read-only via the shared `mount_for`
            # factory (ML-3) — identical-path so --model_path resolves
            # in-container without translation.
            mounts=[model_store_module.mount_for(store_root, read_only=True)],
            # CPU-only: no GPU devices or supplementary groups required.
            devices=[],
            cap_add=[],
            # Required for Proxmox LXC container deployments.
            security_opt=["apparmor=unconfined", "seccomp=unconfined"],
            group_add=[],
            port=port,
            # Port-mapped (not host networking) so it can coexist with the
            # other CPU slots; the Quadlet renderer derives
            # --publish=127.0.0.1:<port>:<port> from spec.port.
            network_mode="",
            extra_args=[],
        )

    # ── Health / infer ─────────────────────────────────────────────────────────

    async def health(self, port: int) -> dict[str, Any]:
        """Probe GET /health on the moonshine-server port.

        NOTE: dead code in the container deployment path — slot health
        checks go through :meth:`ContainerProvider.health`, whose generic
        ``model_loaded`` gating already covers this server's body shape.
        Kept because ``health`` is abstract on the Provider ABC.
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
        """Unary transcription passthrough for smoke tests.

        ``body`` carries ``{"file": (filename, bytes, content_type), ...}``
        pre-shaped for a multipart POST; the dispatcher's streaming WS path
        does NOT go through here (in-container only, unrouted).
        """
        url = f"http://127.0.0.1:{port}/v1/audio/transcriptions"
        file_part = body.get("file")
        if file_part is None:
            raise MoonshineInferError(
                "transcription body carries no 'file' part",
                details={"port": port},
            )
        data = {k: v for k, v in body.items() if k != "file" and v is not None}
        try:
            async with httpx.AsyncClient(timeout=_INFER_TIMEOUT) as client:
                resp = await client.post(url, files={"file": file_part}, data=data)
                resp.raise_for_status()
                return resp.json()  # type: ignore[no-any-return]
        except httpx.HTTPStatusError as exc:
            raise MoonshineInferError(
                f"Moonshine returned HTTP {exc.response.status_code}",
                details={"port": port, "status_code": exc.response.status_code},
            ) from exc
        except httpx.HTTPError as exc:
            raise MoonshineInferError(
                f"Moonshine transport error: {exc}",
                details={"port": port},
            ) from exc
