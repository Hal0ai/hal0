"""ContainerProvider — podman-container-per-slot runtime (P1 tracer bullet).

Every slot with ``profile`` set (or ``runtime="container"``) dispatches
through this provider — the sole slot-lifecycle backend.

Architecture (design doc §2):
  - Profile supplies:    image + bench-tuned flags (+ MTP bundle if mtp=true).
  - Slot supplies:       model path, context_size, port.
  - Container provides:  the running llama-server process.

Container lifecycle → systemd template unit ``hal0-slot@<name>.service``:
  ExecStart = podman run --rm ... <image> --model <path> --port <n> <flags>
  ExecStop  = podman stop -t 20 hal0-slot-<name>

The slot's port is loopback-published (``-p 127.0.0.1:<port>:<port>``) so
the dispatcher can proxy it via a ``kind="remote"`` upstream entry without
exposing it on the LAN.  The publish host is configurable via
``[slots].publish_host`` (``SlotsConfig.publish_host``) — an operator can set
it to ``0.0.0.0`` to reach raw slot ports directly over the LAN
(``http://<host>.local:<port>``); the loopback default is retained for every
install that doesn't opt in.  ``load_sync`` reads the live value and passes it
to :func:`_render_unit_from_plan`; the scalar shims keep the loopback default.

Mount design (IDENTICAL path, design doc §2 gotcha):
  /mnt/ai-models → /mnt/ai-models:ro
  GGUFs in the registry are symlinks whose targets are absolute
  /mnt/ai-models/... paths.  Mounting anywhere else dangles them.

GID resolution (reuses providers/_gpu.py):
  ubuntu:24.04 toolbox images lack ``render``/``video`` group entries.
  Pass numeric GIDs so the kernel gate on /dev/dri/renderD128 passes.

ABC compliance:
  Provider ABC has docker/systemd-shaped methods (build_env, start_cmd,
  container_spec).  ContainerProvider implements container_spec(); unit
  rendering is owned by the module-level ``_render_unit_from_plan`` adapter
  (the legacy ``render_systemd_override`` default was deleted in WS-15).
  build_env / start_cmd / health / infer are implemented as informational
  stubs or thin implementations — the real work is load/unload/status/health.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shlex
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx

from hal0.config.paths import DEFAULT_MODEL_STORE, model_store_root
from hal0.config.schema import family_flags, resolve_chat_template, resolve_profile_flags
from hal0.model_meta import model_is_mtp_eligible
from hal0.profiles import ProfileCatalog
from hal0.providers._gpu import (
    gpu_visibility_env,
    is_nvidia_gpu_device,
    nvidia_cdi_devices,
    resolve_gpu_device_paths,
    resolve_gpu_group_ids,
)
from hal0.providers.base import HealthCheck, Mount, Provider, RuntimeLaunchPlan
from hal0.slots.argv import ResolvedArgv, resolve_argv

# ``ContainerSpec`` is the back-compat alias for ``RuntimeLaunchPlan``; some
# callers/tests still import the old name from this module.
ContainerSpec = RuntimeLaunchPlan


def _resolve_profile(name: str) -> Any:
    """Resolve a profile name to a :class:`~hal0.profiles.ResolvedProfile`.

    Kept as a module-level indirection so it stays the single patch point
    for tests (``hal0.providers.container._resolve_profile``).  The default
    implementation goes through :class:`~hal0.profiles.ProfileCatalog`, so
    providers consume one profile interface instead of re-parsing
    profiles.toml — :func:`_profile_image_and_flags` accepts either a
    ``ResolvedProfile`` or a bare ``ProfileConfig`` (what tests inject).
    """
    return ProfileCatalog().resolve(name)


def _resolve_profile_or_base(profile_name: str, slot_cfg: dict[str, Any]) -> Any:
    """Resolve a slot's profile, falling back to the backend's basic seed
    profile (``rocm`` / ``vulkan``) when the pinned profile no longer exists.

    A seed cleanup (or a renamed seed) can leave an existing slot pinned to a
    profile name that is gone from the catalog — e.g. the retired
    ``rocm-dnse`` / ``rocm-moe``. Rather than hard-fail the launch, fall back to
    the simple profile named after the slot's backend so the slot stays
    launchable across an update. MTP-tuning is lost in the fallback (the base
    profiles are non-MTP); operators re-pin an explicit profile if they want it.
    """
    from hal0.errors import NotFound

    try:
        return _resolve_profile(profile_name)
    except NotFound:
        from hal0.config.loader import load_profiles_config

        backend = str(slot_cfg.get("backend") or "")
        catalog = load_profiles_config().profile
        base = backend if backend in catalog else "rocm"
        # An empty profile is a legitimate "no profile declared" slot running
        # on the default toolbox — not a stale/renamed profile. Falling back is
        # expected, so don't warn on every health probe (#1226); reserve the
        # warning for a NON-empty name the catalog no longer knows.
        if profile_name:
            log.warning(
                "profile %r not found; falling back to base profile %r",
                profile_name,
                base,
                extra={"event": "profile.fallback", "missing": profile_name, "base": base},
            )
        return _resolve_profile(base)


def _resolve_image_ref(slot_cfg: Mapping[str, Any] | None, profile: Any) -> str:
    """Resolve the container image ref for a slot launch.

    Resolution order (matches :meth:`ComfyUIProvider.image_ref`):

      1. ``slot_cfg["image"]`` (top-level string override in the slot TOML).
      2. ``slot_cfg["slot"]["image"]`` (nested under the ``[slot]`` table).
      3. ``profile.image`` (the profile's own image — kept for back-compat
         so existing operator-custom profiles that pin ``image`` round-trip
         cleanly through 0.9.5; targeted for removal in 0.9.6).
      4. ``DEFAULT_ROCMFPX_IMAGE`` (the code-pinned ROCmFPX runner tag).

    Only STRING values are treated as image-ref overrides. The
    ``[image]`` TOML section that holds image-gen settings (#599) shares
    the ``image`` key — treating that dict as a ref renders ``str(dict)``
    into ExecStart and podman fails with 'invalid reference format'.
    """
    if slot_cfg is not None:
        # Walk both possible nestings; first non-empty string wins.
        candidates: list[Any] = []
        for key in ("image",):
            v = slot_cfg.get(key)  # type: ignore[union-attr]
            if v is not None:
                candidates.append(v)
        nested = slot_cfg.get("slot") if isinstance(slot_cfg, Mapping) else None
        if isinstance(nested, Mapping):
            v = nested.get("image")
            if v is not None:
                candidates.append(v)
        for c in candidates:
            if isinstance(c, str) and c:
                return c
    profile_image = getattr(profile, "image", None)
    if isinstance(profile_image, str) and profile_image:
        return profile_image
    # Code default — imported lazily to avoid an import cycle (schema imports
    # from providers in some test fixtures).
    from hal0.config.schema import DEFAULT_ROCMFPX_IMAGE

    return DEFAULT_ROCMFPX_IMAGE


def _profile_image_and_flags(
    profile: Any,
    mtp_override: bool | None = None,
    slot_cfg: Mapping[str, Any] | None = None,
) -> tuple[str, str]:
    """Extract ``(image, resolved_flags)`` for a slot launch.

    Image resolution walks the priority chain in :func:`_resolve_image_ref`:
    slot-level override → profile.image → ``DEFAULT_ROCMFPX_IMAGE``.

    Works for both :class:`~hal0.profiles.ResolvedProfile` (whose
    ``resolved_flags`` is already MTP-expanded) and a plain ``ProfileConfig``
    (resolve the flags here).  This is what lets the default profile
    interface be ProfileCatalog while test-injected ``ProfileConfig`` objects
    keep working.

    When ``mtp_override`` is not None (i.e. a slot-level MTP override is set),
    the flags are always recomputed from the profile's raw ``.flags`` and
    ``.mtp`` so that the override wins over any pre-expanded ``resolved_flags``
    baked into a :class:`~hal0.profiles.ResolvedProfile`.  Both
    ``ResolvedProfile`` and ``ProfileConfig`` expose ``.flags`` and ``.mtp``,
    so this path works for both types.
    """
    if mtp_override is not None:
        # Slot override wins over any pre-expanded resolved_flags: recompute
        # from the profile's raw flags (both ResolvedProfile and ProfileConfig
        # expose .flags and .mtp).
        flags = resolve_profile_flags(profile, mtp_override)
    else:
        flags = getattr(profile, "resolved_flags", None)
        if flags is None:
            flags = resolve_profile_flags(profile)
    return _resolve_image_ref(slot_cfg, profile), str(flags)


def _effective_mtp(
    slot_mtp: bool | None,
    profile: Any,
    model_info: Mapping[str, Any],
    *,
    log_ineligible: bool = False,
) -> bool:
    """Resolve whether MTP speculative decoding is on for this slot launch.

    The three concerns are separated (design: MTP is a model property, not a
    flag-template one):

    * **slot** decides first — an explicit :attr:`SlotConfig.mtp` (``True`` /
      ``False``) always wins, so an operator can force MTP on for an untagged
      model or off for a tagged one.
    * **AUTO** (``slot_mtp is None``) enables MTP only when the **profile**
      opts in (``profile.mtp``) AND the **model** actually ships MTP heads
      (:func:`hal0.model_meta.model_is_mtp_eligible`).

    This is what stops a non-MTP model on an MTP profile (e.g. a plain chat
    GGUF pinned to ``rocm-moe``) from launching with dead ``--spec-draft-*``
    flags, without any per-slot wiring for the common case.

    ``log_ineligible`` gates the auto-off breadcrumb to the LAUNCH path only.
    This function sits inside the shared launch/preview scalar resolver
    (:func:`_resolve_llama_scalars`), and the preview path is hit by every
    dashboard ``GET /api/slots`` poll — logging unconditionally here turned a
    once-per-launch hint into a ~0.4/s stream per polling client for every
    AUTO slot pairing an MTP profile with a non-MTP model.
    """
    if slot_mtp is not None:
        return bool(slot_mtp)
    profile_opts_in = bool(getattr(profile, "mtp", False))
    eligible = model_is_mtp_eligible(model_info)
    if log_ineligible and profile_opts_in and not eligible:
        # Visible breadcrumb for the silent-auto-off case: an MTP-capable model
        # that carries neither the registry tag nor a name marker stops
        # speculating under auto. The fix is tagging the model (or slot
        # mtp=true); this log is how an operator finds that out.
        log.info(
            "mtp.auto_off_model_ineligible",
            extra={
                "model": str(model_info.get("_model_key") or model_info.get("path") or ""),
                "hint": "tag the model 'mtp' or set slot mtp=true to speculate",
            },
        )
    return profile_opts_in and eligible


def _effective_parallel(slot_cfg: Mapping[str, Any]) -> int | None:
    """Resolve the slot's ``--parallel`` (sequence-slot) override, or None.

    A slot's ``parallel`` field carries continuous-batching intent (concurrent
    requests share the once-loaded weights instead of serializing through one
    sequence). ``None`` / absent / <1 means "inherit the profile flags" (today
    every seed profile pins ``--parallel 1``); a value ≥1 is emitted as a slot
    override that beats the profile but loses to hand-authored ``extra_args``.
    """
    raw = slot_cfg.get("parallel")
    if raw is None:
        return None
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    return n if n >= 1 else None


log = logging.getLogger(__name__)

# Path to the hal0-slot@ base template unit (installed by the package).
# ContainerProvider writes a complete self-contained unit here, *not*
# a drop-in, because the v0.2 migration (PR-9) retired the base
# template.  Writing a complete file means the manager never has to
# know whether the base exists.
_SYSTEMD_SYSTEM_DIR = Path("/etc/systemd/system")
# Back-compat alias for the historic default. The *effective* mount root is
# resolved per-render via model_store_root() ([models].store / HAL0_MODEL_STORE
# / this default) so a custom model directory actually reaches the container.
_MODEL_STORE_MOUNT = DEFAULT_MODEL_STORE


# Container runtime binary.  Prefer podman (rootless, no daemon);
# fall back to docker when podman is not installed.  The HAL0_CONTAINER_RUNTIME
# env var overrides both so CI + alternate installs can pin a specific path.
def _container_runtime() -> str:
    """Resolve the container runtime binary path.

    Priority: $HAL0_CONTAINER_RUNTIME > /usr/bin/podman > /usr/bin/docker.
    Raises RuntimeError if neither is found.
    """
    override = os.environ.get("HAL0_CONTAINER_RUNTIME")
    if override:
        return override
    for candidate in ("/usr/bin/podman", "/usr/bin/docker"):
        if shutil.which(candidate):
            return candidate
    raise RuntimeError(
        "no container runtime found; install podman or docker or set HAL0_CONTAINER_RUNTIME"
    )


def _slot_publish_host() -> str:
    """Live ``[slots].publish_host`` — the address slot ports publish on.

    Fail-soft to the loopback default: a malformed/unreadable hal0.toml must
    never widen the bind (fail-open to 0.0.0.0) nor block a slot from
    starting. Read fresh each render so a Settings change lands on the next
    slot (re)start without an api process bounce.
    """
    try:
        from hal0.config.loader import load_hal0_config

        host = (load_hal0_config().slots.publish_host or "").strip()
        return host or "127.0.0.1"
    except Exception:
        log.warning("container.publish_host_load_failed", exc_info=True)
        return "127.0.0.1"


# Health-check tuning: poll GET /health on the slot port.
_HEALTH_POLL_INTERVAL_S = 2.0
_HEALTH_TIMEOUT_S = 180.0
_HEALTH_REQUEST_TIMEOUT_S = 3.0


def _resolve_model_path(model_info: dict[str, Any]) -> str:
    """Return the absolute GGUF path for this model.

    Prefers ``model_info["path"]`` (populated by ModelRegistry.get);
    falls back to ``model_info["_model_key"]`` (the model-id string)
    so the container can attempt to locate the file at runtime.
    """
    path = model_info.get("path") or model_info.get("_model_key", "")
    if not path:
        raise ValueError(
            "model_info has no 'path' — registry lookup failed; "
            "ensure the model is registered before loading a container slot."
        )
    return str(path)


def _unit_skeleton(
    slot_name: str,
    runtime: str,
    exec_start: str,
    *,
    mount_sources: list[str] | None = None,
    mkdir_sources: list[str] | None = None,
) -> str:
    """Wrap an ExecStart line in the shared ``[Unit]/[Service]`` skeleton.

    Single source of truth for the systemd unit shape used by both
    :func:`_render_unit` (llama-server path) and
    :func:`_render_unit_from_spec` (generic ContainerSpec path).
    ExecStop / ExecStopPost are derived from ``runtime`` + container name.

    ``mount_sources`` (every bind source) become ``RequiresMountsFor=`` so a
    slot on an external store (e.g. /mnt/ai-models) orders after — and pulls
    in — the backing mount at boot instead of racing it. ``mkdir_sources``
    (writable bind sources only) get a tolerant ``ExecStartPre=-mkdir -p``:
    a bind source that vanished across a reboot otherwise fails the run with
    podman exit 125 (``statfs ... no such file or directory``) forever.
    Read-only sources are deliberately NOT auto-created — silently mounting
    an empty model store would mask a broken mount with a confusing
    model-not-found error further down.
    """
    container_name = f"hal0-slot-{slot_name}"
    exec_stop = f"{runtime} stop -t 20 {container_name}"
    unit_lines = [
        "[Unit]",
        f"Description=hal0 container inference slot ({slot_name})",
        "After=network-online.target",
    ]
    if mount_sources:
        joined = " ".join(shlex.quote(s) if " " in s else s for s in mount_sources)
        unit_lines.append(f"RequiresMountsFor={joined}")
    service_lines = [
        "[Service]",
        "Type=simple",
        "Restart=no",
        f"SyslogIdentifier=hal0-slot-{slot_name}",
        "StandardOutput=journal",
        "StandardError=journal",
        "",
    ]
    if mkdir_sources:
        joined = " ".join(shlex.quote(s) if " " in s else s for s in mkdir_sources)
        service_lines.append(f"ExecStartPre=-/usr/bin/mkdir -p {joined}")
    return "\n".join(
        [
            "# hal0 container slot — generated by ContainerProvider",
            "# Do not edit manually; regenerated on every slot load.",
            "",
            *unit_lines,
            "",
            *service_lines,
            f"ExecStart={exec_start}",
            f"ExecStop={exec_stop}",
            f"ExecStopPost=-{runtime} rm -f {container_name}",
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        ]
    )


def _render_unit_from_plan(
    slot_name: str,
    plan: RuntimeLaunchPlan,
    *,
    runtime_bin: str | None = None,
    publish_host: str = "127.0.0.1",
) -> str:
    """Render a complete self-contained systemd unit from a launch plan.

    This is the ONE argv builder for every container slot — GPU/llama-server,
    FLM NPU, Kokoro TTS, and ComfyUI all flow through here.  It is the
    systemd/podman *adapter*: the plan carries the launch facts, this turns
    them into ``hal0-slot@<name>.service`` unit text.  Producing a single
    self-contained unit means the manager never needs a parent
    ``hal0-slot@.service`` template (retired in the v0.2 migration, PR-9).

    Args:
        slot_name:   Slot identifier; used in the container name,
                     SyslogIdentifier, and the unit name.
        plan:        :class:`RuntimeLaunchPlan` produced by a provider's
                     ``container_spec``.
        runtime_bin: Override the container runtime binary.  Defaults to
                     :func:`_container_runtime`.  Pass explicitly in tests to
                     avoid requiring podman/docker in the test environment.
        publish_host: Host address the slot port is published on
                     (``--publish=<host>:<port>:<port>``).  Defaults to
                     ``127.0.0.1`` (loopback-only); ``load_sync`` overrides it
                     with the live ``[slots].publish_host`` so an operator can
                     opt into ``0.0.0.0`` (LAN-exposed) or a specific address.
    """
    runtime = runtime_bin or _container_runtime()
    container_name = f"hal0-slot-{slot_name}"

    argv: list[str] = [
        runtime,
        "run",
        "--rm",
        f"--name={container_name}",
        # Unclean shutdown leaves a stale same-name container record (--rm
        # never ran) → exit 125 "name already in use" at next boot (#721).
        # --replace removes it first; no-op when no stale record exists.
        "--replace",
        # B3: the unit already routes conmon's inherited container stdout to
        # journald via StandardOutput=journal. podman's DEFAULT journald log
        # driver ALSO writes the same stdout to journald (tagged CONTAINER_NAME),
        # so `journalctl -u hal0-slot@<name>` matched both copies and every
        # line appeared twice. Disable podman's own log driver so conmon→unit
        # is the single sink. (docker ignores an unknown value gracefully; it
        # accepts --log-driver=none too.)
        "--log-driver=none",
    ]
    if plan.network_mode:
        argv.append(f"--network={plan.network_mode}")
    # Explicit device nodes (podman won't recurse the /dev/dri directory, #674).
    for dev in plan.devices:
        argv.append(f"--device={dev}")
    # Numeric GIDs for video+render groups (ubuntu:24.04 has no group names).
    for gid in plan.group_add:
        argv.append(f"--group-add={gid}")
    for cap in plan.cap_add:
        argv.append(f"--cap-add={cap}")
    for opt in plan.security_opt:
        argv.append(f"--security-opt={opt}")
    # Read-only is a first-class Mount flag (no more ":ro" target smuggling);
    # legacy (src, dst) tuples are coerced — a ":ro" target suffix still maps
    # to read_only so older callers keep rendering correctly.
    for mount in plan.mounts:
        argv.append(f"--volume={Mount.coerce(mount).render()}")
    for k, v in plan.env.items():
        argv.append(f"--env={k}={v}")
    # Publish derived from plan.port (declarative — plans no longer
    # hand-roll "-p ..." in extra_args).  ``publish_host`` defaults to
    # loopback (127.0.0.1); an operator can widen it to 0.0.0.0 / a specific
    # address via [slots].publish_host.  Skipped under host networking where
    # port publishing is meaningless.
    if plan.port and plan.network_mode != "host":
        argv.append(f"--publish={publish_host}:{plan.port}:{plan.port}")
    # Healthcheck override (#684): the toolbox image bakes a HEALTHCHECK that
    # probes a hardcoded port, but a slot runs its server on its own port — so
    # the image check fails forever and `podman ps` shows a permanent
    # unhealthy. The plan carries the override so it renders before the image
    # (health flags are podman-run options). hal0's own ContainerProvider.health()
    # remains the dashboard truth.
    if plan.health is not None:
        argv.extend(plan.health.render_flags())
    # extra_args escape hatch (e.g. "--ulimit memlock=-1")
    for extra in plan.extra_args:
        argv.extend(shlex.split(extra))
    argv.append(plan.image)
    argv.extend(plan.command)

    # ExecStart is a single long line; systemd accepts bare argv tokens.
    exec_start = " ".join(shlex.quote(a) if " " in a else a for a in argv)
    coerced = [Mount.coerce(m) for m in plan.mounts]
    return _unit_skeleton(
        slot_name,
        runtime,
        exec_start,
        mount_sources=[m.source for m in coerced],
        mkdir_sources=[m.source for m in coerced if not m.read_only],
    )


def _render_unit(
    slot_name: str,
    image: str,
    port: int,
    model_path: str,
    flags_str: str,
    runtime_bin: str | None = None,
    device_paths: list[str] | None = None,
    context_size: int | None = None,
    extra_args: str | None = None,
    model_alias: str | None = None,
) -> str:
    """Render a GPU/llama-server slot unit (back-compat scalar-arg shim).

    Retained for callers/tests that pass scalar launch parameters.  Builds a
    :class:`RuntimeLaunchPlan` from those scalars and delegates to the single
    builder :func:`_render_unit_from_plan`, so the legacy llama path and the
    spec path render through identical code.

    ``device_paths`` defaults to :func:`resolve_gpu_device_paths`; ``context_size``
    and ``extra_args`` (``[server].extra_args``) are appended after the profile
    flags so slot-level overrides win.
    """
    devices = device_paths if device_paths is not None else resolve_gpu_device_paths()
    plan = _llama_launch_plan(
        image=image,
        port=port,
        model_path=model_path,
        flags_str=flags_str,
        devices=list(devices),
        group_ids=[str(g) for g in resolve_gpu_group_ids()],
        context_size=context_size,
        extra_args=extra_args,
        model_alias=model_alias,
    )
    return _render_unit_from_plan(slot_name, plan, runtime_bin=runtime_bin)


def _render_unit_from_spec(
    slot_name: str,
    spec: RuntimeLaunchPlan,
    *,
    runtime_bin: str | None = None,
) -> str:
    """Back-compat alias for :func:`_render_unit_from_plan`.

    A ``ContainerSpec``/``RuntimeLaunchPlan`` *is* the launch plan, so this is
    a straight delegation kept for callers/tests that still import the old
    name.
    """
    return _render_unit_from_plan(slot_name, spec, runtime_bin=runtime_bin)


def _llama_argv_segments(
    *,
    port: int,
    model_path: str,
    model_alias: str | None = None,
    context_size: int | None = None,
    profile_flags: str = "",
    model_defaults: dict[str, Any] | None = None,
    chat_template_path: str | None = None,
    mmproj: str | None = None,
    slot_n_gpu_layers: int | None = None,
    slot_parallel: int | None = None,
    extra_args: str | None = None,
) -> list[tuple[str, list[str]]]:
    """Build the ordered, labelled llama-server argv segments (SINGLE SOURCE).

    Both the launch path (:func:`_llama_launch_plan` → :func:`normalize_argv`)
    and the preview path (:func:`_resolve_slot_argv` → :func:`resolve_argv`
    provenance) consume THESE segments, so what an operator previews via
    ``GET /api/slots`` / ``.../resolved`` is exactly what launches.

    Precedence (lowest → highest; ``resolve_argv``/``normalize_argv`` keeps the
    LAST occurrence of each canonical flag)::

        base < profile < model_defaults < chat_template < mmproj
             < slot_overrides < extra_args

    Rationale: profile flags are generic bench-tuning; per-model registry
    ``defaults`` should override the profile; the chat-template / mmproj the
    slot+model resolve to come next; a slot-level ``[model].n_gpu_layers`` beats
    the model default; and a hand-authored ``[server].extra_args`` always wins.
    """
    base: list[str] = ["--host", "0.0.0.0", "--port", str(port)]
    if model_path:
        base += ["--model", model_path]
    # Advertise the hal0 registry model id (else llama-server reports the raw
    # GGUF basename, which the dispatcher can't match to hal0/* virtual names).
    if model_alias:
        base += ["--alias", str(model_alias)]
    # Slot context window (always resolved by _resolve_context_size — never let
    # a slot silently inherit llama-server's 4096 default).
    if context_size is not None:
        base += ["--ctx-size", str(context_size)]

    profile_tokens = shlex.split(profile_flags) if profile_flags and profile_flags.strip() else []

    # Model-registry defaults: shlex-split extra_args + `-ngl <n>` from
    # defaults.n_gpu_layers. rope_freq_base is intentionally NOT emitted
    # (reachable via extra_args only — see ModelDefaults deprecation note).
    md_tokens: list[str] = []
    if model_defaults:
        md_extra = model_defaults.get("extra_args")
        if md_extra and str(md_extra).strip():
            md_tokens += shlex.split(str(md_extra))
        md_ngl = model_defaults.get("n_gpu_layers")
        if md_ngl is not None:
            md_tokens += ["-ngl", str(int(md_ngl))]

    chat_tokens = ["--chat-template-file", chat_template_path] if chat_template_path else []
    mmproj_tokens = ["--mmproj", mmproj] if mmproj else []

    # Slot-level overrides (schema fields), emitted just before extra_args so
    # they beat the profile / model defaults but a hand-authored extra_args
    # still wins. [model].n_gpu_layers (schema default -1 = unset) and the
    # continuous-batching `parallel` field.
    slot_override_tokens: list[str] = []
    if slot_n_gpu_layers is not None and int(slot_n_gpu_layers) >= 0:
        slot_override_tokens += ["-ngl", str(int(slot_n_gpu_layers))]
    if slot_parallel is not None and int(slot_parallel) >= 1:
        slot_override_tokens += ["--parallel", str(int(slot_parallel))]
        if int(slot_parallel) > 1:
            # Unified KV so --ctx-size stays a SHARED pool (each request may use
            # up to the full context) instead of being silently split to ctx/N
            # per sequence slot — the surprise the resolved-command work exists
            # to prevent. See the concurrency-batching plan (D2).
            slot_override_tokens += ["--kv-unified"]

    extra_tokens = shlex.split(extra_args) if extra_args and extra_args.strip() else []

    return [
        ("base", base),
        ("profile", profile_tokens),
        ("model_defaults", md_tokens),
        ("chat_template", chat_tokens),
        ("mmproj", mmproj_tokens),
        ("slot_overrides", slot_override_tokens),
        ("extra_args", extra_tokens),
    ]


def _llama_launch_plan(
    *,
    image: str,
    port: int,
    model_path: str,
    flags_str: str,
    devices: list[str],
    group_ids: list[str],
    context_size: int | None = None,
    extra_args: str | None = None,
    model_alias: str | None = None,
    chat_template_path: str | None = None,
    mmproj: str | None = None,
    model_defaults: dict[str, Any] | None = None,
    slot_n_gpu_layers: int | None = None,
    slot_parallel: int | None = None,
    env: dict[str, str] | None = None,
) -> RuntimeLaunchPlan:
    """Build the GPU/llama-server :class:`RuntimeLaunchPlan`.

    Single source of the llama-server launch shape — used by both
    :meth:`ContainerProvider.container_spec` (the load path) and the
    :func:`_render_unit` scalar shim.  The in-container argv is assembled from
    :func:`_llama_argv_segments` and collapsed by :func:`normalize_argv`
    (last-wins, effective-value-preserving) so cross-segment duplicates become
    one auditable source of truth.  llama-server takes space-separated args
    (``--host HOST``), so they go in ``command`` after the image.
    """
    segments = _llama_argv_segments(
        port=port,
        model_path=model_path,
        model_alias=model_alias,
        context_size=context_size,
        profile_flags=flags_str,
        model_defaults=model_defaults,
        chat_template_path=chat_template_path,
        mmproj=mmproj,
        slot_n_gpu_layers=slot_n_gpu_layers,
        slot_parallel=slot_parallel,
        extra_args=extra_args,
    )
    # resolve_argv over the labelled segments is argv-equivalent to
    # normalize_argv over the flat concatenation (pinned by tests/slots/
    # test_argv.py::test_resolve_argv_equivalent_argv_to_normalize), so launch
    # and preview render byte-identical commands.
    command = resolve_argv(segments).argv

    # Effective model-store root (honours [models].store / HAL0_MODEL_STORE,
    # default /mnt/ai-models). Mounted identical-path, read-only, with an
    # SELinux relabel so it works on enforcing hosts (Fedora).
    model_store = model_store_root()

    return RuntimeLaunchPlan(
        image=image,
        command=command,
        # [server].env → docker run --env (e.g. HSA_OVERRIDE_GFX_VERSION) so
        # operators can tune the runtime without forking the image.
        env=dict(env) if env else {},
        mounts=[Mount(model_store, model_store, read_only=True, selinux="z")],
        devices=list(devices),
        group_add=list(group_ids),
        security_opt=["apparmor=unconfined", "seccomp=unconfined"],
        port=port,
        # Empty network_mode → loopback publish derived from port by the renderer.
        network_mode="",
        health=HealthCheck(cmd=f"curl -fsS http://127.0.0.1:{port}/health || exit 1"),
    )


def _spec_provider_for(slot_cfg: dict[str, Any]) -> Any | None:
    """Provider for a slot, or None for the GPU/llama-server default.

    The runtime family is the authoritative discriminator: a slot's profile
    resolves through :class:`~hal0.profiles.ProfileCatalog` to a
    ``runtime_family`` (flm / kokoro / comfyui / llama-server), so the
    family/quirk knowledge lives in one place instead of being string-matched
    here.  Device/type/provider remain as fallbacks for profile-less slots
    (e.g. a bare ``device=npu`` with no profile set yet).
    """
    family = _profile_runtime_family(slot_cfg)
    device = str(slot_cfg.get("device", ""))
    slot_type = str(slot_cfg.get("type", ""))
    provider = str(slot_cfg.get("provider", ""))

    if family == "flm" or device == "npu":
        from hal0.providers.flm import FLMProvider

        return FLMProvider()
    # Qwen3-TTS (GPU) must be matched by its runtime family BEFORE the generic
    # ``slot_type == "tts"`` → Kokoro fallback, so the two TTS engines coexist:
    # a slot whose profile resolves to the qwen3tts family gets the GPU
    # provider; a profile-less ``type=tts`` slot still falls through to Kokoro.
    if family == "qwen3tts":
        from hal0.providers.qwen3tts import Qwen3TTSProvider

        return Qwen3TTSProvider()
    if family == "kokoro" or slot_type == "tts":
        from hal0.providers.kokoro import KokoroProvider

        return KokoroProvider()
    if family == "comfyui" or provider == "comfyui" or slot_type == "image":
        from hal0.providers.comfyui import ComfyUIProvider

        return ComfyUIProvider()
    return None


def _profile_runtime_family(slot_cfg: dict[str, Any]) -> str | None:
    """Return the slot profile's ``runtime_family`` via ProfileCatalog, or None.

    None when the slot has no profile or the lookup fails — callers fall back
    to device/type discriminators.  Never raises: a malformed or missing
    profile must not break slot dispatch.
    """
    name = str(slot_cfg.get("profile") or slot_cfg.get("slot", {}).get("profile") or "")
    if not name:
        return None
    try:
        return ProfileCatalog().resolve(name).runtime_family
    except Exception:
        return None


# Native context-window resolution (chat@4096 incident, 2026-06-15).
#
# A slot whose [model].context_size is unset must NEVER fall through to
# llama-server's silent 4096 default. We derive the model's native window
# from the registry (GGUF arch max), cap dense models so an unconfigured
# slot can't request an impractically large KV cache, and otherwise use a
# safe floor. Mirrors hal0.hardware.recommend's installer-side policy.
_CTX_SAFE_FALLBACK = 8192
_CTX_DENSE_CAP = 32768


def _native_ctx(model_info: dict[str, Any]) -> int | None:
    """Best-effort native context window for a model, or None if unknown.

    GGUF arch max (registry metadata.context_length) is authoritative;
    a model's own defaults.context_size is the secondary source.
    """
    md = model_info.get("metadata")
    if isinstance(md, dict) and md.get("context_length"):
        try:
            return int(md["context_length"])
        except (TypeError, ValueError):
            pass
    defaults = model_info.get("defaults")
    if isinstance(defaults, dict) and defaults.get("context_size"):
        try:
            return int(defaults["context_size"])
        except (TypeError, ValueError):
            pass
    return None


def _resolve_context_size(explicit: int | None, model_info: dict[str, Any]) -> int:
    """The slot's effective context window.

    The explicit slot value wins when set; otherwise derive the model's
    native window (dense-capped); otherwise a safe _CTX_SAFE_FALLBACK.
    Guarantees a non-None int so the slot never silently inherits
    llama-server's 4096 default.
    """
    if explicit is not None:
        return int(explicit)
    native = _native_ctx(model_info)
    if native:
        return min(native, _CTX_DENSE_CAP)
    return _CTX_SAFE_FALLBACK


def _resolve_llama_scalars(
    slot_cfg: dict[str, Any],
    model_info: dict[str, Any],
    profile: Any,
    *,
    for_launch: bool = False,
) -> dict[str, Any]:
    """Resolve every llama-server launch scalar for a slot+model+profile.

    SINGLE SOURCE for both the launch path (:meth:`ContainerProvider.container_spec`)
    and the preview path (:func:`_resolve_slot_argv`): profile flags (MTP-expanded
    via the slot ``mtp`` override), the always-resolved context size, the
    registry model alias, the resolved chat-template file path, the vision-gated
    mmproj sidecar, the per-model registry ``defaults`` bundle, the slot-level
    ``[model].n_gpu_layers`` override, and the ``[server]`` env / extra_args.
    Returning one dict means the two paths can never drift.

    ``for_launch`` marks the real launch call (vs a status/preview render) —
    it only gates side-effects like the MTP auto-off breadcrumb; the RESOLVED
    VALUES are identical on both paths, preserving launch/preview parity.
    """
    effective_mtp = _effective_mtp(
        slot_cfg.get("mtp"), profile, model_info, log_ineligible=for_launch
    )
    image, flags_str = _profile_image_and_flags(profile, effective_mtp, slot_cfg=slot_cfg)

    slot_parallel = _effective_parallel(slot_cfg)
    if for_launch:
        if effective_mtp and slot_parallel is not None and slot_parallel > 1:
            # MTP x continuous batching runs on current llama.cpp master
            # (parallel drafting merged 2026-05) but is perf-unproven on
            # gfx1151 and needs a build new enough to allow n_parallel>1 with
            # draft-mtp. Surface it; don't refuse (the plan's D3, bench-gated).
            log.info(
                "mtp.batched_speculation",
                extra={
                    "slot": str(slot_cfg.get("name") or ""),
                    "parallel": slot_parallel,
                    "hint": "batched slot — MTP speculation gain unverified here; "
                    "requires a build that allows draft-mtp with --parallel>1",
                },
            )
        workers = slot_cfg.get("workers")
        if workers is not None and int(workers or 1) != 1:
            log.warning(
                "slot.workers_deprecated_inert",
                extra={
                    "slot": str(slot_cfg.get("name") or ""),
                    "workers": workers,
                    "hint": "the 'workers' field is inert (never emitted to argv); "
                    "use the 'parallel' field for continuous batching",
                },
            )

    model_table = slot_cfg.get("model") or {}
    if not isinstance(model_table, dict):
        model_table = {}
    context_size = _resolve_context_size(model_table.get("context_size"), model_info)

    server_table = slot_cfg.get("server") or {}
    if not isinstance(server_table, dict):
        server_table = {}
    extra_args = server_table.get("extra_args")
    server_env = server_table.get("env")
    if not isinstance(server_env, dict):
        server_env = None

    # Registry model id → llama-server --alias so the container advertises the
    # hal0 id (not the raw GGUF basename) for dispatcher matching.
    model_alias = model_info.get("_model_key") or model_table.get("default") or None

    # Resolve chat template: slot override > model defaults.chat_template > None.
    # None/'auto' = GGUF-embedded template (no --chat-template-file flag).
    tmpl_id = resolve_chat_template(slot_cfg, model_info)
    chat_template_path = (
        str(Path(model_store_root()) / "chat-templates" / f"{tmpl_id}.jinja") if tmpl_id else None
    )

    # Vision projector sidecar, gated by the per-slot ``vision`` toggle (#901).
    mmproj = model_info.get("mmproj")
    if not slot_cfg.get("vision", True):
        mmproj = None

    defaults = model_info.get("defaults")
    model_defaults = dict(defaults) if isinstance(defaults, dict) else None

    # Family-architecture overrides (e.g. gemma → f16 KV): the middle layer
    # between the profile's generic flags and the slot's own [model].defaults.
    # Prepended INSIDE the model_defaults segment so it beats the profile
    # (later segment, normalize_argv last-wins) but a per-slot extra_args in
    # [model].defaults still wins over the family default.
    fam = family_flags(
        model_info.get("_model_key"), model_table.get("default"), model_info.get("path")
    )
    if fam:
        if model_defaults is None:
            model_defaults = {}
        existing = model_defaults.get("extra_args") or ""
        model_defaults["extra_args"] = f"{fam} {existing}".strip()

    slot_n_gpu_layers = model_table.get("n_gpu_layers")

    # Multi-GPU pinning (SlotConfig.gpu_index): affects env/devices only —
    # never argv — so launch/preview argv parity is untouched.
    gpu_index = slot_cfg.get("gpu_index")
    try:
        gpu_index = int(gpu_index) if gpu_index is not None else None
    except (TypeError, ValueError):
        gpu_index = None
    if gpu_index is not None and gpu_index < 0:
        gpu_index = None

    return {
        "image": str(image),
        "flags_str": str(flags_str),
        "context_size": context_size,
        "extra_args": extra_args,
        "server_env": server_env,
        "model_alias": model_alias,
        "chat_template_path": chat_template_path,
        "mmproj": str(mmproj) if mmproj else None,
        "model_defaults": model_defaults,
        "slot_n_gpu_layers": slot_n_gpu_layers,
        "slot_parallel": slot_parallel,
        "device_class": str(getattr(profile, "device_class", "gpu") or "gpu"),
        # GPU vendor discriminator: the profile's declared backend (rocm /
        # vulkan / cuda / None) — configuration, not host probing.
        "profile_backend": getattr(profile, "backend", None),
        "device": str(slot_cfg.get("device") or ""),
        "gpu_index": gpu_index,
    }


class ContainerProvider(Provider):
    """Podman-container-per-slot inference backend.

    One instance is shared across all container slots (stateless —
    all config is passed per-call via slot_cfg / model_info, same
    contract as other Providers).

    Public API used by SlotManager:
      load(slot_cfg, model_info)  → writes + starts systemd unit.
      unload(slot_cfg)            → stops systemd unit.
      status(slot_cfg)            → systemctl is-active + /health.
      health(port)                → GET 127.0.0.1:<port>/health.
    """

    name = "container"

    # ── Provider ABC stubs (not used in the container path) ──────────────────

    def build_env(self, slot_cfg: dict[str, Any], model_info: dict[str, Any]) -> dict[str, str]:
        """Informational env block (not written to disk — container is self-contained)."""
        return {
            "HAL0_SLOT": str(slot_cfg.get("name", "")),
            "HAL0_RUNTIME": "container",
            "HAL0_PROFILE": str(slot_cfg.get("profile", "")),
        }

    def start_cmd(self, env: dict[str, str]) -> list[str]:
        """Not applicable — systemd starts the container."""
        raise NotImplementedError("ContainerProvider uses systemd; start_cmd() is unused")

    async def infer(self, port: int, body: dict[str, Any]) -> dict[str, Any]:
        """Direct inference passthrough (used by tests; dispatcher is primary path)."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"http://127.0.0.1:{port}/v1/chat/completions", json=body)
            resp.raise_for_status()
            return resp.json()  # type: ignore[no-any-return]

    def container_spec(
        self, slot_cfg: dict[str, Any], model_info: dict[str, Any]
    ) -> RuntimeLaunchPlan:
        """Build the GPU/llama-server :class:`RuntimeLaunchPlan` for a slot.

        This is the load path for GPU slots: :meth:`load_sync` resolves the
        provider, calls ``container_spec``, and hands the plan to the single
        renderer.  The plan is complete — registry alias, model-registry
        ``defaults``, slot ``context_size``, chat-template, mmproj,
        ``[server].env``/``extra_args``, the read-only model-store mount, and
        the health-check override are all included, so what loads matches what
        the plan describes (no GPU-special argv assembly elsewhere).

        GPU passthrough branches by VENDOR, decided from the slot's declared
        device/profile (never by probing at spec-build time):

        * AMD/other (default): device nodes + group GIDs are included ONLY
          for gpu/img profiles and are existence-filtered — a cpu (or npu)
          ``device_class`` profile gets no ``/dev/kfd`` / ``/dev/dri``
          passthrough or ``--group-add``.
        * NVIDIA (``device=gpu-cuda`` or profile ``backend="cuda"``): CDI —
          ``--device nvidia.com/gpu=all`` (per-index ``nvidia.com/gpu=<n>``
          when ``gpu_index`` is set). CDI names are not paths, so no
          existence filter and no GID ``--group-add`` (the CDI spec injects
          nodes + permissions itself).
        """
        profile_name = slot_cfg.get("profile") or ""
        profile = _resolve_profile_or_base(profile_name, slot_cfg)
        # for_launch: this is the real container-spec build — side-effect logs
        # (MTP auto-off breadcrumb) fire here, never on preview/status renders.
        scalars = _resolve_llama_scalars(slot_cfg, model_info, profile, for_launch=True)

        model_path = _resolve_model_path(model_info)
        port = int(slot_cfg.get("port", 0))

        # GPU plumbing gate (#674/CPU-profile fix): only gpu/img profiles get
        # GPU passthrough. On the AMD path only device paths that actually
        # exist on this host are passed (podman errors on a non-existent
        # --device); CDI entries are names, not paths — passed verbatim.
        if scalars["device_class"] in ("gpu", "img"):
            if is_nvidia_gpu_device(scalars["device"], scalars["profile_backend"]):
                devices = nvidia_cdi_devices(scalars["gpu_index"])
                group_ids = []
            else:
                devices = [p for p in resolve_gpu_device_paths() if os.path.exists(p)]
                group_ids = [str(g) for g in resolve_gpu_group_ids()]
        else:
            devices = []
            group_ids = []

        # gpu_index visibility env (per device family), merged UNDER the
        # slot's [server].env so an operator's explicit key always wins.
        vis_env = gpu_visibility_env(scalars["device"], scalars["gpu_index"])
        server_env = scalars["server_env"] or {}
        merged_env = {**vis_env, **server_env}

        return _llama_launch_plan(
            image=scalars["image"],
            port=port,
            model_path=model_path,
            flags_str=scalars["flags_str"],
            devices=devices,
            group_ids=group_ids,
            context_size=scalars["context_size"],
            extra_args=scalars["extra_args"],
            model_alias=scalars["model_alias"],
            chat_template_path=scalars["chat_template_path"],
            mmproj=scalars["mmproj"],
            model_defaults=scalars["model_defaults"],
            slot_n_gpu_layers=scalars["slot_n_gpu_layers"],
            slot_parallel=scalars["slot_parallel"],
            env=merged_env or None,
        )

    # ── ContainerProvider-specific control plane ──────────────────────────────

    def _unit_name(self, slot_name: str) -> str:
        return f"hal0-slot@{slot_name}.service"

    def _unit_path(self, slot_name: str) -> Path:
        return _SYSTEMD_SYSTEM_DIR / self._unit_name(slot_name)

    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        """Run a subprocess synchronously (load/unload are blocking ops anyway)."""
        return subprocess.run(list(args), capture_output=True, text=True, check=check)

    async def health(self, port: int, slot_cfg: dict[str, Any] | None = None) -> dict[str, Any]:
        """Probe GET /health on the container port.

        For llama-server slots /health returns 200 when ready.
        kokoro-server returns 200 with ``{"model_loaded": false}`` while
        weights are still loading (multi-minute on auto-download) — when the
        body is JSON and carries a ``model_loaded`` key, ok is gated on
        ``model_loaded is True``. Bodies without the key (llama-server) keep
        the plain-200 behavior.

        FLM delegation: when ``slot_cfg`` is supplied and the slot resolves to
        :class:`~hal0.providers.flm.FLMProvider` (via :func:`_spec_provider_for`),
        delegate to that provider's CHEAP liveness probe — non-empty
        ``/v1/models``, no NPU work. A real-inference sentinel must NOT run on
        this path: it is the hot path (2s fail-watch + per-request readiness),
        and a repeating/overlapping NPU completion double-frees the single NPU
        context (status 134). Real inferability is verified once, out of band,
        by ``FLMProvider.verify_inference`` at the warm→ready promotion. The
        probe still distinguishes "up but still loading" (ok=False,
        ``models_endpoint_empty``) from "dead" (transport error) for the
        manager's strike-based fail-watcher. Callers without slot context (the
        legacy port-only path) keep the weak /v1/models fallback below.

        FLM containers have no /health endpoint — they return 404 there.
        When /health returns a non-connection error (e.g. 404), fall back to
        GET /v1/models; 200 there means the server is up and healthy.
        Connection-refused / timeout are always unhealthy (no fallback).

        Returns {"ok": bool, "status": str}.
        """
        if slot_cfg is not None:
            try:
                provider = _spec_provider_for(slot_cfg)
            except Exception:
                provider = None
            from hal0.providers.flm import FLMProvider

            if isinstance(provider, FLMProvider):
                return await provider.health(port)

        health_url = f"http://127.0.0.1:{port}/health"
        models_url = f"http://127.0.0.1:{port}/v1/models"
        try:
            async with httpx.AsyncClient(timeout=_HEALTH_REQUEST_TIMEOUT_S) as client:
                resp = await client.get(health_url)
                if resp.status_code == 200:
                    try:
                        body = resp.json()
                    except ValueError:
                        body = None
                    if isinstance(body, dict) and "model_loaded" in body:
                        loaded = body.get("model_loaded") is True
                        return {
                            "ok": loaded,
                            "status": "healthy" if loaded else "loading",
                        }
                    return {"ok": True, "status": "healthy"}
                # Non-200 (e.g. 404 from FLM) → try /v1/models fallback.
                models_resp = await client.get(models_url)
                ok = models_resp.status_code == 200
                return {
                    "ok": ok,
                    "status": "healthy" if ok else f"http_{models_resp.status_code}",
                }
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            return {"ok": False, "status": str(exc)}

    async def wait_ready(self, port: int) -> None:
        """Poll /health until 200 or HEALTH_TIMEOUT_S exceeded.

        Raises:
            TimeoutError: If the container does not become healthy in time.
        """
        deadline = asyncio.get_event_loop().time() + _HEALTH_TIMEOUT_S
        while asyncio.get_event_loop().time() < deadline:
            h = await self.health(port)
            if h.get("ok"):
                return
            await asyncio.sleep(_HEALTH_POLL_INTERVAL_S)
        raise TimeoutError(
            f"container slot port {port} did not become healthy within {_HEALTH_TIMEOUT_S}s"
        )

    def _write_and_start_unit(self, slot_name: str, unit_text: str) -> None:
        """Write unit file then daemon-reload + enable + restart (shared by all load paths).

        Extracted from :meth:`load_sync` so both the llama-server path and the
        spec-rendered NPU path share a single systemd interaction sequence.
        ``unload_sync`` continues to work unchanged — it uses
        :meth:`_unit_name` / :meth:`_unit_path` which are common.
        """
        unit_path = self._unit_path(slot_name)
        dropin_dir = unit_path.with_name(unit_path.name + ".d")
        if dropin_dir.is_dir():
            # Legacy (pre-container) render_systemd_override drop-ins carry
            # dead EnvironmentFile refs that fail container units (#694 — hit
            # live on the Phase B tts deploy). The container unit is fully
            # self-contained; no drop-in is ever legitimate here.
            shutil.rmtree(dropin_dir)
            log.info(
                "container.stale_dropin_removed",
                extra={"slot": slot_name, "dir": str(dropin_dir)},
            )
        log.info(
            "container.unit_write",
            extra={"slot": slot_name, "unit_path": str(unit_path)},
        )
        unit_path.write_text(unit_text)
        self._run("systemctl", "daemon-reload")
        # Enable so it survives reboots (best-effort — don't fail if already enabled).
        self._run("systemctl", "enable", self._unit_name(slot_name), check=False)
        self._run("systemctl", "restart", self._unit_name(slot_name))
        log.info(
            "container.unit_started",
            extra={"slot": slot_name, "unit": self._unit_name(slot_name)},
        )

    def _render_unit_text(self, slot_cfg: dict[str, Any], model_info: dict[str, Any]) -> str:
        """Render the desired systemd unit text for a slot — the ONE renderer.

        Sole producer of slot-unit text: both :meth:`load_sync` (first install)
        and :meth:`rerender_unit_sync` (update) render through here, so a fresh
        box and an updated box emit **byte-identical** units for the same slot
        config (#1103). Resolves the slot's runtime family to a provider via
        :func:`_spec_provider_for` (FLM/NPU, Kokoro/TTS, ComfyUI/img — else this
        GPU/llama-server provider), builds its :class:`RuntimeLaunchPlan` via
        ``container_spec``, and turns it into ``hal0-slot@<name>.service`` text
        via the one adapter :func:`_render_unit_from_plan`.

        Crucially it threads the live ``[slots].publish_host`` into every
        render. The update path used to drop that argument and fall back to the
        loopback default, so re-rendering a slot on a LAN-exposed box
        (``publish_host = 0.0.0.0``) silently narrowed the bind back to
        ``127.0.0.1`` — the exact fresh-vs-updated divergence WS-J removes.
        """
        slot_name: str = str(slot_cfg.get("name", ""))
        provider = _spec_provider_for(slot_cfg) or self
        plan = provider.container_spec(slot_cfg, model_info)
        log.info(
            "container.unit_render",
            extra={
                "slot": slot_name,
                "unit_path": str(self._unit_path(slot_name)),
                "image": plan.image,
                "port": plan.port,
                "provider": getattr(provider, "name", type(provider).__name__),
            },
        )
        return _render_unit_from_plan(
            slot_name,
            plan,
            runtime_bin=_container_runtime(),
            publish_host=_slot_publish_host(),
        )

    def load_sync(
        self,
        slot_cfg: dict[str, Any],
        model_info: dict[str, Any],
    ) -> None:
        """Write systemd unit, daemon-reload, enable, start (synchronous).

        Called from ``_spawn_locked`` (which is already inside an
        asyncio.to_thread-friendly path — SlotManager awaits the slot spawn
        via ``await self._spawn_locked(...)``).

        Single launch path: unit text comes from the one renderer
        :meth:`_render_unit_text` (shared with the update-time re-render), and
        this method layers on the install-only steps — the NPU loud-fail guard
        plus writing the file and enabling/starting the service.
        """
        slot_name: str = str(slot_cfg.get("name", ""))

        # Loud-fail for NPU slots only: a missing FLM tag must not silently
        # fall through to FLM's legacy build_env default. Kokoro/ComfyUI are
        # self-managed and need no registry tag — the check fires ONLY when
        # device == "npu" and the slot resolves to a spec provider.
        if str(slot_cfg.get("device", "")) == "npu" and _spec_provider_for(slot_cfg) is not None:
            model_table = slot_cfg.get("model") or {}
            tag = (
                model_info.get("flm_tag")
                or model_info.get("_model_key")
                or (model_table.get("default") if isinstance(model_table, dict) else None)
            )
            if not tag:
                raise ValueError("npu slot has no FLM model tag — set [model].default")

        unit_text = self._render_unit_text(slot_cfg, model_info)
        self._write_and_start_unit(slot_name, unit_text)

    def rerender_unit_sync(self, slot_cfg: dict[str, Any], model_info: dict[str, Any]) -> bool:
        """Re-render an EXISTING unit file through current code — without
        touching the running service.

        The unit bakes the launch argv at load time, so after a hal0 update the
        on-disk ExecStart still carries pre-update flags: a bare ``systemctl
        restart`` (or a reboot!) re-runs stale config. This rewrites the unit
        via the same renderer as :meth:`load_sync` — :meth:`_render_unit_text`,
        the sole producer of slot-unit text — but deliberately does NOT
        enable/restart: serving is never bounced by an update; the new argv
        applies on the next start from any path. Callers batch one
        ``daemon_reload`` after a sweep.

        Because both paths render through :meth:`_render_unit_text`, the unit an
        update writes is byte-identical to the one a fresh install would write
        for the same slot config — the WS-J guarantee (#1103).

        Returns True when the unit file changed. No-ops (False) when the slot
        has no unit on disk (never rendered → nothing stale) or the fresh
        render is byte-identical.
        """
        slot_name: str = str(slot_cfg.get("name", ""))
        unit_path = self._unit_path(slot_name)
        if not unit_path.exists():
            return False
        unit_text = self._render_unit_text(slot_cfg, model_info)
        if unit_path.read_text() == unit_text:
            return False
        unit_path.write_text(unit_text)
        log.info(
            "container.unit_rerendered",
            extra={"slot": slot_name, "unit_path": str(unit_path)},
        )
        return True

    def daemon_reload(self) -> None:
        """``systemctl daemon-reload`` — public for the unit-rerender sweep."""
        self._run("systemctl", "daemon-reload")

    def expected_argv(
        self, slot_cfg: dict[str, Any], model_info: dict[str, Any]
    ) -> list[str] | None:
        """Return the freshly-rendered runtime command for drift checks.

        This is the command portion passed to the container image, not the
        podman/systemd preamble. It goes through the same provider plan path as
        ``load_sync`` so derived context size, profile flags, model alias, and
        slot ``[server].extra_args`` match what a restart would render now.
        """
        try:
            provider = _spec_provider_for(slot_cfg) or self
            plan = provider.container_spec(slot_cfg, model_info)
        except Exception:
            return None
        return list(plan.command)

    def unload_sync(self, slot_cfg: dict[str, Any]) -> None:
        """Stop and clean up the container unit (synchronous)."""
        slot_name: str = str(slot_cfg.get("name", ""))
        unit = self._unit_name(slot_name)
        log.info("container.unit_stop", extra={"slot": slot_name, "unit": unit})
        self._run("systemctl", "stop", unit, check=False)
        # Clear a ``failed`` sub-state left by a crash-looped/OOM-killed unit
        # (#1224). Without this, systemd's StartLimit can refuse the next
        # ``systemctl restart``, wedging a slot that a restart should recover.
        self._run("systemctl", "reset-failed", unit, check=False)
        # Disable so it doesn't re-start on reboot.
        self._run("systemctl", "disable", unit, check=False)
        # Remove unit file so daemon-reload leaves no stale entry.
        unit_path = self._unit_path(slot_name)
        if unit_path.exists():
            unit_path.unlink()
            self._run("systemctl", "daemon-reload")

    def is_active(self, slot_name: str) -> bool:
        """Return True if the systemd unit is in an active state."""
        result = self._run("systemctl", "is-active", self._unit_name(slot_name), check=False)
        return result.returncode == 0

    def image_present(self, image: str) -> bool:
        """Return True if ``image`` is in the local container image store.

        Uses ``<runtime> image inspect`` (exit 0 = present, non-zero = missing).
        Runs synchronously — callers must dispatch to a thread executor when
        called from an async context.
        """
        try:
            runtime = _container_runtime()
        except RuntimeError:
            return False
        result = subprocess.run(
            [runtime, "image", "inspect", image],
            capture_output=True,
            check=False,
        )
        return result.returncode == 0

    def running_image(self, slot_name: str) -> str | None:
        """Return the image ref of the running container for *slot_name* (#663).

        Deterministic backend-of-record for a container slot: the running
        backend IS the image tag. Uses ``<runtime> inspect hal0-slot-<name>
        --format {{.ImageName}}``. Returns None when the container is not
        running or inspect fails. Reads stdout only - podman emits benign
        device warnings to stderr under LXC. Never raises; callers dispatch to
        a thread executor from the async status path.
        """
        try:
            runtime = _container_runtime()
        except RuntimeError:
            return None
        container_name = f"hal0-slot-{slot_name}"
        try:
            result = subprocess.run(
                [runtime, "inspect", container_name, "--format", "{{.ImageName}}"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None
        ref = result.stdout.strip()
        return ref or None

    def running_argv(self, slot_name: str) -> list[str] | None:
        """Return the live container command argv for *slot_name*.

        Uses ``<runtime> inspect hal0-slot-<name> --format {{json .Config.Cmd}}``.
        Returns None when the container is not running, inspect fails, or the
        runtime returns an unexpected shape. Never raises; status callers treat
        missing data as "unknown", not drift.
        """
        try:
            runtime = _container_runtime()
        except RuntimeError:
            return None
        container_name = f"hal0-slot-{slot_name}"
        try:
            result = subprocess.run(
                [runtime, "inspect", container_name, "--format", "{{json .Config.Cmd}}"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None
        try:
            payload = json.loads(result.stdout.strip())
        except ValueError:
            return None
        if not isinstance(payload, list):
            return None
        return [str(part) for part in payload]

    async def pull_image_stream(self, image: str):
        """Async generator that runs ``<runtime> pull <image>`` and yields
        layer-progress dicts.

        Yields dicts::

            {"state": "pulling",  "layer": N, "total_layers": M, "line": "<raw line>"}
            {"state": "completed"}
            {"state": "failed",   "error": "<message>"}

        Layer counting heuristic (docker non-TTY output):
          - Each ``Pulling fs layer`` / ``Waiting`` / ``Verifying Checksum`` /
            ``Already exists`` lines indicate a discovered layer (M increments).
          - Each ``Pull complete`` / ``Download complete`` line indicates a
            finished layer (N increments, capped at M).
        """
        import asyncio as _asyncio

        try:
            runtime = _container_runtime()
        except RuntimeError as exc:
            yield {"state": "failed", "error": str(exc)}
            return

        proc = await _asyncio.create_subprocess_exec(
            runtime,
            "pull",
            image,
            stdout=_asyncio.subprocess.PIPE,
            stderr=_asyncio.subprocess.STDOUT,
        )

        total_layers = 0
        done_layers = 0

        try:
            assert proc.stdout is not None
            async for raw in proc.stdout:
                line = raw.decode("utf-8", errors="replace").rstrip()
                if not line:
                    continue
                # Discover new layers.
                if any(
                    kw in line
                    for kw in (
                        "Pulling fs layer",
                        "Waiting",
                        "Verifying Checksum",
                        "Already exists",
                    )
                ):
                    total_layers += 1
                # Count finished layers.
                if (
                    "Pull complete" in line
                    or "Download complete" in line
                    or "Already exists" in line
                ):
                    done_layers = min(done_layers + 1, max(total_layers, 1))
                yield {
                    "state": "pulling",
                    "layer": done_layers,
                    "total_layers": total_layers,
                    "line": line,
                }
        except Exception as exc:
            yield {"state": "failed", "error": str(exc)}
            return
        finally:
            with contextlib.suppress(ProcessLookupError, OSError):
                proc.kill()

        exit_code = await proc.wait()
        if exit_code == 0:
            yield {"state": "completed", "layer": done_layers, "total_layers": total_layers}
        else:
            yield {"state": "failed", "error": f"pull exited with code {exit_code}"}


# ── Module-level singleton (matches the provider-factory pattern) ────────────

_container_provider: ContainerProvider | None = None


def container_provider() -> ContainerProvider:
    """Return the process-wide ContainerProvider singleton."""
    global _container_provider
    if _container_provider is None:
        _container_provider = ContainerProvider()
    return _container_provider


def resolved_command_for_slot(
    slot_cfg: dict[str, Any],
    model_path: str | None = None,
) -> list[str] | None:
    """Return the canonical llama-server argv for a container slot.

    Used by the API layer (GET /api/slots + /config) to surface a
    ``resolved_command`` field without fabricating flags client-side.

    Returns the podman run argv *starting from the image tag* — the
    boilerplate podman preamble (--device, --group-add, --security-opt,
    --volume, --publish) is omitted because:
      a) it requires root to read GIDs (``resolve_gpu_group_ids``), and
      b) it is not useful for debugging inference behaviour.

    Returns ``None`` when the slot has no profile (not a container slot)
    or the profile lookup fails.
    """
    resolved = _resolve_slot_argv(slot_cfg, model_path)
    if resolved is None:
        return None
    image, result = resolved
    return [image, *result.argv]


def _best_effort_model_info(
    slot_cfg: dict[str, Any],
    model_path: str | None = None,
) -> dict[str, Any]:
    """Best-effort model-registry entry for the preview path.

    The preview builders (:func:`resolved_command_for_slot` /
    :func:`resolved_argv_detail_for_slot`) run without a live model download and
    are called with only the slot cfg. To render the SAME argv the launch path
    would (model registry ``defaults``, mmproj sidecar, native context window,
    chat-template), look the model up in the registry — but never raise: a miss
    or an un-wired registry degrades to a minimal ``{_model_key, path}`` dict,
    exactly the best-effort contract the launch path's ``_resolve_model_path``
    already tolerates.
    """
    model_table = slot_cfg.get("model") or {}
    model_id = (
        model_table.get("default", "") if isinstance(model_table, dict) else str(model_table)
    ) or ""
    info: dict[str, Any] = {}
    if model_id:
        info["_model_key"] = model_id
        try:
            from hal0.registry.store import ModelRegistry

            model = ModelRegistry().get(model_id)
            dump = model.model_dump() if hasattr(model, "model_dump") else dict(model)
            info.update(dump)
        except Exception:
            # Registry miss / un-wired / stub — minimal info is fine for preview.
            pass
    effective = model_path or info.get("path") or model_id
    if effective:
        info["path"] = str(effective)
    return info


def _resolve_slot_argv(
    slot_cfg: dict[str, Any],
    model_path: str | None = None,
) -> tuple[str, ResolvedArgv] | None:
    """Build the labelled argv segments for a slot and resolve them.

    Returns ``(image, ResolvedArgv)`` — the deduped flag portion (image
    excluded) plus per-flag provenance — or ``None`` when the slot has no
    profile / the profile lookup fails. Consumes the SAME
    :func:`_llama_argv_segments` builder and :func:`_resolve_llama_scalars`
    resolver the launch path uses, so the previewed argv (including the mtp
    override, model-registry defaults, chat-template file, mmproj, slot ngl
    override, and the always-resolved ctx-size) is byte-identical to what
    :meth:`ContainerProvider.container_spec` would launch.
    """
    profile_name = str(slot_cfg.get("profile") or "")
    if not profile_name:
        return None
    try:
        profile = _resolve_profile_or_base(profile_name, slot_cfg)
        model_info = _best_effort_model_info(slot_cfg, model_path)
        scalars = _resolve_llama_scalars(slot_cfg, model_info, profile)
    except Exception:
        return None

    # port: may be at top-level or nested under [slot]
    port = int(slot_cfg.get("port") or slot_cfg.get("slot", {}).get("port") or 0)

    segments = _llama_argv_segments(
        port=port,
        model_path=str(model_info.get("path") or ""),
        model_alias=scalars["model_alias"],
        context_size=scalars["context_size"],
        profile_flags=scalars["flags_str"],
        model_defaults=scalars["model_defaults"],
        chat_template_path=scalars["chat_template_path"],
        mmproj=scalars["mmproj"],
        slot_n_gpu_layers=scalars["slot_n_gpu_layers"],
        slot_parallel=scalars["slot_parallel"],
        extra_args=scalars["extra_args"],
    )
    return str(scalars["image"]), resolve_argv(segments)


def resolved_argv_detail_for_slot(
    slot_cfg: dict[str, Any],
    model_path: str | None = None,
) -> dict[str, Any] | None:
    """Structured resolution for the dashboard's "resolved command" drawer.

    Returns ``{"argv", "provenance", "removed"}`` where ``provenance`` lists each
    surviving flag with the segment that set its final value (``base`` /
    ``profile`` / ``model_defaults`` / ``chat_template`` / ``mmproj`` /
    ``slot_overrides`` / ``extra_args``) — so an operator can see exactly which
    source won each flag and how many duplicates were collapsed. The UI renders
    these labels generically. ``None`` for a slot with no profile.
    """
    resolved = _resolve_slot_argv(slot_cfg, model_path)
    if resolved is None:
        return None
    image, result = resolved
    return {
        "argv": [image, *result.argv],
        "provenance": [
            {"flag": p.flag, "value": p.value, "source": p.source} for p in result.provenance
        ],
        "removed": result.removed,
    }


__all__ = [
    "ContainerProvider",
    "_render_unit_from_plan",
    "_render_unit_from_spec",
    "_spec_provider_for",
    "container_provider",
    "resolved_argv_detail_for_slot",
    "resolved_command_for_slot",
]


def _image_mismatch(running_image: str | None, declared_image: str | None) -> bool:
    """Return True iff both image refs are known AND differ (#663).

    The deterministic replacement for the /proc ``actual_backend`` sniff on
    container slots: the running backend IS the image tag, so drift is a plain
    ref comparison (the running container's image vs the slot profile's
    declared image). Returns False whenever the running image can't be
    determined (container down / inspect failed) - never cry wolf on missing
    data, same omit-don't-guess contract as ``resolve_actual_backend``.
    """
    if not running_image or not declared_image:
        return False
    return running_image.strip() != declared_image.strip()
