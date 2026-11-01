"""HalogenProvider — Peonist's halogen Flash-Next engine (optional runner).

halogen is a closed-source engine for Qwen3.8-Flash-Next on Strix Halo
(gfx1151, ROCm). hal0 runs the vendor's own image
(``ghcr.io/peonist-ai/halogen-flash-server``, pinned in ``manifest.json``) as
one container per slot, like every other family; it never builds or modifies
the image.

Everything the engine reads is an environment variable, read once at start
(``HALOGEN_<NAME>``). :func:`halogen_env` builds that block from three layers,
later layers winning:

1. the profile's free flags — ``HALOGEN_NAME=value`` tokens in its ``flags``
   text (ADR-0029: the profile owns tuning);
2. the slot's effective tune (:func:`hal0.slots.tune.effective_tune`):
   context window, thinking, reasoning effort and budget, vision, speculation;
3. hal0-managed facts, always set and never overridable: the listener
   (``HALOGEN_API_PORT``, ``HALOGEN_BIND=127.0.0.1``), the checkpoint file and
   the model id.

The container never receives ``HALOGEN_DOWNLOAD`` (hal0 owns downloads),
``HALOGEN_API_KEY*`` (the slot port is loopback-fenced and hal0's ``/v1`` is
the authenticated edge) or any ``HALOGEN_NPU_*`` variable, even from a profile
file that names them.

The image ENTRYPOINT's single-container mode (``all``: engine, API and
watchdog) is selected explicitly by ``command=["all"]``.

Self-managed weights: the slot's ``[model].default`` names a registry row for
the ``.hgn`` checkpoint, and the entrypoint finds the tokenizer, the lookup
table and the vision sidecar beside it by name. The model store is mounted
read-only at its own path, so the checkpoint path resolves unchanged inside.

Readiness is provider-owned (:meth:`HalogenProvider.wait_ready`): the engine
can take minutes to read ~118 GiB of weights, far past the generic 180 s
health budget. The generic output-sanity gate asks ``/completion``, which this
engine does not serve, so :meth:`HalogenProvider.verify_inference` runs the
same sentinel over ``/v1/chat/completions`` with thinking off.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx

from hal0.config import store as model_store_module
from hal0.config.paths import _covers, model_store_root
from hal0.errors import Hal0Error, UnprocessableEntity
from hal0.http_client import async_client
from hal0.install.perms import ensure_shared_dir
from hal0.providers._gpu import resolve_gpu_device_paths, resolve_gpu_group_ids
from hal0.providers.base import HealthCheck, Mount, Provider, RuntimeLaunchPlan
from hal0.runners.knobs import (
    HALOGEN_DENIED_ENV,
    HALOGEN_DENIED_PREFIXES,
    HALOGEN_MANAGED_ENV,
    HALOGEN_MANAGED_PREFIXES,
    parse_env_tokens,
)
from hal0.slots import output_sanity
from hal0.slots.tune import EffectiveTune, effective_tune

log = logging.getLogger(__name__)

#: Default profile name if the slot TOML omits one.
_DEFAULT_PROFILE = "halogen"

#: Slot port when the slot TOML names none (it normally pins one).
_DEFAULT_PORT = 8099

#: The vision tower's weights sit beside the checkpoint under this exact name.
#: The entrypoint exits 1 when ``HALOGEN_VISION_TOWER`` is set and the file is
#: absent, so hal0 sets the variable only after checking for it.
VISION_SIDECAR = "qwen38-flash-next-vision.hgn"

#: Health/infer timeouts (the infer passthrough is for smoke checks; the
#: dispatcher is the primary request path).
_HEALTH_TIMEOUT = httpx.Timeout(5.0)
_INFER_TIMEOUT = httpx.Timeout(connect=5.0, read=120.0, write=5.0, pool=5.0)

#: Names a profile may not put in the container: hal0-owned and held-back
#: variables (:mod:`hal0.runners.knobs`). Stripped from the profile's tokens
#: so a hand-edited profile file cannot bypass the write-time screen.
_PROFILE_STRIPPED_PREFIXES: tuple[str, ...] = (
    *HALOGEN_MANAGED_PREFIXES,
    *HALOGEN_DENIED_PREFIXES,
)


class HalogenInferError(Hal0Error):
    """halogen inference call failed."""

    code = "dispatch.upstream_failed"
    status = 502


def vision_sidecar_present(checkpoint: str) -> bool:
    """True when the vision sidecar file sits beside ``checkpoint``."""
    return (Path(checkpoint).parent / VISION_SIDECAR).is_file()


def _profile_env(profile_flags: str) -> dict[str, str]:
    """The profile's ``HALOGEN_NAME=value`` tokens, less names hal0 keeps out of the container."""
    return {
        name: value
        for name, value in parse_env_tokens(profile_flags).items()
        if name not in HALOGEN_MANAGED_ENV
        and name not in HALOGEN_DENIED_ENV
        and not name.startswith(_PROFILE_STRIPPED_PREFIXES)
    }


def _tune_env(tune: EffectiveTune, checkpoint: str) -> dict[str, str]:
    """The variables the slot's tune owns; a value that means "engine default" is left out.

    ``speculation = "ngram"`` maps like ``auto``: this engine has no n-gram
    drafter, and the write seams refuse the value before it is stored.
    """
    env: dict[str, str] = {}
    if tune.ctx_size is not None:
        env["HALOGEN_CTX"] = str(tune.ctx_size)
    if tune.enable_thinking is not None:
        env["HALOGEN_ENABLE_THINKING"] = "1" if tune.enable_thinking else "0"
    if tune.reasoning_effort and tune.reasoning_effort != "default":
        env["HALOGEN_REASONING_EFFORT"] = tune.reasoning_effort
    if tune.reasoning_budget >= 0:
        env["HALOGEN_MAX_THINKING_TOKENS"] = str(tune.reasoning_budget)
    if tune.vision == "auto" and vision_sidecar_present(checkpoint):
        env["HALOGEN_VISION_TOWER"] = "1"
    if tune.speculation == "off":
        env["HALOGEN_DRAFTER_DEFAULT"] = "0"
        env["HALOGEN_PLD"] = "0"
    return env


def halogen_env(
    *,
    port: int,
    checkpoint: str,
    model_id: str,
    tune: EffectiveTune,
    profile_flags: str,
) -> dict[str, str]:
    """The container's environment: profile tokens, then the tune, then hal0's own facts.

    Pure apart from the sidecar existence check. Later layers win, and the
    managed block is always present, so a profile can never move the listener,
    the checkpoint or the model id. Raises ``UnprocessableEntity``
    (``profiles.halogen_flag_malformed``) when ``profile_flags`` is not
    ``HALOGEN_NAME=value`` tokens.
    """
    env = _profile_env(profile_flags)
    env.update(_tune_env(tune, checkpoint))
    env.update(
        {
            "HALOGEN_API_PORT": str(port),
            "HALOGEN_BIND": "127.0.0.1",
            "HALOGEN_CHECKPOINT": checkpoint,
            "HALOGEN_MODEL_ID": model_id,
        }
    )
    return env


def _probe_failure(status: str, detail: str) -> dict[str, Any]:
    """A sentinel verdict for a round trip that did not complete."""
    return {"ok": False, "status": status, "sample": "", "detail": detail}


def _ensure_cache_dir(path: str) -> None:
    """Best-effort create the ``/cache`` bind source, as the hal0 service account.

    podman exits 125 on a bind source that does not exist, so a missing cache
    dir would fail every start. The default sits under ``/var/lib/hal0``, which
    the service account owns, so it can create it (``ensure_shared_dir`` lands
    the shared-directory mode whatever the umask). A dir that exists is left as
    it is. A failure is logged with the path and never raised: the unit's start
    failure then has a visible cause.
    """
    try:
        ensure_shared_dir(path)
    except OSError as exc:
        log.warning("halogen.cache_dir_unavailable path=%s error=%s", path, exc)


def _slot_model_default(slot_cfg: Mapping[str, Any]) -> str:
    """The slot's ``[model].default``, or ``""``."""
    model = slot_cfg.get("model")
    if isinstance(model, Mapping):
        return str(model.get("default") or "")
    return ""


class HalogenProvider(Provider):
    """Provider for the halogen Flash-Next engine (ROCm, one model family).

    Primary deployment path is ``container_spec`` → ``_render_quadlet_from_plan``
    (the same pattern as every other container family). ``build_env`` /
    ``start_cmd`` are informational stubs kept for ABC compliance.
    """

    name = "halogen"

    #: ROCm-only image (``RUNNER_IMAGES["halogen"].supported_backends ==
    #: ("rocm",)``); ``container_spec`` forwards ``/dev/kfd``.
    gpu_runtime_needs_rocm = True

    #: The entrypoint's own stop is SIGTERM, 30 s, SIGKILL, up to 30 s more,
    #: then a GTT report; hal0 gives it 120 s and never removes the unit sooner.
    stop_grace_s = 120

    #: Seconds ``wait_ready`` polls ``/health``. Under the WARMING watchdog's
    #: 900 s; about twice the slowest cold load seen (7 to 97 s for the KV pool
    #: under host compaction, minutes for the weights).
    READY_TIMEOUT_S = 600.0
    READY_POLL_S = 5.0

    #: Seconds a responding engine may report a failed capability probe before
    #: ``wait_ready`` stops waiting. The front end re-probes on the next request
    #: and a slot that is not ready gets none, so the sentinel has to be it.
    CAPABILITY_GRACE_S = 60.0

    #: Read timeout of :meth:`liveness`: a PING can take up to 30 s on a busy
    #: engine, the same figure as the unit's own health timeout.
    LIVENESS_READ_TIMEOUT_S = 35.0

    #: Seconds the per-request readiness gate waits on :meth:`liveness`
    #: (:func:`hal0.providers.container.readiness_probe_budget_s`). A request
    #: must not sit out a stall; it gets the retryable "starting" instead.
    readiness_budget_s = 5.0

    #: Budget for the sentinel chat turn: the first prompt after a start pages
    #: the lookup table in from disk.
    SENTINEL_TIMEOUT_S = 60.0

    #: Host directory bind-mounted read-write at ``/cache``. Reserved for the
    #: engine's cache variables; env ``HAL0_HALOGEN_CACHE`` overrides it.
    CACHE_DIR = "/var/lib/hal0/halogen-cache"

    # ── Provider ABC stubs ─────────────────────────────────────────────────────

    def build_env(
        self,
        slot_cfg: dict[str, Any],
        model_info: dict[str, Any],
    ) -> dict[str, str]:
        """Informational env block (the container is self-contained)."""
        return {
            "HAL0_SLOT": str(slot_cfg.get("name", "")),
            "HAL0_RUNTIME": "container",
            "HAL0_PROFILE": str(slot_cfg.get("profile") or _DEFAULT_PROFILE),
        }

    def start_cmd(self, env: dict[str, str]) -> list[str]:
        """Not applicable — systemd starts the container."""
        raise NotImplementedError("HalogenProvider uses systemd; start_cmd() is unused")

    # ── Image / container spec ─────────────────────────────────────────────────

    def image_ref(self, slot_cfg: dict[str, Any]) -> str:
        """Return the halogen image reference.

        Same tier order as every other family
        (:func:`hal0.providers._image.resolve_family_image`): the slot's
        ``image_pin`` → ``[slots].default_images["halogen"]`` → the runner
        registry (``HAL0_RUNNER_IMAGE_HALOGEN`` env → the ``manifest.json`` pin).
        """
        from hal0.providers._image import resolve_family_image

        return resolve_family_image(slot_cfg, "halogen")

    def container_spec(
        self,
        slot_cfg: dict[str, Any],
        model_info: dict[str, Any],
    ) -> RuntimeLaunchPlan:
        """Build the RuntimeLaunchPlan for a halogen slot.

        The image ENTRYPOINT takes a mode as its first argument, so ``command``
        is ``["all"]``. The environment comes from :func:`halogen_env`. GPU
        device nodes and the numeric render/video GIDs come from the shared
        helpers; ``ipc=host`` and ``memlock=-1`` are load-bearing (the ROCm
        runtime, and ``HALOGEN_WEIGHTS_LOCK=1``). Stop and restart are typed:
        a 120 s stop bound, and exit statuses 1 (the entrypoint's terminal
        status) and 2 (a refused configuration) never auto-restart.

        Raises ``UnprocessableEntity`` (``slot.model_missing``) when
        ``model_info`` carries no checkpoint path: the engine must never start
        on a file it cannot read.
        """
        from hal0.providers.container import resolve_named_profile

        checkpoint = str(model_info.get("path") or "")
        if not checkpoint:
            raise UnprocessableEntity(
                f"slot {slot_cfg.get('name')!r} has no halogen checkpoint to launch; "
                "assign a model whose file is a .hgn checkpoint",
                code="slot.model_missing",
                details={"slot": str(slot_cfg.get("name") or "")},
            )
        model_id = str(model_info.get("_model_key") or _slot_model_default(slot_cfg))

        port = int(slot_cfg.get("port") or _DEFAULT_PORT)
        profile_name = str(slot_cfg.get("profile") or _DEFAULT_PROFILE)
        profile = resolve_named_profile(slot_cfg, profile_name)
        tune = effective_tune(slot_cfg, profile)
        if tune.speculation == "ngram":
            # The write seams refuse ngram for this family; a stale stored value
            # launches the engine default, and this is the one place that says so.
            log.warning(
                "halogen.speculation_ngram_ignored slot=%s: this engine has no n-gram "
                "drafter, so the slot launches with its default speculation; set "
                "speculation to auto or off",
                slot_cfg.get("name") or "",
            )

        cache_dir = os.environ.get("HAL0_HALOGEN_CACHE", self.CACHE_DIR)
        _ensure_cache_dir(cache_dir)
        model_roots = self._model_roots(checkpoint)
        probe = (
            'python3 -c "import urllib.request;'
            f"urllib.request.urlopen('http://127.0.0.1:{port}/health',timeout=30)\""
        )

        return RuntimeLaunchPlan(
            image=self.image_ref(slot_cfg),
            command=["all"],
            env=halogen_env(
                port=port,
                checkpoint=checkpoint,
                model_id=model_id,
                tune=tune,
                profile_flags=profile.flags,
            ),
            mounts=[
                # Model roots via the shared `mount_for` factory: identical path,
                # read-only, SELinux relabel omitted on NFS.
                *(model_store_module.mount_for(root, read_only=True) for root in model_roots),
                Mount(cache_dir, "/cache", read_only=False, selinux="z"),
            ],
            devices=list(resolve_gpu_device_paths()),
            cap_add=[],
            # Required for Proxmox LXC container deployments.
            security_opt=["apparmor=unconfined", "seccomp=unconfined"],
            group_add=[str(g) for g in resolve_gpu_group_ids()],
            port=port,
            # Port-mapped: the renderer derives 127.0.0.1:<port>:<port> from spec.port.
            network_mode="",
            ipc="host",
            ulimits=["memlock=-1"],
            # A non-200 raises, so the probe exits 1. A PING can take up to 30 s
            # on a busy engine, hence the 35 s timeout and the 10 min start period.
            health=HealthCheck(
                cmd=probe,
                start_period="10m",
                interval="30s",
                retries=3,
                timeout="35s",
            ),
            stop_timeout_s=self.stop_grace_s,
            # 1 = every runtime failure, 2 = a refused configuration (HALOGEN_CTX
            # past the native window without HALOGEN_ROPE_YARN)
            restart_prevent_exit_status=(1, 2),
        )

    @staticmethod
    def _model_roots(checkpoint: str) -> list[str]:
        """Directories to mount read-only for ``checkpoint``.

        The model store root, plus the checkpoint's own directory when the
        store root does not cover it (the llama path's reachability rule): the
        entrypoint reads the tokenizer, lookup table and vision sidecar beside
        the checkpoint, so the engine must see that whole directory.
        """
        store = model_store_root()
        path = os.path.normpath(checkpoint)
        roots = [str(store)]
        if not _covers(store, path):
            parent = os.path.dirname(path)
            if parent and not _covers(store, parent):
                roots.append(parent)
        return roots

    # ── Health / readiness / sentinel ──────────────────────────────────────────

    async def health(self, port: int) -> dict[str, Any]:
        """Probe ``GET /health``: ready once the engine answers its own PING.

        ``ok`` needs HTTP 200 and ``engine.responds`` true. ``status`` is
        ``"ready"``, ``"loading"`` (200 but the engine is not up yet),
        ``"http_<code>"`` or the transport error text. ``capability_probe`` is
        the body's own ``capability_probe`` value (``None`` when the body has
        none or there was no body); it does not change ``ok``, and
        :meth:`wait_ready` judges it. Never raises. This is the readiness view
        of a start; a loaded slot is watched through :meth:`liveness`.
        """
        url = f"http://127.0.0.1:{port}/health"
        try:
            async with async_client(timeout=_HEALTH_TIMEOUT) as client:
                resp = await client.get(url)
            if resp.status_code != 200:
                return {"ok": False, "status": f"http_{resp.status_code}", "capability_probe": None}
            body = resp.json()
        except Exception as exc:
            return {
                "ok": False,
                "status": str(exc) or type(exc).__name__,
                "capability_probe": None,
            }
        engine = body.get("engine") if isinstance(body, Mapping) else None
        responds = isinstance(engine, Mapping) and engine.get("responds") is True
        probe = body.get("capability_probe") if isinstance(body, Mapping) else None
        return {
            "ok": responds,
            "status": "ready" if responds else "loading",
            "capability_probe": probe,
        }

    async def liveness(self, port: int) -> dict[str, Any]:
        """Is a loaded slot's front end still there? The fail-watcher's probe.

        Deliberately does not ask whether the engine is healthy. The engine can
        go silent under host memory pressure, and taking it down then is what
        leaves the driver holding its memory, so only a connection that really
        breaks may strike a slot (the container's own death reads through the
        unit's is-active first).

        ``GET /health`` with a :attr:`LIVENESS_READ_TIMEOUT_S` read timeout: HTTP
        200 is ``ok`` without judging ``engine.responds``, a read timeout is ``ok``
        with status ``"stalled"`` (accepted, then no answer), any other status or
        a connection that fails is not ``ok``. Never raises.
        """
        url = f"http://127.0.0.1:{port}/health"
        timeout = httpx.Timeout(connect=5.0, read=self.LIVENESS_READ_TIMEOUT_S, write=5.0, pool=5.0)
        try:
            async with async_client(timeout=timeout) as client:
                resp = await client.get(url)
        except httpx.ReadTimeout:
            return {"ok": True, "status": "stalled"}
        except (httpx.HTTPError, OSError) as exc:
            return {"ok": False, "status": str(exc) or type(exc).__name__}
        if resp.status_code != 200:
            return {"ok": False, "status": f"http_{resp.status_code}"}
        return {"ok": True, "status": "alive"}

    async def wait_ready(self, port: int) -> None:
        """Poll :meth:`health` until the engine responds and its capability probe has settled.

        Returns on ``capability_probe == "ok"``, or once ``"failed"`` has held
        for :attr:`CAPABILITY_GRACE_S` with the engine responding. A failed probe
        is the front end not getting the engine's capability line (context and
        version) in time, with ``HALOGEN_CTX`` as its fallback, not a broken
        engine: the chat template has its own check, which refuses to start. It
        re-probes on the next request, and the sentinel that runs after this
        wait is that request, and judges the answer. Any other value, or none,
        keeps waiting.

        ``TimeoutError`` past ``READY_TIMEOUT_S``, naming the last thing seen and
        the last probe state. The slot manager prefers a provider's own
        ``wait_ready`` over the generic container waiter, whose 180 s budget is
        shorter than a cold load of this engine.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.READY_TIMEOUT_S
        last = "no answer"
        failed_since: float | None = None
        while True:
            verdict = await self.health(port)
            probe = verdict.get("capability_probe")
            responds = bool(verdict.get("ok"))
            if responds and probe == "ok":
                return
            if responds and probe == "failed":
                if failed_since is None:
                    failed_since = loop.time()
                if loop.time() - failed_since >= self.CAPABILITY_GRACE_S:
                    log.warning(
                        "halogen.capability_probe_failed port=%s: the engine responds but "
                        "capability_probe has read 'failed' for %gs; letting the sentinel's "
                        "chat turn judge it",
                        port,
                        self.CAPABILITY_GRACE_S,
                    )
                    return
            else:
                failed_since = None
            status = "engine responds" if responds else str(verdict.get("status") or "no answer")
            last = f"{status}, capability_probe={probe!r}"
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(self.READY_POLL_S, remaining))
        raise TimeoutError(
            f"halogen slot port {port} was not ready within {self.READY_TIMEOUT_S:.0f}s "
            f"(last: {last})"
        )

    async def verify_inference(self, port: int) -> dict[str, Any]:
        """The output-sanity sentinel over ``/v1/chat/completions``.

        One greedy 32-token turn with thinking off (the template's default
        thinking can end with an empty answer inside a small budget), judged by
        the shared classifier. Returns ``{"ok", "status", "sample"}``. A round
        trip that did not finish is ``ok`` false with the generic gate's
        ``probe_*`` status (``probe_timeout``, ``probe_transport_error``,
        ``probe_http_<code>``, ``probe_unparseable``) and a ``detail``; it never
        raises.
        """
        url = f"http://127.0.0.1:{port}/v1/chat/completions"
        body = {
            "messages": [{"role": "user", "content": output_sanity.SANITY_PROMPT}],
            "max_tokens": 32,
            "temperature": 0,
            "enable_thinking": False,
            "stream": False,
        }
        try:
            async with async_client(timeout=httpx.Timeout(self.SENTINEL_TIMEOUT_S)) as client:
                resp = await client.post(url, json=body)
        except httpx.TimeoutException:
            return _probe_failure(
                "probe_timeout", f"no completion within {self.SENTINEL_TIMEOUT_S:g}s"
            )
        except (httpx.HTTPError, OSError) as exc:
            return _probe_failure("probe_transport_error", str(exc) or type(exc).__name__)
        if resp.status_code != 200:
            return _probe_failure(f"probe_http_{resp.status_code}", (resp.text or "")[:120])
        try:
            payload = resp.json()
        except ValueError:
            return _probe_failure("probe_unparseable", (resp.text or "")[:120])
        verdict = output_sanity.classify(output_sanity._extract_text(payload))
        return {"ok": verdict.ok, "status": verdict.status, "sample": verdict.sample}

    async def infer(self, port: int, body: dict[str, Any]) -> dict[str, Any]:
        """Passthrough ``/v1/chat/completions`` to the engine."""
        url = f"http://127.0.0.1:{port}/v1/chat/completions"
        try:
            async with async_client(timeout=_INFER_TIMEOUT) as client:
                resp = await client.post(url, json=body)
                resp.raise_for_status()
                return resp.json()  # type: ignore[no-any-return]
        except httpx.HTTPStatusError as exc:
            raise HalogenInferError(
                f"halogen returned HTTP {exc.response.status_code}",
                details={"port": port, "status_code": exc.response.status_code},
            ) from exc
        except httpx.HTTPError as exc:
            raise HalogenInferError(
                f"halogen transport error: {exc}",
                details={"port": port},
            ) from exc
