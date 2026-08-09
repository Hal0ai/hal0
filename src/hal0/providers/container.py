"""ContainerProvider — podman-container-per-slot runtime (P1 tracer bullet).

Every slot with ``profile`` set (or ``runtime="container"``) dispatches
through this provider — the sole slot-lifecycle backend.

Architecture (design doc §2):
  - Profile supplies:    image + bench-tuned flags (+ MTP bundle if mtp=true).
  - Slot supplies:       model path, context_size, port.
  - Container provides:  the running llama-server process.

Container lifecycle → Podman Quadlet ``.container`` unit (P3-quadlet):
  /etc/containers/systemd/hal0-slot@<token>.container  (declarative
  [Container] keys; podman's generator emits hal0-slot@<token>.service)
  Image= / Exec= / AddDevice= / Volume= / PublishPort= / Health*= — no more
  hand-rendered ``podman run …`` ExecStart string.

The slot's port is loopback-published (``PublishPort=127.0.0.1:<port>:<port>``)
so the dispatcher can proxy it via a ``kind="remote"`` upstream entry without
exposing it on the LAN.  The publish host is configurable via
``[slots].publish_host`` (``SlotsConfig.publish_host``) — an operator can set
it to ``0.0.0.0`` to reach raw slot ports directly over the LAN
(``http://<host>.local:<port>``); the loopback default is retained for every
install that doesn't opt in.  ``load_sync`` reads the live value and passes it
to :func:`_render_quadlet_from_plan`.

Mount design (IDENTICAL path, design doc §2 gotcha):
  /mnt/ai-models → /mnt/ai-models:ro
  GGUFs in the registry are symlinks whose targets are absolute
  /mnt/ai-models/... paths.  Mounting anywhere else dangles them.

GID resolution (reuses providers/_gpu.py):
  ubuntu:24.04 toolbox images lack ``render``/``video`` group entries.
  Pass numeric GIDs so the kernel gate on /dev/dri/renderD128 passes.

ABC compliance:
  Provider ABC has podman/systemd-shaped methods (build_env, start_cmd,
  container_spec).  ContainerProvider implements container_spec(); unit
  rendering is owned by the module-level ``_render_quadlet_from_plan`` adapter
  (the hand-rendered ``podman run`` string assembly was deleted in P3-quadlet).
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

from hal0.config import store as model_store_module
from hal0.config.paths import (
    DEFAULT_MODEL_STORE,
    _covers,
    model_mount_roots,
    model_store_root,
)
from hal0.config.schema import (
    build_mtp_flag_bundle,
    family_flags,
    resolve_chat_template,
    resolve_profile_flags,
)
from hal0.errors import Hal0Error, UnprocessableEntity
from hal0.http_client import async_client
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
from hal0.slots.activation import autoload_enabled
from hal0.slots.argv import ResolvedArgv, resolve_argv
from hal0.slots.naming import (
    slot_container_name,
    slot_instance_token,
    slot_quadlet_name,
    slot_unit_name,
)
from hal0.system.seam import SystemCtlSeam

# ``ContainerSpec`` is the back-compat alias for ``RuntimeLaunchPlan``; some
# callers/tests still import the old name from this module.
ContainerSpec = RuntimeLaunchPlan


class UnknownRuntimeFamilyError(Hal0Error):
    """A slot profile resolved to a runtime family with no dispatch branch.

    Raised by :func:`_spec_provider_for` instead of silently falling through
    to the llama-server default (which would spawn the wrong binary).
    """

    code = "slot.unknown_runtime_family"
    status = 500


def _artefact_token(slot: Mapping[str, Any] | str) -> str:
    """The runtime-artefact instance token for a probe/query call (#1417).

    The lifecycle half (``load_sync`` / ``unload_sync`` / ``rerender_unit_sync``)
    resolves ``slot_instance_token(slot_cfg)`` before touching a name, so on an
    id-keyed box every artefact is ``hal0-slot@<id>``. The query half
    (:meth:`ContainerProvider.is_active` / ``running_image`` / ``running_argv``)
    used to pass the mutable slot NAME straight into the pure formatters and so
    asked systemd/podman about a pre-migration artefact that does not exist —
    every healthy container slot then read back as offline/stopped. Both halves
    now derive their token here.

    Accepts a slot config mapping (the correct, id-aware input) or an
    already-resolved token string (back-compat for callers that resolved it
    themselves).
    """
    if isinstance(slot, Mapping):
        return slot_instance_token(slot)
    return str(slot)


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
        base = "chat" if "chat" in catalog else (backend if backend in catalog else "rocm")
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


def _effective_backend_and_device_class(
    slot_cfg: Mapping[str, Any] | None, profile: Any
) -> tuple[str | None, str | None]:
    """``(backend, device_class)`` for this lane — the SLOT's device decides.

    SINGLE SOURCE for "what hardware class is this launch" (spec-hw-slot-ownership
    §2/§4.1). Consumed by :func:`_effective_runner` (image/capability resolution)
    AND by :func:`_resolve_llama_scalars` → :meth:`ContainerProvider.container_spec`
    (the real ``/dev/kfd`` + ``/dev/dri`` / NVIDIA-CDI passthrough gate), so the
    image that launches, the capabilities that gate its flags, and the device
    nodes mounted into it can never disagree.

    A PROFILE carries no hardware placement in 1.0. ``profile.device_class`` /
    ``profile.backend`` survive on :class:`~hal0.config.schema.ProfileConfig` only
    as INERT match-only fit hints for :func:`hal0.model_fit.profile_fits_slot`
    (and for the shipped ``.hal0profile.json`` artifacts whose checksums cover
    them). They are read here ONLY as the last-resort fallback for a hand-built
    / pre-pivot ``slot_cfg`` dict that declares no ``device`` at all — every
    real ``SlotConfig`` has one (``device`` defaults to
    :data:`~hal0.model_meta.DEFAULT_DEVICE`), so on a real box the profile never
    contributes.
    """
    if isinstance(slot_cfg, Mapping):
        # 1.0: the slot's ``device`` enum is the authoritative hardware fact.
        device = slot_cfg.get("device")
        if isinstance(device, str) and device:
            from hal0.model_meta import device_to_backend

            recipe, backend = device_to_backend(device)
            device_class = "npu" if recipe == "flm" else ("cpu" if device == "cpu" else "gpu")
            return backend, device_class
        # No ``device`` at all: a pre-pivot TOML or a hand-built dict. Retain the
        # pre-pivot slot-level ``backend`` mirror, then the profile's inert hint.
        sb = slot_cfg.get("backend")
        if isinstance(sb, str) and sb:
            return sb, getattr(profile, "device_class", None)
    return getattr(profile, "backend", None), getattr(profile, "device_class", None)


def _binary_runner(slot_cfg: Mapping[str, Any] | None) -> Any | None:
    """The slot's ``BINARY`` field resolved to a :class:`~hal0.runners.Runner`.

    spec-hw-slot-ownership §2/§3: the slot owns the runner as the typed
    ``binary`` field (a key into :data:`~hal0.runners.RUNNER_IMAGES`),
    replacing the sunset ``model.preferred_runner``. Returns ``None`` when
    ``binary`` is unset/empty or names an unknown key — the caller then falls
    back to the HW-gated default (:func:`hal0.runners.runner_for_backend`).

    BINARY is authoritative and honored directly (the ``(device, BINARY)``
    fit-check WARNS at assignment, not at spawn — spec §4), so unlike the
    prior ``preferred_runner`` shim this does NOT re-gate the chosen runner
    against the lane's backend/device_class; only a genuinely unknown key
    (get_runner miss) is skipped.
    """
    if not isinstance(slot_cfg, Mapping):
        return None
    binary = slot_cfg.get("binary")
    if not (isinstance(binary, str) and binary):
        return None
    from hal0.errors import NotFound
    from hal0.runners import get_runner

    try:
        return get_runner(binary)
    except NotFound:
        return None


def _effective_runner(
    slot_cfg: Mapping[str, Any] | None,
    profile: Any,
    model_info: Mapping[str, Any] | None = None,
) -> Any:
    """The :class:`~hal0.runners.Runner` that actually applies to this launch.

    SINGLE SOURCE for "which runner's capabilities gate this slot" — shared by
    :func:`_resolve_image_ref` (the runner-derived image) and the mtp/jinja
    capability gates in :func:`_effective_mtp` / :func:`_resolve_llama_scalars`,
    so the image that launches and the capabilities that gate its flags can
    never drift onto two different runners.

    Resolution (spec-hw-slot-ownership §2/§3): the slot's ``binary`` field when
    it names a known :data:`~hal0.runners.RUNNER_IMAGES` key
    (:func:`_binary_runner`) — else :func:`hal0.runners.runner_for_backend`,
    the HW-gated default derived from the lane's backend/device_class.

    ``model_info`` is retained for call-site compatibility but no longer
    consulted: the runner is a slot-owned physical fact now, not a model one.
    """
    del model_info  # runner is slot-owned (BINARY), no longer model-derived
    runner = _binary_runner(slot_cfg)
    if runner is not None:
        return runner

    backend, device_class = _effective_backend_and_device_class(slot_cfg, profile)
    from hal0.runners import runner_for_backend

    return runner_for_backend(backend, device_class)


def _resolve_image_ref(
    slot_cfg: Mapping[str, Any] | None,
    profile: Any,
    *,
    model_info: Mapping[str, Any] | None = None,
) -> str:
    """Resolve the container image ref for a slot launch.

    Resolution (spec-hw-slot-ownership §3 — collapses the prior §7.1b chain)::

        image_default = RUNNER_IMAGES[slot.BINARY]     (code registry)
        effective     = slot.image_pin or image_default

      1. ``slot_cfg["image_pin"]`` — the single canonical escape hatch (debug
         build / A-B / rollback-to-last-known-good). A non-empty string is
         honored verbatim, never re-resolved. Promotes the former
         ``_resolve_image_ref`` tiers 1-2 (``slot.image`` / ``[slot].image``)
         to a first-class typed field; those old nestings are folded into
         ``image_pin`` by the migration lane, not read here.
      2. ``image_default`` — :func:`hal0.runners.resolve_runner_image` of the
         :func:`_effective_runner` (the slot's ``binary`` when set, else the
         HW-gated default via :func:`hal0.runners.runner_for_backend`). Same
         runner :func:`_resolve_llama_scalars` uses to gate mtp/jinja, so the
         launched image and its capability gates never drift.

    DELETED vs the prior chain: the ``profile.image`` tier (spec §3 — profiles
    are device-agnostic tune templates carrying no image) and the raw
    ``slot.image`` / ``[slot].image`` string reads (collapsed into
    ``image_pin``). Because ``image_pin`` is its own typed ``str | None`` field
    — disjoint from the ``[image]`` image-gen table (#599, the ``image_gen``
    field) — the prior ``str(dict)`` overload of the shared ``image`` key can
    no longer happen.

    ``model_info`` is retained for call-site compatibility but no longer read.
    """
    del model_info  # image is a slot-owned physical fact now (BINARY/image_pin)
    if isinstance(slot_cfg, Mapping):
        pin = slot_cfg.get("image_pin")
        if isinstance(pin, str) and pin:
            return pin  # escape hatch — honored verbatim, never re-resolved

    # image_default = RUNNER_IMAGES[slot.BINARY] (or the HW-gated default).
    from hal0.runners import resolve_runner_image

    return resolve_runner_image(_effective_runner(slot_cfg, profile))


def _profile_image_and_flags(
    profile: Any,
    mtp_override: bool | None = None,
    slot_cfg: Mapping[str, Any] | None = None,
    *,
    model_info: Mapping[str, Any] | None = None,
) -> tuple[str, str]:
    """Extract ``(image, resolved_flags)`` for a slot launch.

    Image resolution walks the chain in :func:`_resolve_image_ref`
    (spec-hw-slot-ownership §3): ``slot.image_pin`` escape hatch → the
    ``slot.binary``-resolved image (or the HW-gated default).

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

    ``model_info`` is retained for call-site compatibility (threaded through
    from :func:`_resolve_llama_scalars`) but is no longer read by image
    resolution — the image is a slot-owned physical fact now (BINARY /
    image_pin), not a model one.
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
    return _resolve_image_ref(slot_cfg, profile, model_info=model_info), str(flags)


def _effective_mtp(
    model_info: Mapping[str, Any],
    runner: Any,
    *,
    log_ineligible: bool = False,
) -> bool:
    """Resolve whether MTP speculative decoding is on for this slot launch.

    spec-hw-slot-ownership §1: MTP is a MODEL property — the MODEL is the
    single authority; there is no slot-level override anymore (SlotConfig
    carries no ``mtp`` field; a write to that key is hard-rejected at the API
    boundary). ``profile.mtp`` is also not consulted (§7.1a / ML-5).
    Precedence:

    * **model** decides first — an explicit ``ModelDefaults.mtp`` tri-state
      (``True``/``False``) wins in EITHER direction over the registry
      ``mtp`` tag (a curator who explicitly tagged the model's launcher
      defaults knows what they're doing) — the unconditional operator
      escape hatch.
    * **AUTO** (``ModelDefaults.mtp`` is ``None``) enables MTP only when the
      model carries the registry ``mtp`` tag (:func:`hal0.model_meta.
      model_is_mtp_eligible` — no filename/GGUF-name sniffing anymore)
      AND the resolved :class:`~hal0.runners.Runner` actually supports MTP
      drafting (``runner.supports.mtp`` — off for the cuda/cpu llama-server
      lanes).

    This is what stops a non-MTP model / a non-drafting runner lane from
    launching with dead ``--spec-draft-*`` flags, without any per-slot
    wiring for the common case.

    ``log_ineligible`` gates the auto-off breadcrumb to the LAUNCH path only.
    This function sits inside the shared launch/preview scalar resolver
    (:func:`_resolve_llama_scalars`), and the preview path is hit by every
    dashboard ``GET /api/slots`` poll — logging unconditionally here turned a
    once-per-launch hint into a ~0.4/s stream per polling client for every
    AUTO slot on an untagged model.
    """
    defaults = model_info.get("defaults")
    model_mtp = defaults.get("mtp") if isinstance(defaults, Mapping) else None
    if model_mtp is not None:
        return bool(model_mtp)
    eligible = model_is_mtp_eligible(model_info)
    if log_ineligible and not eligible:
        # Visible breadcrumb for the silent-auto-off case: a model tagged
        # neither via defaults.mtp nor the registry 'mtp' tag stops
        # speculating under auto. The fix is tagging the model (or an
        # explicit slot/model mtp=true); this log is how an operator finds
        # that out.
        log.info(
            "mtp.auto_off_model_ineligible",
            extra={
                "model": str(model_info.get("_model_key") or model_info.get("path") or ""),
                "hint": "tag the model 'mtp' (or set defaults.mtp / slot mtp=true) to speculate",
            },
        )
    return eligible and bool(getattr(getattr(runner, "supports", None), "mtp", False))


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

# P3-quadlet: per-slot units are Podman Quadlet ``.container`` source files
# dropped here; podman's systemd generator turns each into a
# ``hal0-slot@<token>.service`` on ``daemon-reload``. Root-owned by design
# (written through the hal0-systemctl seam once hal0-api runs as User=hal0).
_QUADLET_DIR = Path("/etc/containers/systemd")

# P3-perms: the seam that routes unit writes + systemctl verbs through
# `sudo -n hal0-systemctl` once hal0-api runs as the (unprivileged) hal0
# service user — a pure passthrough (today's exact direct behaviour)
# everywhere else, including every existing test. See hal0.system.seam.
_SYSTEMCTL_SEAM = SystemCtlSeam()

# Back-compat alias for the historic default. The *effective* mount root is
# resolved per-render via model_store_root() ([models].store / HAL0_MODEL_STORE
# / this default) so a custom model directory actually reaches the container.
_MODEL_STORE_MOUNT = DEFAULT_MODEL_STORE


# Container runtime binary. Podman ONLY — Quadlet is podman's declarative unit
# generator and has no docker equivalent, so docker is unsupported (P3-quadlet;
# the old docker fallback + its ``--replace`` / ``ExecStartPre rm`` cargo cult
# is deleted). HAL0_CONTAINER_RUNTIME still pins a specific path for CI /
# alternate installs.
def _container_runtime() -> str:
    """Resolve the podman binary path (docker is unsupported).

    Priority: $HAL0_CONTAINER_RUNTIME > /usr/bin/podman > ``podman`` on PATH.
    The absolute-path candidate is checked first (the common,
    package-manager-installed location); the bare-name PATH lookup is the
    fallback for podman installed somewhere else (snap, /usr/local/bin, nix,
    ...) — a plain ``shutil.which("/usr/bin/podman")`` never matches those.
    Raises RuntimeError if podman is not found.
    """
    override = os.environ.get("HAL0_CONTAINER_RUNTIME")
    if override:
        return override
    for candidate in ("/usr/bin/podman", "podman"):
        found = shutil.which(candidate)
        if found:
            return found
    raise RuntimeError("no podman runtime found; install podman or set HAL0_CONTAINER_RUNTIME")


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


def _slot_network_mode() -> str:
    """Live ``[slots].network_mode`` — the box-default podman network mode.

    Deploy-time configuration, NOT runtime substrate probing: an operator (or
    the installer) sets this to ``host`` on netns-limited substrates
    (unprivileged podman-in-LXC, where bridge netns teardown races — see
    docs/rework/podman-unprivileged-findings.md) so every slot renders
    ``Network=host``. The renderer never sniffs podman/LXC itself.

    Empty (the default) means "bridge + loopback PublishPort" — today's
    behaviour, unchanged. Read fresh each render so a Settings change lands on
    the next slot (re)start. Fail-soft to "" so a malformed hal0.toml never
    wedges a slot start (and never silently widens exposure).
    """
    try:
        from hal0.config.loader import load_hal0_config

        return (load_hal0_config().slots.network_mode or "").strip()
    except Exception:
        log.warning("container.network_mode_load_failed", exc_info=True)
        return ""


# ── host-net loopback fence (podman-unprivileged-findings.md, Issue 1) ───────
# The slot process's listen-address flags. Under BRIDGE networking the LAN
# fence is the ``PublishPort=127.0.0.1:<port>:<port>`` pin and the process
# correctly binds ``0.0.0.0`` inside its own netns. Under ``Network=host``
# there is NO publish, so the bind itself must be the fence — every backend
# templates its listen address as one of these flags.
_BIND_FLAGS = ("--host", "--listen")
_LAN_BIND = "0.0.0.0"
_LOOPBACK_BIND = "127.0.0.1"


def _loopback_fence_command(command: list[str]) -> list[str]:
    """Flip any ``0.0.0.0`` bind in *command* to loopback (host-net fence).

    THE single chokepoint coupling ``Network=host`` to a loopback process bind
    (podman-unprivileged-findings.md, Issue 1, operator-validated on halo143):
    under host networking there is no ``PublishPort=127.0.0.1:…`` fencing the
    raw slot port off the LAN, so an unflipped ``--host 0.0.0.0`` would bind the
    CT's LAN IP and expose the unauthenticated slot port. Applied uniformly to
    EVERY backend's plan, so a host-net plan can never render a 0.0.0.0 bind
    regardless of which provider built the argv:

      * llama-server / FLM / Kokoro / Qwen3-TTS template ``--host 0.0.0.0``
        (two argv tokens);
      * ComfyUI templates ``--listen 0.0.0.0`` inside a ``bash -lc`` payload
        string (one token).

    Both the split-token and embedded-in-a-shell-string forms (and the inline
    ``--host=0.0.0.0`` form) are rewritten. hal0-api, sharing the CT netns,
    still reaches the slot at ``127.0.0.1:<port>`` — dispatch is unchanged.
    Bridge-mode plans never call this.
    """
    out: list[str] = []
    i = 0
    n = len(command)
    while i < n:
        tok = command[i]
        # Split-token form: `--host 0.0.0.0` / `--listen 0.0.0.0`.
        if tok in _BIND_FLAGS and i + 1 < n and command[i + 1] == _LAN_BIND:
            out.append(tok)
            out.append(_LOOPBACK_BIND)
            i += 2
            continue
        # Inline `--host=0.0.0.0` and shell-payload `--listen 0.0.0.0`
        # embedded inside a single token (ComfyUI's bash -lc string).
        rewritten = tok
        for flag in _BIND_FLAGS:
            rewritten = rewritten.replace(f"{flag}={_LAN_BIND}", f"{flag}={_LOOPBACK_BIND}")
            rewritten = rewritten.replace(f"{flag} {_LAN_BIND}", f"{flag} {_LOOPBACK_BIND}")
        out.append(rewritten)
        i += 1
    return out


# Health-check tuning: poll GET /health on the slot port.
_HEALTH_POLL_INTERVAL_S = 2.0
_HEALTH_TIMEOUT_S = 180.0
_HEALTH_REQUEST_TIMEOUT_S = 3.0

#: Wall-clock bound on the teardown systemd verbs issued by
#: :meth:`ContainerProvider.unload_sync` (#1224). systemd's OWN
#: ``TimeoutStopSec`` does not help once a unit is already parked in
#: ``failed`` — the client-side ``systemctl stop`` can block indefinitely — so
#: the child process is bounded here instead.
#:
#: This is the WORKER-side bound and it complements, rather than duplicates,
#: :meth:`hal0.slots.manager.SlotManager.terminate`'s caller-side one. That
#: bound lets the *request* return; it cannot touch the executor thread, which
#: keeps sitting on the child. Without this the thread leaks for the life of
#: the process AND — the part that actually matters — ``unload_sync`` never
#: reaches the Quadlet-source removal + ``daemon-reload`` below, so the
#: generated ``.service`` stays on disk in ``failed`` and the "subsequent load
#: still converges" promise does not hold. Deliberately under the caller's
#: budget so the worker unwinds first.
_UNIT_STOP_TIMEOUT_S = 20.0


def _resolve_model_path(model_info: dict[str, Any]) -> str:
    """Return the absolute GGUF path for this model.

    Prefers ``model_info["path"]`` (populated by ModelRegistry.get);
    falls back to ``model_info["_model_key"]`` (the model-id string)
    so the container can attempt to locate the file at runtime.

    ML-3: also runs a best-effort store-escape sanity check via
    ``store.assert_under_store(..., severity="warn")`` — WARN, never
    fail-fast, because this resolves for an already-running (or about to
    launch) slot and a sanity check must never be the thing that kills a
    live container (plan §23.3a's severity split; the write path in
    ``registry/pull.py`` is where escape attempts fail fast instead).
    """
    path = model_info.get("path") or model_info.get("_model_key", "")
    if not path:
        raise ValueError(
            "model_info has no 'path' — registry lookup failed; "
            "ensure the model is registered before loading a container slot."
        )
    with contextlib.suppress(Exception):
        model_store_module.assert_under_store(str(path), severity="warn")
    return str(path)


def _render_quadlet_from_plan(
    instance_token: str,
    plan: RuntimeLaunchPlan,
    *,
    publish_host: str = "127.0.0.1",
    network_mode_default: str = "",
    autoload: bool = True,
) -> str:
    """Render a Podman Quadlet ``.container`` unit from a launch plan.

    The ONE renderer for every container slot — GPU/llama-server, FLM NPU,
    Kokoro / Qwen3-TTS, and ComfyUI all flow through here. It replaces the
    hand-rendered ``podman run …`` ExecStart string assembly: every flag becomes
    a typed ``[Container]`` key (``Image=`` / ``AddDevice=`` / ``GroupAdd=`` /
    ``SecurityOpt=`` / ``Volume=`` / ``Environment=`` / ``PublishPort=`` /
    ``Health*=``), and podman's systemd generator emits the ``[Unit]/[Service]``
    skeleton + the ``podman run`` itself. Quadlet auto-removes the prior
    container on start (no more ``--replace`` / ``ExecStartPre rm`` dance) and
    orders the unit after its bind mounts (auto-emitted ``RequiresMountsFor=``).

    Args:
        instance_token: The name-based instance token today (§11.1 M5 flips it
                        to the slot id via :func:`hal0.slots.naming`). Used for
                        the ``ContainerName=`` (``hal0-slot-<token>``), the
                        ``Description=``, and the ``SyslogIdentifier=``.
        plan:           :class:`RuntimeLaunchPlan` from a provider's
                        ``container_spec``.
        publish_host:   Host address the slot port publishes on
                        (``PublishPort=<host>:<port>:<port>``). Defaults to
                        ``127.0.0.1`` (loopback); ``load_sync`` widens it from
                        the live ``[slots].publish_host``.
        network_mode_default:
                        Box-default podman network mode from
                        ``[slots].network_mode`` (deploy-time config, threaded
                        in by ``_render_quadlet_text``). Used ONLY when the
                        plan does not itself pin a mode — a provider that
                        REQUIRES host net (ComfyUI) always wins. When the
                        effective mode is ``host`` this couples two things in
                        one place: ``Network=host`` + no ``PublishPort`` (the
                        publish would be a no-op), and the process bind is
                        flipped to loopback (:func:`_loopback_fence_command`)
                        so the raw slot port is never LAN-exposed
                        (podman-unprivileged-findings.md, Issue 1).
    """
    container_name = slot_container_name(instance_token)
    # Effective network mode: an explicit per-plan mode (e.g. ComfyUI's
    # required ``host``) wins; otherwise the deploy-time box default. This is a
    # PURE function of (plan, params) — the config value is resolved upstream
    # in _render_quadlet_text, never sniffed here.
    effective_network_mode = plan.network_mode or network_mode_default

    lines: list[str] = [
        "# hal0 container slot — generated by ContainerProvider (Podman Quadlet).",
        "# Do not edit manually; regenerated on every slot load.",
        "",
        "[Unit]",
        f"Description=hal0 container inference slot ({instance_token})",
        # StartLimit*= are [Unit]-section directives (systemd.unit(5)), not
        # [Service]. Quadlet passes [Unit] through verbatim to the generated
        # unit, so they belong here — emitting them under [Service] makes
        # systemd log "Unknown key" and SILENTLY DROP them, disabling the
        # slot's restart rate-limiting (install-validation m2, halo150,
        # 2026-07-19). Restart=/RestartSec= stay in [Service] below — those
        # ARE Service-section keys.
        "StartLimitIntervalSec=300",
        "StartLimitBurst=5",
        "",
        "[Container]",
        f"Image={plan.image}",
        f"ContainerName={container_name}",
        # ``LogDriver=none`` keeps conmon→journal the single sink so
        # ``journalctl -u`` isn't double-fed (podman's own journald driver
        # would tag a second copy — the old B3 note).
        "LogDriver=none",
    ]
    # ── ONE quadlet render for every substrate (halo150/143 O8+O11) ──────
    # DELIBERATE: no native AutoRemove=/GroupAdd=/SecurityOpt= keys and no
    # podman-version branch. Rationale, proven on real hardware 2026-07-18:
    #   * 4.x generators HARD-FAIL the conversion on those keys (halo150,
    #     podman 4.9.3 — the lxc105 live-reference substrate) → no unit.
    #   * The native render's systemd lifecycle breaks on unprivileged
    #     podman-5-in-LXC (halo143, 5.7): netavark teardown races
    #     /run/user/0/netns and the unit exits 5, while the SAME container
    #     runs clean under manual podman run — the unit shape, not the
    #     flags, is the problem. The compat render ran healthy there.
    #   * ``PodmanArgs=`` is the documented, version-stable escape hatch and
    #     is semantically identical for --group-add/--security-opt.
    # AutoRemove/--rm is dropped everywhere: stop-cleanup rides the generated
    # unit's own cidfile ExecStopPost rm; crash-path auto-remove hid failure
    # logs and fed the netns race. One render = one behavior to validate
    # (both-boxes policy). If a future podman makes native keys strictly
    # better, reintroduce them fleet-wide with hardware evidence, not a
    # version sniff.
    compat_args: list[str] = []
    if effective_network_mode:
        lines.append(f"Network={effective_network_mode}")
    # Explicit device nodes (podman won't recurse /dev/dri, #674); CDI names
    # (nvidia.com/gpu=all) pass through the same key.
    for dev in plan.devices:
        lines.append(f"AddDevice={dev}")
    # Numeric GIDs for video+render groups (ubuntu:24.04 has no group names).
    for gid in plan.group_add:
        compat_args += ["--group-add", str(gid)]
    for cap in plan.cap_add:
        lines.append(f"AddCapability={cap}")
    for opt in plan.security_opt:
        compat_args += ["--security-opt", str(opt)]
    if compat_args:
        lines.append("PodmanArgs=" + " ".join(shlex.quote(a) for a in compat_args))
    # Read-only + SELinux ``:z`` are first-class Mount flags; render_quadlet omits
    # the relabel on NFS sources (chcon ENOTSUP there).
    for mount in plan.mounts:
        lines.append(f"Volume={Mount.coerce(mount).render_quadlet()}")
    for k, v in plan.env.items():
        lines.append(f"Environment={k}={v}")
    # Publish derived from plan.port (declarative). Skipped under host networking
    # where port publishing is meaningless. ``publish_host`` defaults to loopback;
    # an operator widens it via [slots].publish_host.
    if plan.port and effective_network_mode != "host":
        lines.append(f"PublishPort={publish_host}:{plan.port}:{plan.port}")
    # Healthcheck override (#684): the toolbox image bakes a HEALTHCHECK on a
    # hardcoded port; the slot runs on its own port, so override it declaratively.
    if plan.health is not None:
        lines.extend(plan.health.render_quadlet())
    # extra_args escape hatch (e.g. ``--ipc=host``, ``--ulimit memlock=-1``):
    # Quadlet has no typed key for arbitrary ``podman run`` flags, so they pass
    # through ``PodmanArgs=`` verbatim. DEPRECATED — kept working for one release
    # so an operator's hand-authored extra_args survives the Quadlet upgrade.
    podman_args: list[str] = []
    for extra in plan.extra_args:
        podman_args.extend(shlex.split(extra))
    if podman_args:
        log.warning(
            "container.extra_args_deprecated",
            extra={
                "slot": instance_token,
                "podman_args": podman_args,
                "hint": "extra_args is deprecated; move these to a "
                "hal0-slot@<token>.container.d/ drop-in with typed Quadlet keys",
            },
        )
        lines.append("PodmanArgs=" + " ".join(shlex.quote(a) for a in podman_args))
    # Exec = the in-container argv (after the image). Every token is
    # ``shlex.quote``-d — NOT the old "quote only if it has a space" rule — so a
    # space-less token carrying shell/systemd-special characters (notably a JSON
    # blob like ``--chat-template-kwargs '{"enable_thinking":false}'``) survives
    # systemd's Exec= word-splitter with its double quotes intact. The bare form
    # let systemd strip the inner quotes → invalid JSON → slot never starts
    # (regression #, pinned by tests/providers/test_container.py).
    if plan.command:
        # host-net LAN fence (coupled to Network=host above): with no
        # PublishPort to pin the port to loopback, the process bind IS the
        # fence — flip 0.0.0.0 → 127.0.0.1 for EVERY backend so a host-net plan
        # can never render a 0.0.0.0 bind. Bridge mode keeps 0.0.0.0 (the
        # 127.0.0.1 PublishPort pins it). See podman-unprivileged-findings.md.
        command = (
            _loopback_fence_command(plan.command)
            if effective_network_mode == "host"
            else plan.command
        )
        lines.append("Exec=" + " ".join(shlex.quote(t) for t in command))

    # Restart=always lets systemd own crash recovery (the old hand-rendered
    # Restart=no forced the manager to reap+restart every failed slot). The
    # StartLimit caps (emitted in [Unit] above — systemd.unit(5), not
    # [Service]) match the manager-driven behaviour it replaces.
    lines.extend(
        [
            "",
            "[Service]",
            "Restart=always",
            "RestartSec=3",
            f"SyslogIdentifier={container_name}",
            "StandardOutput=journal",
            "StandardError=journal",
        ]
    )
    # [Install] WantedBy=hal0.target is the ONLY thing that boot-starts a
    # slot (Quadlet's generator links the .wants symlink from unit content —
    # no systemctl enable anywhere). autoload=false omits it wholesale: the
    # unit stays start-able (load/swap use `systemctl restart`, which never
    # consulted [Install]) but nothing pulls it up at boot. Spec 2026-08-02.
    if autoload:
        lines.extend(["", "[Install]", "WantedBy=hal0.target"])
    lines.append("")
    return "\n".join(lines)


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
    slot_threads: int | None = None,
    slot_parallel: int | None = None,
    extra_args: str | None = None,
    slot_profile_flags: str = "",
) -> list[tuple[str, list[str]]]:
    """Build the ordered, labelled llama-server argv segments (SINGLE SOURCE).

    Both the launch path (:func:`_llama_launch_plan` → :func:`normalize_argv`)
    and the preview path (:func:`_resolve_slot_argv` → :func:`resolve_argv`
    provenance) consume THESE segments, so what an operator previews via
    ``GET /api/slots`` / ``.../resolved`` is exactly what launches.

    Ownership split (spec-hw-slot-ownership §2, reversing spec-flags-ownership
    §5): the SLOT owns the physical hardware grid (NGL → ``-ngl``, THREADS →
    ``--threads``; ``-dev`` rides the GPU-visibility path, not here) as typed
    fields, emitted in a TRUSTED ``slot_hardware`` segment. The MODEL still owns
    the logical, device-agnostic tune — free-form ``defaults.extra_args``
    (``model_extra_args`` segment, still screened against the §21.7 managed-arg
    denylist by :func:`~hal0.slots.argv.resolve_argv`). The old ``profile``
    segment stays removed — a profile is a copy-on-stamp template, read at
    stamp time, not at launch, whenever the slot's profile matches the model's
    stamped provenance (``defaults.profile``).

    Divergence overlay (#1636): when the slot's profile DIFFERS from the
    model's provenance, ``slot_profile_flags`` carries that profile's resolved
    flag text and is emitted as an UNTRUSTED ``slot_profile`` segment layered
    over the model tune — the per-slot flag-divergence path that replaced the
    retired duplicate-for-device model flow (PR #1635). The caller
    (:func:`_resolve_llama_scalars`) computes it divergence-gated; aligned and
    provenance-less slots pass ``""`` and launch byte-identical to the stamped
    tune.

    Precedence (lowest → highest; ``resolve_argv``/``normalize_argv`` keeps the
    LAST occurrence of each canonical flag)::

        base < model_extra_args < slot_profile < slot_hardware < chat_template < mmproj

    The slot hardware segment sits AFTER ``model_extra_args`` and
    ``slot_profile`` so the slot's typed ``-ngl``/``--threads`` win over any
    collision a model tune or divergent profile smuggled in (defense in depth
    — the model/profile flag save also hard-rejects hardware flags, spec §5).

    ``profile_flags`` / ``slot_parallel`` / ``extra_args`` are accepted-and-
    ignored (kept for call-site compat until the sunset ratchet drops the
    plumbing).
    """
    # HAL0-SUNSET: v1.0.0 — profile flags + slot parallel/extra_args lost their
    # launch surface (spec-flags-ownership §2/§4). These params are inert; drop
    # them (and the callers threading them) once the sunset ratchet lands.
    del profile_flags, slot_parallel, extra_args

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

    # Model logical tune: the free-form ``defaults.extra_args``. It is
    # caller-supplied, so it rides its own ``model_extra_args`` segment which
    # ``resolve_argv`` screens against the managed-arg denylist
    # (argv.UNTRUSTED_SEGMENT_LABELS) — a model whose extra_args smuggles
    # ``--port``/``--model``/… fails loudly at launch, not silently redirected.
    # rope_freq_base is intentionally NOT emitted (reachable via extra_args
    # only — see ModelDefaults deprecation note). ``-ngl`` NO LONGER comes from
    # the model (defaults.n_gpu_layers is deleted); it is a slot-owned field.
    md_extra_tokens: list[str] = []
    if model_defaults:
        md_extra = model_defaults.get("extra_args")
        if md_extra and str(md_extra).strip():
            md_extra_tokens += shlex.split(str(md_extra))

    # Slot hardware grid (spec-hw-slot-ownership §2): the slot owns the physical
    # knobs as typed fields — NGL → ``-ngl``, THREADS → ``--threads``. These are
    # hal0-computed from typed SlotConfig fields, so they ride a TRUSTED segment
    # (not screened against the managed-arg denylist). ``-ngl -1`` (all layers)
    # is a legitimate explicit value. ``--threads`` is omitted when unset (0) so
    # the runtime picks its own default. ``-dev`` is emitted from the device
    # enum on the GPU-visibility path (gpu_visibility_env), not in this segment.
    slot_hw_tokens: list[str] = []
    if slot_n_gpu_layers is not None:
        slot_hw_tokens += ["-ngl", str(int(slot_n_gpu_layers))]
    if slot_threads is not None and int(slot_threads) > 0:
        slot_hw_tokens += ["--threads", str(int(slot_threads))]

    chat_tokens = ["--chat-template-file", chat_template_path] if chat_template_path else []
    mmproj_tokens = ["--mmproj", mmproj] if mmproj else []

    # Divergent slot-profile overlay (#1636): free-form/operator-editable, so it
    # rides an UNTRUSTED label (managed-arg screen in resolve_argv). Sits above
    # the model tune (the divergence wins collisions) but below slot_hardware
    # (typed slot fields stay authoritative). The segment appears ONLY when a
    # divergence produced flags — the aligned case keeps the exact golden #5
    # segment shape (tests/golden_paths/test_gp05_stamped_launch_layering.py).
    segments: list[tuple[str, list[str]]] = [
        ("base", base),
        ("model_extra_args", md_extra_tokens),
    ]
    if slot_profile_flags.strip():
        try:
            _profile_tokens = shlex.split(slot_profile_flags)
        except ValueError as exc:
            # A profile's flags string is free text (operator-editable on
            # disk) and the create/edit path has no quoting validator —
            # unmatched quotes only surface here, where an unhandled
            # ValueError would otherwise 500 the launch/preview. Fail with a
            # controlled config error instead.
            raise UnprocessableEntity(
                f"divergent slot profile flags are not valid shell text: {exc}",
                code="slot.profile_flags_malformed",
                details={"flags": slot_profile_flags},
            ) from exc
        # jinja is a runner+model CAPABILITY, resolved once as
        # ``effective_jinja`` and injected/suppressed through
        # ``model_defaults.extra_args`` above — never through a raw profile
        # flag. llama-server has no ``--no-jinja`` negation, so a legacy or
        # hand-authored profile's own ``--jinja`` token would otherwise
        # permanently defeat an explicit ``defaults.jinja=false`` once it
        # rides the (higher-precedence) slot_profile segment. Strip it so
        # the overlay can never outrank the already-computed capability.
        _profile_tokens = [t for t in _profile_tokens if t != "--jinja"]
        if _profile_tokens:
            segments.append(("slot_profile", _profile_tokens))
    segments += [
        ("slot_hardware", slot_hw_tokens),
        ("chat_template", chat_tokens),
        ("mmproj", mmproj_tokens),
    ]
    return segments


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
    slot_threads: int | None = None,
    slot_parallel: int | None = None,
    env: dict[str, str] | None = None,
    slot_profile_flags: str = "",
) -> RuntimeLaunchPlan:
    """Build the GPU/llama-server :class:`RuntimeLaunchPlan`.

    Single source of the llama-server launch shape — used by
    :meth:`ContainerProvider.container_spec` (the load path).  The in-container
    argv is assembled from
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
        slot_threads=slot_threads,
        slot_parallel=slot_parallel,
        extra_args=extra_args,
        slot_profile_flags=slot_profile_flags,
    )
    # resolve_argv over the labelled segments is argv-equivalent to
    # normalize_argv over the flat concatenation (pinned by tests/slots/
    # test_argv.py::test_resolve_argv_equivalent_argv_to_normalize), so launch
    # and preview render byte-identical commands.
    command = resolve_argv(segments).argv

    # Model-store roots (honours [models].store / HAL0_MODEL_STORE AND
    # [models].pull_root — the external tree registry paths point at, e.g.
    # /mnt/ai-models). Mounting ONLY the effective store left model files under
    # pull_root unreachable → llama exits ~90ms after start → slot flaps
    # error↔warming (rework O25). Each root is mounted identical-path,
    # read-only, via the shared `mount_for` factory (ML-3) — which omits the
    # SELinux relabel on NFS (chcon ENOTSUP there) instead of unconditionally
    # appending ``:z``. `model_mount_roots()` dedups equal/nested roots so
    # store==pull_root renders exactly ONE Volume.
    model_stores = model_mount_roots()

    # Defensive reachability (rework O25 follow-up): a slot's resolved model
    # file can live OUTSIDE every *configured* model root (store/pull_root) —
    # e.g. a registry path under /mnt/ai-models after [models].store was moved
    # to /var/lib/hal0/models. `assert_under_store(severity="warn")` +
    # `doctor models HAL0-MODEL-STORE-UNMOUNTED` DETECT this but don't heal it,
    # so the container still can't read the file → llama exits at load → the
    # slot crashes/flaps warming↔error (live-proven on Strix Halo 150: 6 models
    # under /mnt/ai-models flagged, store pointed at /var/lib/hal0/models).
    # Mount the model file's own directory (identical-path, read-only) whenever
    # no configured root already covers it, so the slot loads regardless of the
    # store-config drift (the guard still surfaces the misconfig to fix).
    if model_path:
        mp = os.path.normpath(model_path)
        if not any(_covers(root, mp) for root in model_stores):
            model_dir = os.path.dirname(mp)
            if model_dir and not any(_covers(root, model_dir) for root in model_stores):
                model_stores = [*model_stores, model_dir]

    return RuntimeLaunchPlan(
        image=image,
        command=command,
        # [server].env → docker run --env (e.g. HSA_OVERRIDE_GFX_VERSION) so
        # operators can tune the runtime without forking the image.
        env=dict(env) if env else {},
        mounts=[model_store_module.mount_for(root, read_only=True) for root in model_stores],
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
    # Moonshine (CPU STT). NPU transcription never reaches here — the FLM
    # branch above matches on family/device first, so this generic
    # ``slot_type == "transcription"`` fallback (for profile-less stt slots)
    # can only mean the CPU engine, mirroring the tts → Kokoro fallback.
    if family == "moonshine" or slot_type == "transcription":
        from hal0.providers.moonshine import MoonshineProvider

        return MoonshineProvider()
    if family == "comfyui" or provider == "comfyui" or slot_type == "image":
        from hal0.providers.comfyui import ComfyUIProvider

        return ComfyUIProvider()
    if family not in (None, "llama-server"):
        # A runtime family with no dispatch branch above. This can only
        # happen when someone extends the RuntimeFamily literal without
        # teaching this resolver about it — silently falling through to the
        # llama-server default would spawn the wrong binary, so fail loudly
        # instead (the F3 fragility, made explicit).
        raise UnknownRuntimeFamilyError(
            f"no provider is registered for runtime family {family!r} — "
            "extend _spec_provider_for alongside the RuntimeFamily literal",
            details={"runtime_family": family, "slot_type": slot_type},
        )
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


# Context-window resolution (chat@4096 incident, 2026-06-15).
#
# A slot must NEVER fall through to llama-server's silent 4096 default. When
# the model declares no window of its own we derive it from the registry's
# GGUF arch max, cap dense models so an unconfigured slot can't request an
# impractically large KV cache, and otherwise use a safe floor. Mirrors
# hal0.hardware.recommend's installer-side policy.
_CTX_SAFE_FALLBACK = 8192
_CTX_DENSE_CAP = 32768

#: Plausibility bounds for an EXPLICIT ``defaults.context_size`` on the launch
#: path (#1414). Mirrors the write-boundary range check so the two agree; kept
#: as a separate launch-side guard on purpose, because the write boundary can
#: only screen NEW writes — rows persisted before that screen existed (0, -1,
#: 99999999 all returned HTTP 200) are deliberately left readable so they stay
#: fixable, which means they are still on disk and still reach this function.
#:
#: Before the v1.0 ownership flip the damage was masked: the slot's
#: ``[model].context_size`` won outright, so a slot with a sane value covered
#: for a garbage model row. Now the MODEL is authoritative and a bad value goes
#: straight to ``--ctx-size``, where -1 or 99999999 is a slot that never warms.
#: Making the model the owner without this guard would have ENTRENCHED #1414.
_CTX_MIN_PLAUSIBLE = 128
_CTX_MAX_PLAUSIBLE = 2**24


def _model_declared_ctx(model_info: dict[str, Any]) -> int | None:
    """The MODEL's own explicit context window (``defaults.context_size``).

    This is the authoritative value under the v1.0 ownership split — an
    operator's deliberate, model-scoped choice made in the model drawer. It is
    NOT dense-capped: an explicit number means what it says.

    An unparseable or implausible value (outside
    ``[_CTX_MIN_PLAUSIBLE, _CTX_MAX_PLAUSIBLE]``) is treated as ABSENT, not
    clamped — a half-applied garbage number is harder to diagnose than falling
    through to the native window or the safe floor, and the operator's stated
    intent is unrecoverable either way. Logged at warning so the bad row is
    visible in ``journalctl -u hal0-api`` instead of surfacing as a slot that
    silently never warms.
    """
    defaults = model_info.get("defaults")
    if not isinstance(defaults, dict) or not defaults.get("context_size"):
        return None
    raw = defaults["context_size"]
    try:
        value = int(raw)
    except (TypeError, ValueError):
        log.warning(
            "slot.context_size_unparseable",
            extra={
                "raw": repr(raw),
                "hint": "model defaults.context_size is not a number; ignoring it and "
                "falling back to the model's native window. Fix it in the model drawer.",
            },
        )
        return None
    if not (_CTX_MIN_PLAUSIBLE <= value <= _CTX_MAX_PLAUSIBLE):
        log.warning(
            "slot.context_size_implausible",
            extra={
                "value": value,
                "min": _CTX_MIN_PLAUSIBLE,
                "max": _CTX_MAX_PLAUSIBLE,
                "hint": "model defaults.context_size is outside the plausible range; "
                "ignoring it and falling back to the model's native window. Fix it in "
                "the model drawer.",
            },
        )
        return None
    return value


def _native_ctx(model_info: dict[str, Any]) -> int | None:
    """The model's INTRINSIC native window (registry ``metadata.context_length``).

    Read straight off the GGUF arch max at import time — still a MODEL fact, but
    a derived one, so it only applies when nobody made an explicit choice, and it
    is dense-capped by the caller.
    """
    md = model_info.get("metadata")
    if isinstance(md, dict) and md.get("context_length"):
        try:
            return int(md["context_length"])
        except (TypeError, ValueError):
            pass
    return None


def _resolve_context_size(
    slot_ceiling: int | None,
    model_info: dict[str, Any],
    *,
    slot_name: str = "",
) -> int:
    """The slot's effective context window — the MODEL is authoritative.

    v1.0 ownership split: **the MODEL owns context size.** Before this, a slot's
    ``[model].context_size`` won OUTRIGHT over everything, so two drawers
    (model + slot) each exposed a "Context size" control with no indication that
    the slot silently overrode the model. Precedence is now, highest first:

    1. ``model_info["defaults"]["context_size"]`` — the model's own explicit
       window (:func:`_model_declared_ctx`). Authoritative, NOT dense-capped —
       but an unparseable or implausible value is ignored rather than launched
       (#1414; see :data:`_CTX_MIN_PLAUSIBLE`).
    2. ``model_info["metadata"]["context_length"]`` — the GGUF-derived native
       window (:func:`_native_ctx`), dense-capped at :data:`_CTX_DENSE_CAP`.
       Still a model fact, but a *derived* one, so an explicit choice outranks
       it — the reverse of the pre-1.0 order, which let a 262144-token arch max
       shadow a deliberate ``defaults.context_size``.
    3. :data:`_CTX_SAFE_FALLBACK` (8192) — never llama-server's silent 4096.

    ``slot_ceiling`` (the on-disk ``[model].context_size``) is NO LONGER an
    override. It is honored only as a **hardware CEILING**: the slot owns
    hardware, and a slot-level window written by
    :func:`hal0.hardware.recommend.recommend_primary_slot` /
    :mod:`hal0.install.orchestrate` is a VRAM-budget clamp computed for this
    box's memory (#1108). Dropping it outright would let a model's own larger
    window reopen the first-warm OOM those clamps exist to prevent. So the
    resolved value is capped at the slot ceiling but can never be RAISED by it:

        effective = min(model_authoritative, slot_ceiling)

    Net effect on a box that already has a slot-level ``context_size`` on disk:
    where the clamp was below the model's window (the installer's case) the
    effective context is UNCHANGED; where the slot was silently inflating the
    model's window (the dual-ownership bug) it drops to what the model asked
    for. The effective context can therefore only stay the same or shrink —
    never grow — so no box gains a KV cache it cannot hold. Every case where
    the ceiling actually changes the answer is logged, so the change is visible
    in ``journalctl -u hal0-api`` rather than silent.

    Guarantees a non-None int.
    """
    declared = _model_declared_ctx(model_info)
    if declared is not None:
        resolved, source = declared, "model.defaults.context_size"
    else:
        native = _native_ctx(model_info)
        if native:
            resolved, source = min(native, _CTX_DENSE_CAP), "model.metadata.context_length"
        else:
            resolved, source = _CTX_SAFE_FALLBACK, "safe_fallback"

    if slot_ceiling is None:
        return resolved

    ceiling = int(slot_ceiling)
    if ceiling < resolved:
        log.info(
            "slot.context_size_clamped_by_slot",
            extra={
                "slot": slot_name,
                "model_ctx": resolved,
                "model_ctx_source": source,
                "slot_ceiling": ceiling,
                "effective": ceiling,
                "hint": "the slot's [model].context_size is a hardware ceiling now, "
                "not an override; raise it (or lower the model's context size) to change "
                "the effective window",
            },
        )
        return ceiling
    if ceiling > resolved:
        log.warning(
            "slot.context_size_no_longer_overrides",
            extra={
                "slot": slot_name,
                "model_ctx": resolved,
                "model_ctx_source": source,
                "slot_context_size": ceiling,
                "effective": resolved,
                "hint": "the MODEL owns context size in 1.0; this slot's larger "
                "[model].context_size no longer wins. Set the window on the model to "
                "restore it.",
            },
        )
    return resolved


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
    # SINGLE SOURCE for "which runner's capabilities gate this launch" — the
    # same runner backs the image resolution (:func:`_resolve_image_ref`) and
    # the mtp/jinja capability gates below, so they can never drift onto two
    # different runners (§7.1a / ML-5).
    runner = _effective_runner(slot_cfg, profile, model_info)
    # SINGLE SOURCE for "what hardware class is this launch" — the same helper
    # ``_effective_runner`` uses, so the image and the device-node passthrough
    # gate in ``container_spec`` can never drift onto two different hardware
    # classes. Slot-derived; the profile contributes nothing on a real box.
    effective_backend, effective_device_class = _effective_backend_and_device_class(
        slot_cfg, profile
    )
    effective_mtp = _effective_mtp(model_info, runner, log_ineligible=for_launch)
    # FLAGS-own (spec-flags-ownership §2, golden #5): resolve the image WITHOUT
    # resolving the profile's flags — the profile flag resolver
    # (:func:`resolve_profile_flags`) is NOT consulted at launch. The model's
    # materialized ``defaults`` is the whole tune; a profile is a copy-on-stamp
    # template read only in the drawer, never on the launch/preview path.
    image = _resolve_image_ref(slot_cfg, profile, model_info=model_info)
    # ``flags_str`` retained (empty) for dict-shape/caller compat; profile flags
    # no longer enter the argv chain (see _llama_argv_segments).
    flags_str = ""

    # Divergent slot-profile overlay (#1636): a slot whose ``profile`` differs
    # from the model's stamped provenance (``defaults.profile``) launches with
    # that profile's flags layered over the model tune (the ``slot_profile``
    # segment) — the per-slot flag-divergence path replacing the retired
    # duplicate-for-device model flow. Gated three ways so the common case
    # stays byte-identical to golden #5:
    #   * the slot names a profile AND the model records a provenance — a
    #     provenance-less (hand-authored) tune has made no profile choice, so
    #     there is nothing to diverge from;
    #   * the names differ;
    #   * the resolved profile IS the slot's named one — a missing-profile
    #     fallback to the backend base (``_resolve_profile_or_base``) must not
    #     inject flags the operator never picked;
    #   * the profile actually FITS the slot (``profile_fits_slot`` — same
    #     supported_slot_types + device_class/backend predicate the drawer's
    #     cross-device picker and Q1 adoption already gate on). A profile
    #     assigned out-of-band (API, hand-edited TOML) that names a wrong-type
    #     profile (e.g. ``embedding``/``kokoro`` on an LLM slot) must not
    #     inject mode-changing flags (``--embedding``, ``--model_path``) that
    #     were inert before this overlay existed.
    # No MTP expansion here (``resolve_profile_flags`` with no override): the
    # ``--spec-draft-*`` bundle stays model-driven (see _effective_mtp).
    slot_profile_flags = ""
    _cfg_profile = str(slot_cfg.get("profile") or "")
    _mi_defaults = model_info.get("defaults")
    _provenance = _mi_defaults.get("profile") if isinstance(_mi_defaults, Mapping) else None
    _resolved_name = getattr(profile, "name", None)
    if (
        _cfg_profile
        and isinstance(_provenance, str)
        and _provenance
        and _cfg_profile != _provenance
        and (_resolved_name is None or _resolved_name == _cfg_profile)
    ):
        from hal0.slots.profile_adopt import profile_fits_slot

        if profile_fits_slot(_cfg_profile, slot_cfg):
            slot_profile_flags = str(resolve_profile_flags(profile) or "").strip()
        if slot_profile_flags and for_launch:
            log.info(
                "slot.profile_divergence_applied",
                extra={
                    "slot": str(slot_cfg.get("name") or ""),
                    "profile": _cfg_profile,
                    "model_profile": _provenance,
                },
            )

    if for_launch:
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
    # MODEL-authoritative context window; the slot's on-disk [model].context_size
    # is a hardware ceiling only, never an override (see _resolve_context_size).
    context_size = _resolve_context_size(
        model_table.get("context_size"),
        model_info,
        slot_name=str(slot_cfg.get("name") or ""),
    )

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

    defaults = model_info.get("defaults")
    model_defaults = dict(defaults) if isinstance(defaults, dict) else None

    # Vision projector sidecar (spec-hw-slot-ownership §1): the MODEL is the
    # single authority now via the tri-state ``ModelDefaults.vision`` (the
    # former per-slot ``vision`` toggle (#901) is gone — SlotConfig carries
    # no such field; a write to that key is hard-rejected at the API
    # boundary). None/True = AUTO/affirm — mmproj loads whenever the model
    # carries one; False force-suppresses it even when present (e.g. to save
    # the ~0.9 GB resident projector on a memory-tight host).
    mmproj = model_info.get("mmproj")
    model_vision = defaults.get("vision") if isinstance(defaults, dict) else None
    if model_vision is False:
        mmproj = None

    # Family-architecture overrides (e.g. gemma → f16 KV): the middle layer
    # between the profile's generic flags and the slot's own [model].defaults.
    # Prepended INSIDE defaults.extra_args (the model_extra_args segment) so a
    # per-slot extra_args still wins over the family default (last-wins). §7.1a /
    # ML-5:
    # re-keyed off the registry's authoritative Model.architecture first
    # (model_family/family_flags fall back to the filename/id token scan
    # only when architecture is unset — see hal0.config.schema.model_family).
    fam = family_flags(
        model_info.get("_model_key"),
        model_table.get("default"),
        model_info.get("path"),
        architecture=model_info.get("architecture"),
    )
    if fam:
        if model_defaults is None:
            model_defaults = {}
        existing = model_defaults.get("extra_args") or ""
        model_defaults["extra_args"] = f"{fam} {existing}".strip()

    # --jinja capability injection (§7.1a / ML-5): jinja is a RUNNER
    # capability, not a profile tune anymore (seed profiles no longer carry
    # --jinja in their flags — there is no --no-jinja negation, so this must
    # be injected conditionally, never removed post-hoc). Default true for
    # a runner that supports it, suppressible per-model via
    # defaults.jinja=false; appended to defaults.extra_args (the
    # model_extra_args segment), so a hand-authored extra_args flag later in
    # that string still wins (last-wins).
    #
    # Never injected for an --embedding/--reranking model: jinja chat-
    # template rendering is meaningless there (llama-server's --jinja is a
    # chat-completions feature). FLAGS-own: the mode marker now rides the
    # model's materialized tune (``defaults.extra_args``, family-merged above),
    # NOT a live profile flag string — so read it from there.
    mode_tune = (model_defaults or {}).get("extra_args") or ""
    mode_tokens = set(shlex.split(mode_tune)) if mode_tune.strip() else set()
    is_embed_or_rerank_mode = bool(mode_tokens & {"--embedding", "--reranking"})
    model_jinja = defaults.get("jinja") if isinstance(defaults, dict) else None
    effective_jinja = (
        bool(getattr(runner.supports, "jinja", False))
        and model_jinja is not False
        and not is_embed_or_rerank_mode
    )
    if effective_jinja:
        if model_defaults is None:
            model_defaults = {}
        existing = model_defaults.get("extra_args") or ""
        model_defaults["extra_args"] = f"{existing} --jinja".strip()

    # MTP draft-speculation bundle (§7.1a / ML-5, FLAGS-own): MTP is a MODEL
    # capability (``defaults.mtp`` / the registry ``mtp`` tag) gated by the
    # launching runner — the profile no longer carries it. Previously the
    # ``--spec-draft-*`` bundle was appended inside ``resolve_profile_flags``
    # (the now-severed profile-flag path); inject it here instead, computed
    # from the model-resolved ``effective_mtp`` + the backend's draft device.
    # PREPENDED so a model's own ``defaults.extra_args`` spec-draft overrides
    # still win (last-wins). Draft device tracks the profile backend (device
    # axis, unchanged). Never for an embed/rerank mode (no chat drafting).
    if effective_mtp and not is_embed_or_rerank_mode:
        bundle = build_mtp_flag_bundle(getattr(profile, "backend", None))
        if model_defaults is None:
            model_defaults = {}
        existing = model_defaults.get("extra_args") or ""
        model_defaults["extra_args"] = f"{bundle} {existing}".strip()

    # Slot hardware grid (spec-hw-slot-ownership §2): NGL + THREADS are now
    # authoritative slot-TOP-LEVEL typed fields (SlotConfig.n_gpu_layers /
    # .threads), NOT the sunset nested [model].n_gpu_layers. The emission lane
    # (_llama_argv_segments' slot_hardware segment) renders -ngl / --threads.
    slot_n_gpu_layers = slot_cfg.get("n_gpu_layers")
    slot_threads = slot_cfg.get("threads")

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
        "slot_profile_flags": str(slot_profile_flags),
        "context_size": context_size,
        "extra_args": extra_args,
        "server_env": server_env,
        "model_alias": model_alias,
        "chat_template_path": chat_template_path,
        "mmproj": str(mmproj) if mmproj else None,
        "model_defaults": model_defaults,
        # Slot-owned hardware grid (spec-hw-slot-ownership §2): NGL + THREADS
        # reach the argv chain via _llama_argv_segments' slot_hardware segment.
        "slot_n_gpu_layers": slot_n_gpu_layers,
        "slot_threads": slot_threads,
        # slot_parallel stays inert (spec-flags-ownership §2 — sunset).
        "slot_parallel": None,
        # HARDWARE CLASS + GPU-vendor discriminator (spec-hw-slot-ownership §2):
        # derived from the SLOT's ``device`` enum via the one shared helper, NOT
        # read off the profile. A profile is a model-tuning bundle and selects no
        # hardware; before this, a ``cpu-chat`` profile with device_class unset
        # still fell through ``or "gpu"`` and requested /dev/kfd + /dev/dri on a
        # ``device="cpu"`` slot. The ``or "gpu"`` floor is retained ONLY for a
        # slot_cfg that declares no device at all (hand-built dict / pre-pivot
        # TOML) so an old GPU slot is never stranded on CPU.
        "device_class": str(effective_device_class or "gpu"),
        "backend": effective_backend,
        # Back-compat mirror of ``backend`` under its former name; some callers
        # (and the ``is_nvidia_gpu_device`` parameter) still say "profile".
        "profile_backend": effective_backend,
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
        async with async_client(timeout=30.0) as client:
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

        GPU passthrough branches by VENDOR, decided from the SLOT's declared
        ``device`` enum (never by probing at spec-build time, and never from the
        profile — spec-hw-slot-ownership §2: a profile is a model-tuning bundle
        and selects no hardware):

        * AMD/other (default): device nodes + group GIDs are included ONLY when
          the slot's device resolves to a gpu/img class, and are
          existence-filtered — a ``device="cpu"`` (or ``"npu"``) slot gets no
          ``/dev/kfd`` / ``/dev/dri`` passthrough and no ``--group-add``,
          whatever its profile says.
        * NVIDIA (``device=gpu-cuda``): CDI —
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

        # GPU plumbing gate (#674/CPU-profile fix): only a gpu/img-class SLOT
        # gets GPU passthrough. ``scalars["device_class"]`` is derived from the
        # slot's ``device`` enum by ``_effective_backend_and_device_class`` — a
        # profile's inert ``device_class``/``backend`` hint can no longer mount
        # /dev/kfd + /dev/dri into a ``device="cpu"`` slot's container. On the
        # AMD path only device paths that actually exist on this host are passed
        # (podman errors on a non-existent --device); CDI entries are names, not
        # paths — passed verbatim.
        if scalars["device_class"] in ("gpu", "img"):
            if is_nvidia_gpu_device(scalars["device"], scalars["backend"]):
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
            slot_threads=scalars["slot_threads"],
            slot_parallel=scalars["slot_parallel"],
            env=merged_env or None,
            slot_profile_flags=scalars["slot_profile_flags"],
        )

    # ── ContainerProvider-specific control plane ──────────────────────────────

    def _unit_name(self, slot_name: str) -> str:
        """The systemd **service** name for a slot (``systemctl`` verbs target it).

        Routed through :func:`hal0.slots.naming.slot_unit_name` — the ONE seam
        §11.1's M5 downtime window re-points from name to id. Kept the
        ``hal0-slot@<token>.service`` shape (Quadlet generates it) so every
        existing call site + the hal0-systemctl seam stay valid.
        """
        return slot_unit_name(slot_name)

    def _unit_path(self, slot_name: str) -> Path:
        """The Quadlet ``.container`` source file this slot's unit is written to.

        Was ``/etc/systemd/system/hal0-slot@<name>.service``; is now
        ``/etc/containers/systemd/hal0-slot@<token>.container`` (P3-quadlet).
        Kept the ``_unit_path`` name — it is the write/remove target both
        ``load_sync`` and ``unload_sync`` route through the seam.
        """
        return _QUADLET_DIR / slot_quadlet_name(slot_name)

    def _run(
        self, *args: str, check: bool = True, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run a subprocess synchronously (load/unload are blocking ops anyway).

        P3-perms: routes ``systemctl`` daemon-reload + hal0-slot@ unit verbs
        through the hal0-systemctl seam when this process is running as the
        hal0 service user (see :mod:`hal0.system.seam`); a direct
        ``subprocess.run`` everywhere else — dev, CI, tests, and a
        pre-flip/root install all behave exactly as before.

        ``timeout`` (default ``None`` = unbounded, as before) bounds the child
        so a wedged systemd verb raises :class:`subprocess.TimeoutExpired`
        instead of hanging the caller forever (#1224). The seam forwards it on
        BOTH routes, so the bound holds on a real hal0-service-user install —
        which is the deployment the wedge was observed on.
        """
        return _SYSTEMCTL_SEAM.systemctl(*args, check=check, timeout=timeout)

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
            from hal0.providers.comfyui import ComfyUIProvider
            from hal0.providers.flm import FLMProvider

            # ComfyUI serves neither /health nor /v1/models (its liveness
            # probe is GET /system_stats), so the generic path below read a
            # healthy ComfyUI as http_404 — the fail-watcher then struck a
            # READY img slot to ERROR within seconds of it coming up.
            if isinstance(provider, (FLMProvider, ComfyUIProvider)):
                return await provider.health(port)

        health_url = f"http://127.0.0.1:{port}/health"
        models_url = f"http://127.0.0.1:{port}/v1/models"
        try:
            async with async_client(timeout=_HEALTH_REQUEST_TIMEOUT_S) as client:
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
        """Write the Quadlet ``.container`` file, daemon-reload, start.

        The Quadlet sequence that replaces the old write→daemon-reload→enable→
        restart triple: the ``.container`` file is the source of truth, podman's
        generator emits ``hal0-slot@<token>.service`` on ``daemon-reload``, and
        ``[Install] WantedBy=hal0.target`` in the unit handles boot-enable
        (autoload-gated: slots with ``autoload = false`` render without an
        ``[Install]`` section and do not boot-start) — so no per-load
        ``systemctl enable``. ``restart`` (not bare ``start``) keeps
        this idempotent: it starts a stopped slot and restarts a running one,
        exactly as before. All writes route through the hal0-systemctl seam.
        """
        unit_path = self._unit_path(slot_name)
        dropin_dir = unit_path.with_name(unit_path.name + ".d")
        if dropin_dir.is_dir():
            # A stale ``hal0-slot@<token>.container.d/`` drop-in from a prior
            # render (or a legacy override) could carry dead keys; the generated
            # unit is fully self-contained, so no drop-in is legitimate here.
            shutil.rmtree(dropin_dir)
            log.info(
                "container.stale_dropin_removed",
                extra={"slot": slot_name, "dir": str(dropin_dir)},
            )
        log.info(
            "container.unit_write",
            extra={"slot": slot_name, "unit_path": str(unit_path)},
        )
        _SYSTEMCTL_SEAM.write_quadlet(unit_path, unit_text)
        # daemon-reload runs the Quadlet generator, materialising the .service.
        self._run("systemctl", "daemon-reload")
        self._run("systemctl", "restart", self._unit_name(slot_name))
        log.info(
            "container.unit_started",
            extra={"slot": slot_name, "unit": self._unit_name(slot_name)},
        )

    def _render_quadlet_text(self, slot_cfg: dict[str, Any], model_info: dict[str, Any]) -> str:
        """Render the desired Quadlet ``.container`` text for a slot — the ONE renderer.

        Sole producer of slot-unit text: both :meth:`load_sync` (first install)
        and :meth:`rerender_unit_sync` (update) render through here, so a fresh
        box and an updated box emit **byte-identical** units for the same slot
        config (#1103). Resolves the slot's runtime family to a provider via
        :func:`_spec_provider_for` (FLM/NPU, Kokoro/TTS, ComfyUI/img — else this
        GPU/llama-server provider), builds its :class:`RuntimeLaunchPlan` via
        ``container_spec``, and turns it into ``hal0-slot@<token>.container`` text
        via the one adapter :func:`_render_quadlet_from_plan`.

        Crucially it threads the live ``[slots].publish_host`` into every
        render. The update path used to drop that argument and fall back to the
        loopback default, so re-rendering a slot on a LAN-exposed box
        (``publish_host = 0.0.0.0``) silently narrowed the bind back to
        ``127.0.0.1`` — the exact fresh-vs-updated divergence WS-J removes.
        """
        token = slot_instance_token(slot_cfg)
        provider = _spec_provider_for(slot_cfg) or self
        plan = provider.container_spec(slot_cfg, model_info)
        log.info(
            "container.unit_render",
            extra={
                "slot": token,
                "unit_path": str(self._unit_path(token)),
                "image": plan.image,
                "port": plan.port,
                "provider": getattr(provider, "name", type(provider).__name__),
            },
        )
        return _render_quadlet_from_plan(
            token,
            plan,
            publish_host=_slot_publish_host(),
            network_mode_default=_slot_network_mode(),
            autoload=autoload_enabled(slot_cfg),
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
        :meth:`_render_quadlet_text` (shared with the update-time re-render), and
        this method layers on the install-only steps — the NPU loud-fail guard
        plus writing the Quadlet file and starting the service.
        """
        token = slot_instance_token(slot_cfg)

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

        unit_text = self._render_quadlet_text(slot_cfg, model_info)
        self._write_and_start_unit(token, unit_text)

    def rerender_unit_sync(self, slot_cfg: dict[str, Any], model_info: dict[str, Any]) -> bool:
        """Re-render an EXISTING unit file through current code — without
        touching the running service.

        The unit bakes the launch argv at load time, so after a hal0 update the
        on-disk ``Exec=`` still carries pre-update flags: a bare ``systemctl
        restart`` (or a reboot!) re-runs stale config. This rewrites the
        ``.container`` via the same renderer as :meth:`load_sync` —
        :meth:`_render_quadlet_text`, the sole producer of slot-unit text — but
        deliberately does NOT restart: serving is never bounced by an update;
        the new argv applies on the next start from any path. Callers batch one
        ``daemon_reload`` after a sweep.

        Because both paths render through :meth:`_render_quadlet_text`, the unit
        an update writes is byte-identical to the one a fresh install would write
        for the same slot config — the WS-J guarantee (#1103); Quadlet's
        deterministic generator preserves it end-to-end.

        Returns True when the unit file changed. No-ops (False) when the slot
        has no unit on disk (never rendered → nothing stale) or the fresh
        render is byte-identical — so a convergent rerun never rewrites (nor
        rebuilds) an unchanged unit.
        """
        token = slot_instance_token(slot_cfg)
        unit_path = self._unit_path(token)
        if not unit_path.exists():
            return False
        unit_text = self._render_quadlet_text(slot_cfg, model_info)
        if unit_path.read_text() == unit_text:
            return False
        _SYSTEMCTL_SEAM.write_quadlet(unit_path, unit_text)
        log.info(
            "container.unit_rerendered",
            extra={"slot": token, "unit_path": str(unit_path)},
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
        """Stop and clean up the slot's Quadlet unit (synchronous).

        Quadlet teardown: stop the generated service, delete the ``.container``
        source, then ``daemon-reload`` — the generator drops the now-sourceless
        ``.service`` and Quadlet stops+removes the container by unit basename
        (no more ``reset-failed`` / ``disable``; those cleared ``.service``
        StartLimit + enable state the generated unit no longer carries).

        The stop is BOUNDED and best-effort (#1224): ``systemctl stop`` on a
        unit already parked in ``failed`` — or whose ExecStop is itself wedged
        — used to block forever, which wedged ``SlotManager.restart()`` and
        left the REST caller ReadTimeout'ing with the unit never relaunched.
        ``SlotManager.terminate``'s bound releases the *caller*, but the
        executor thread stays on the child, so without a bound here teardown
        never got past this line. On timeout the stop is abandoned and teardown
        CONTINUES: removing the Quadlet source + ``daemon-reload`` drops the
        generated ``.service`` (clearing its failed sub-state) and Quadlet
        reaps the container by basename, so the subsequent load converges.
        """
        token = slot_instance_token(slot_cfg)
        unit = self._unit_name(token)
        log.info("container.unit_stop", extra={"slot": token, "unit": unit})
        try:
            self._run("systemctl", "stop", unit, check=False, timeout=_UNIT_STOP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            log.warning(
                "container.unit_stop_timeout",
                extra={"slot": token, "unit": unit, "timeout_s": _UNIT_STOP_TIMEOUT_S},
            )
        # Remove the Quadlet source so daemon-reload regenerates without it.
        unit_path = self._unit_path(token)
        if unit_path.exists():
            _SYSTEMCTL_SEAM.remove_quadlet(unit_path)
            self._run("systemctl", "daemon-reload", timeout=_UNIT_STOP_TIMEOUT_S)

    def is_active(self, slot: Mapping[str, Any] | str) -> bool:
        """Return True if the slot's systemd unit is in an active state.

        Takes the slot **config** (#1417): the unit to probe is
        ``hal0-slot@<instance-token>.service``, and on an id-keyed
        (post-migration) box that token is the durable ``id`` — the same token
        ``load_sync`` used to create the unit. Passing the mutable slot *name*
        here asked systemd about a pre-migration artefact that does not exist,
        so ``is-active`` returned non-zero and the drift reconciler force-
        transitioned every healthy container slot to OFFLINE. A bare string is
        still accepted as an already-resolved token.
        """
        result = self._run(
            "systemctl", "is-active", self._unit_name(_artefact_token(slot)), check=False
        )
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

    def running_image(self, slot: Mapping[str, Any] | str) -> str | None:
        """Return the image ref of the running container for a slot (#663).

        Deterministic backend-of-record for a container slot: the running
        backend IS the image tag. Uses ``<runtime> inspect
        hal0-slot-<instance-token> --format {{.ImageName}}``. Takes the slot
        **config** so the token matches what Quadlet actually named the
        container (#1417: a name on an id-keyed box inspected
        ``hal0-slot-brain``, which never existed, so this returned None for
        every slot and the image backend-of-record was inert). Returns None
        when the container is not running or inspect fails. Reads stdout only —
        podman emits benign device warnings to stderr under LXC. Never raises;
        callers dispatch to a thread executor from the async status path.
        """
        try:
            runtime = _container_runtime()
        except RuntimeError:
            return None
        container_name = slot_container_name(_artefact_token(slot))
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

    def running_argv(self, slot: Mapping[str, Any] | str) -> list[str] | None:
        """Return the live container command argv for a slot.

        Uses ``<runtime> inspect hal0-slot-<instance-token> --format
        {{json .Config.Cmd}}``. Takes the slot **config** for the same reason
        as :meth:`running_image` (#1417). Returns None when the container is
        not running, inspect fails, or the runtime returns an unexpected shape.
        Never raises; status callers treat missing data as "unknown", not drift.
        """
        try:
            runtime = _container_runtime()
        except RuntimeError:
            return None
        container_name = slot_container_name(_artefact_token(slot))
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
        slot_threads=scalars["slot_threads"],
        slot_parallel=scalars["slot_parallel"],
        extra_args=scalars["extra_args"],
        slot_profile_flags=scalars["slot_profile_flags"],
    )
    return str(scalars["image"]), resolve_argv(segments)


def resolved_argv_detail_for_slot(
    slot_cfg: dict[str, Any],
    model_path: str | None = None,
) -> dict[str, Any] | None:
    """Structured resolution for the dashboard's "resolved command" drawer.

    Returns ``{"argv", "provenance", "removed"}`` where ``provenance`` lists each
    surviving flag with the segment that set its final value (``base`` /
    ``model_extra_args`` / ``slot_hardware`` / ``chat_template`` / ``mmproj``) —
    so an operator can see exactly which
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
    "_loopback_fence_command",
    "_render_quadlet_from_plan",
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
