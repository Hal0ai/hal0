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
import dataclasses
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
    MTP_DRAFT_BUNDLE,
    mtp_draft_device,
    resolve_chat_template,
)
from hal0.config.schema import (
    family_flags as _family_flags,
)
from hal0.errors import Hal0Error, UnprocessableEntity
from hal0.http_client import async_client
from hal0.model_meta import model_is_mtp_eligible
from hal0.profiles import ProfileCatalog
from hal0.providers import podman_introspect
from hal0.providers._gpu import (
    gpu_visibility_env,
    is_nvidia_gpu_device,
    nvidia_cdi_devices,
    require_kfd_for_gpu_slot,
    resolve_gpu_device_paths,
    resolve_gpu_group_ids,
    resolve_image_runtime_uid,
    runtime_lane_for_provider,
)
from hal0.providers.base import HealthCheck, Mount, Provider, RuntimeLaunchPlan
from hal0.providers.podman_mutate import PullLineParser
from hal0.slot_lifecycle_budget import (
    HEALTH_TIMEOUT_S,
    UNIT_ADMIN_CALLS_ALLOWANCE_S,
    UNIT_START_TIMEOUT_S,
)
from hal0.slots.activation import autoload_enabled
from hal0.slots.argv import ResolvedArgv, resolve_argv
from hal0.slots.naming import (
    slot_container_name,
    slot_instance_token,
    slot_quadlet_name,
    slot_unit_name,
)
from hal0.slots.state import SlotSpawnFailed
from hal0.system.seam import SeamTimeout, SystemCtlSeam, is_hal0_service_user

# ``ContainerSpec`` is the back-compat alias for ``RuntimeLaunchPlan``; some
# callers/tests still import the old name from this module.
ContainerSpec = RuntimeLaunchPlan

#: Existence probe for GPU device nodes (/dev/kfd, /dev/dri/renderD*), used by
#: the AMD passthrough filter in :meth:`ContainerProvider.container_spec`.
#: A dedicated seam so tests can force device nodes "present" on hosts without
#: a GPU by patching ``hal0.providers.container._dev_node_exists`` — patching
#: ``hal0.providers.container.os.path.exists`` instead mutates the ONE shared
#: ``os`` module for the whole process, and on Python >= 3.14
#: ``pathlib.Path.exists()`` delegates to ``os.path.exists``, so a
#: ``return_value=True`` patch made every absent-file guard in the config
#: loaders take the read path and raise ConfigNotFound (#2166).
_dev_node_exists = os.path.exists


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

    Kept as a module-level indirection so it stays the single patch point for
    tests (``hal0.providers.container._resolve_profile``).
    """
    return ProfileCatalog().resolve(name)


#: The profile a slot that names none launches with: its type's seed (the one
#: carrying the mode flag), else ``chat``.
TYPE_DEFAULT_PROFILE: dict[str, str] = {"embedding": "embedding", "reranking": "reranking"}


def resolve_slot_profile(slot_cfg: Mapping[str, Any]) -> Any:
    """The profile a slot launches with (ADR-0029: the whole tune).

    A slot that names no profile gets its type's seed (``embedding`` /
    ``reranking``), else ``chat``. A slot that names a profile the catalog
    does not have refuses to launch (``slot.profile_missing``) — there is no
    silent fallback to some other tune.
    """
    from hal0.errors import NotFound

    name = str(slot_cfg.get("profile") or "")
    if not name:
        name = TYPE_DEFAULT_PROFILE.get(str(slot_cfg.get("type") or ""), "chat")
    try:
        return _resolve_profile(name)
    except NotFound as exc:
        raise UnprocessableEntity(
            f"slot {slot_cfg.get('name')!r} names profile {name!r}, which does not exist; "
            "pick another profile for the slot",
            code="slot.profile_missing",
            details={"slot": str(slot_cfg.get("name") or ""), "profile": name},
        ) from exc


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
        effective     = slot.image_pin
                        or [slots].default_images[family]
                        or image_default

      1. ``slot_cfg["image_pin"]`` — the single canonical escape hatch (debug
         build / A-B / rollback-to-last-known-good). A non-empty string is
         honored verbatim, never re-resolved. Promotes the former
         ``_resolve_image_ref`` tiers 1-2 (``slot.image`` / ``[slot].image``)
         to a first-class typed field; those old nestings are folded into
         ``image_pin`` by the migration lane, not read here.
      2. ``[slots].default_images[<family>]`` — the per-family operator
         override (runner-image-catalogue v2), keyed by the effective
         runner's :data:`~hal0.runners.RUNNER_IMAGES` key and read live via
         :func:`hal0.providers._image.operator_default_image`. Sits ABOVE the whole
         :func:`hal0.runners.resolve_runner_image` chain (env var, then the
         ``manifest.json`` pin): a default the operator set in the UI is
         box-wide intent, only the per-slot ``image_pin`` outranks it.
      3. ``image_default`` — :func:`hal0.runners.resolve_runner_image` of the
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

    A slot the launch dispatches to a non-llama provider (see
    :func:`_spec_provider_for`) resolves through that provider's
    ``image_ref`` instead of tiers 2-3 above — ``image_pin`` →
    ``[slots].default_images[<provider family>]`` → that family's registry
    default (:func:`hal0.providers._image.resolve_family_image`; ComfyUI also
    honors its older top-level ``slot.image`` string). This function is the single
    entry point the pull, status, drift and load-preflight seams share, so
    they inspect the image the Quadlet actually launches (#2234).

    ``model_info`` is retained for call-site compatibility but no longer read.
    """
    del model_info  # image is a slot-owned physical fact now (BINARY/image_pin)
    if isinstance(slot_cfg, Mapping):
        pin = slot_cfg.get("image_pin")
        if isinstance(pin, str) and pin:
            return pin  # escape hatch — honored verbatim, never re-resolved

    # A fixed-family slot (ComfyUI, FLM, Kokoro, Moonshine, Qwen3-TTS) launches
    # its PROVIDER's image, not a device-derived llama runner: hand it to the
    # same ``image_ref`` its ``container_spec`` renders with, so the pull,
    # status, drift and load-preflight seams that call this function all see
    # the ref the Quadlet launches (#2234).
    fixed = _fixed_family_provider(slot_cfg, profile)
    if fixed is not None:
        return str(fixed.image_ref(dict(slot_cfg) if isinstance(slot_cfg, Mapping) else {}))

    # image_default = RUNNER_IMAGES[slot.BINARY] (or the HW-gated default).
    from hal0.providers._image import operator_default_image
    from hal0.runners import resolve_runner_image

    runner = _effective_runner(slot_cfg, profile)
    # Shared with the non-llama providers (#2234).
    override = operator_default_image(runner.key)
    if override is not None:
        return override  # [slots].default_images — operator family default
    return resolve_runner_image(runner)


def _fixed_family_provider(slot_cfg: Mapping[str, Any] | None, profile: Any) -> Any | None:
    """The non-llama provider that launches ``slot_cfg``, or None for llama.

    Same dispatch the launch uses (:func:`_provider_for_family`, i.e.
    :func:`_spec_provider_for`). A caller-supplied resolved profile's
    ``runtime_family`` is used when it carries one; otherwise the family is
    looked up from the slot's profile name exactly as the launch does.
    """
    cfg: dict[str, Any] = dict(slot_cfg) if isinstance(slot_cfg, Mapping) else {}
    family = getattr(profile, "runtime_family", None)
    if not isinstance(family, str):
        family = _profile_runtime_family(cfg)
    return _provider_for_family(cfg, family)


def model_has_mtp_heads(model_info: Mapping[str, Any]) -> bool:
    """The model fact: does the file carry MTP draft heads?

    An explicit ``defaults.mtp`` wins; otherwise the registry ``mtp`` tag.
    """
    defaults = model_info.get("defaults")
    explicit = defaults.get("mtp") if isinstance(defaults, Mapping) else None
    if explicit is not None:
        return bool(explicit)
    return model_is_mtp_eligible(model_info)


def _effective_mtp(
    model_info: Mapping[str, Any],
    runner: Any,
    profile: Any,
    *,
    embed_mode: bool = False,
    log_ineligible: bool = False,
) -> bool:
    """Whether the launch drafts with MTP (ADR-0029).

    The profile decides (``mtp = "auto"`` / ``"off"``); ``auto`` drafts when the
    model has MTP heads and the launching runner supports drafting, outside
    embedding/reranking mode.
    """
    if str(getattr(profile, "mtp", "auto") or "auto") == "off" or embed_mode:
        return False
    if not model_has_mtp_heads(model_info):
        if log_ineligible:
            log.info(
                "mtp.auto_off_model_ineligible",
                extra={
                    "model": str(model_info.get("_model_key") or model_info.get("path") or ""),
                    "hint": "tag the model 'mtp' (or set its MTP heads fact) to draft",
                },
            )
        return False
    return bool(getattr(getattr(runner, "supports", None), "mtp", False))


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


def _decode_argv_json(raw: str) -> list[str] | None:
    """Decode a podman ``{{json .Config.Cmd}}`` payload into an argv list.

    Shared by :meth:`ContainerProvider.running_argv`'s two read paths (the
    ``hal0-podman-ro`` seam and the rootless fallback) so both apply the same
    shape check. ``None`` on anything that is not a JSON array — status
    callers treat missing data as "unknown", never as drift.
    """
    try:
        payload = json.loads(raw.strip())
    except ValueError:
        return None
    if not isinstance(payload, list):
        return None
    return [str(part) for part in payload]


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
# The overall bound lives in hal0.slot_lifecycle_budget so the clients of the
# blocking lifecycle endpoints derive their read timeout from it instead of
# hardcoding a number this module can silently outgrow (#1832).
_HEALTH_POLL_INTERVAL_S = 2.0
_HEALTH_TIMEOUT_S = HEALTH_TIMEOUT_S
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
#:
#: Reused (#1869) for every OTHER fast, should-be-sub-second systemd/seam
#: admin call this provider makes outside the teardown path — the Quadlet
#: write and ``daemon-reload`` in :meth:`_write_and_start_unit`,
#: :meth:`daemon_reload`, :meth:`reset_failed`, :meth:`is_active`, and
#: :meth:`unit_status` — none of which does real work beyond a sudo
#: round-trip and a filesystem/systemd-daemon touch. Derived from
#: ``hal0.slot_lifecycle_budget.UNIT_ADMIN_CALLS_ALLOWANCE_S`` (that budget is
#: this bound charged three times — the load path's write + daemon-reload +
#: reset-failed) so the two stay numerically locked together.
_UNIT_STOP_TIMEOUT_S = UNIT_ADMIN_CALLS_ALLOWANCE_S / 3

#: Wall-clock bound on the ``systemctl restart`` that actually (re)spawns a
#: slot's container (#1869/#1870) — the one seam call in the load path that
#: can legitimately take a while (device attach, a cold netavark setup)
#: rather than being sub-second like the admin calls above. Imported from
#: ``hal0.slot_lifecycle_budget`` (not a fresh literal) so the CLI's own wait,
#: derived from the same module, stays in sync with the server's actual bound.
_UNIT_START_TIMEOUT_S = UNIT_START_TIMEOUT_S

#: Unit properties :meth:`ContainerProvider.unit_status` reads (#1791). All
#: read-only — ``systemctl show`` needs no privilege and is never routed
#: through the hal0-systemctl seam (which only forwards the mutating verbs).
_UNIT_SHOW_PROPS: tuple[str, ...] = (
    "LoadState",
    "ActiveState",
    "SubState",
    "Result",
    "NRestarts",
    # #2126: the exit status of the last main process. podman propagates a
    # container's signal death as 128+signal, so this is where SIGILL shows
    # up as a plain `132` and the reason string can name the fault instead of
    # making an operator open the journal to find out what "result=exit-code"
    # meant.
    "ExecMainStatus",
)

#: 128+signal statuses podman propagates for a container killed by a
#: deterministic fault, mapped to the operator-facing diagnosis (#2126).
#: These are the faults where "restart it" is never the answer: the same
#: instruction stream meets the same CPU and dies the same way.
_FAULT_EXIT_STATUS: dict[int, str] = {
    132: (
        "the runner binary hit an illegal instruction (SIGILL) — the image's "
        "CPU/ISA baseline does not match this host's CPU"
    ),
    134: "the runner binary aborted (SIGABRT)",
    139: "the runner binary segfaulted (SIGSEGV)",
}

#: The hint appended to a SIGILL diagnosis. SIGILL on a slot is almost always
#: an image that was never built for the device the slot is on — #2126's shape
#: is a ``device = "cpu"`` slot launched from a GPU toolbox.
_SIGILL_HINT = (
    "This is an image/hardware mismatch, not a model problem: the slot's "
    "runner image is not built for the CPU it was launched on. `hal0 slot "
    "show <slot>` reports the slot's device and resolved image. "
    "See hal0 issue #2126."
)


def _is_sigterm_stop(props: Mapping[str, str]) -> bool:
    """True when a ``failed`` unit's death is a plain SIGTERM exit (#2130).

    podman propagates a container's graceful SIGTERM shutdown as its own exit
    CODE 143 (128+15), so on a unit rendered before ``SuccessExitStatus=143``
    a deliberate ``systemctl stop`` reads ``Result=exit-code`` /
    ``ExecMainStatus=143`` — indistinguishable, to systemd, from a crash. It
    is distinguishable here: a self-inflicted 143 under ``Restart=always``
    would have been restarted rather than parked in ``failed``, so a unit
    that IS parked with this signature was told to stop. This is the
    version-skew-proof half of the fix — deployed units keep their old text
    until the next load rerenders them.

    Deliberately narrow: only ``exit-code`` + 143. ``start-limit-hit`` (the
    crash-loop verdict), the terminal statuses (64/132), and every other exit
    code stay failures.
    """
    if props.get("Result", "") != "exit-code":
        return False
    return str(props.get("ExecMainStatus", "")).strip() == "143"


def _fault_diagnosis(props: Mapping[str, str]) -> str:
    """Name the deterministic fault behind a failed unit, or ``""`` (#2126).

    Reads ``ExecMainStatus`` from a :meth:`ContainerProvider.unit_status`
    property bag. podman propagates a container killed by a signal as its own
    ``128+signal`` exit status, so systemd records ``status=132/n/a`` for a
    SIGILL — the exact shape #2126 reported, where the operator's only clue
    was an unexplained ``result=exit-code`` on a slot the dashboard still
    painted as ``warming``.

    Only :data:`_FAULT_EXIT_STATUS` members produce a string. Everything else
    (a real exit code, a missing/unparseable property, the OOM-killer's 137)
    returns ``""`` so the caller's generic reason stands unchanged — this
    ANNOTATES a failure, it never decides one.
    """
    raw = str(props.get("ExecMainStatus", "")).strip()
    try:
        status = int(raw)
    except ValueError:
        return ""
    diagnosis = _FAULT_EXIT_STATUS.get(status)
    if diagnosis is None:
        return ""
    if status == 132:
        return f"{diagnosis}. {_SIGILL_HINT}"
    return diagnosis


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
        #
        # Window/burst are sized for the RestartSteps= ramp below: ~10
        # attempts spread over ~20 minutes (5s → 300s geometric), so a
        # transient cause — a neighbour hogging the GPU, a cold mount, a
        # slow image pull — gets real time to clear before systemd gives
        # up. The old 300s/5 pairing burned all 5 attempts in ~16s and
        # LATCHED the unit in ``failed`` (#1424): slot 15 stayed down for
        # hours after the actual contention had passed.
        "StartLimitIntervalSec=1800",
        "StartLimitBurst=10",
        "",
        "[Container]",
        f"Image={plan.image}",
        f"ContainerName={container_name}",
        # ``LogDriver=passthrough`` hands the container's stdout/stderr to
        # the unit itself, which systemd journals exactly once. The old
        # ``LogDriver=none`` assumed conmon→journal held the streams, but
        # the Quadlet service runs podman detached: the client exits after
        # echoing the container id, conmon holds the real streams, and with
        # podman's own sink disabled there was NO sink at all — every slot
        # produced zero container output and a crashing model's actual
        # error was invisible. (#721/B3 was a real double-logging bug; the
        # wrong copy was removed.)
        "LogDriver=passthrough",
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
            # Geometric restart ramp (systemd ≥ 254; the lxc105 reference
            # substrate runs 255): 5s → 10 → 20 → 39 → 77 → 152 → 300,
            # capped at RestartMaxDelaySec. Sized together with the
            # StartLimit window in [Unit] above.
            "RestartSec=5",
            "RestartSteps=6",
            "RestartMaxDelaySec=300",
            # #2037 fail-fast: the runner entrypoint translates a death
            # before /health ever answered into exit 64 ("died during model
            # load" — corrupt GGUF, bogus flag), which would otherwise burn
            # the whole ramp above on a doomed model. Old images never exit
            # 64, so this is inert against them — safe version skew.
            #
            # #2126 adds 132 (128+SIGILL) as a SECOND terminal status, and it
            # is deliberately the only signal death here. The entrypoint's
            # exit-64 translation only exists inside images that SHIP that
            # entrypoint; a slot launched from a foreign or older image (an
            # operator image_pin, or a GPU build on a CPU box — #2126) has
            # nothing in the container to translate anything, so podman
            # propagates 128+signal and systemd sees a plain `status=132`.
            # This host-side directive is the version-skew-proof half.
            #
            # Why ONLY SIGILL host-side: RestartPreventExitStatus is
            # unconditional — it applies to a slot that has been serving for
            # days exactly as it does to one dying at load. An illegal
            # instruction is the one fault where that is safe: it means the
            # binary contains opcodes this CPU cannot execute, which no
            # restart can ever change. SIGSEGV (139) and SIGABRT (134) are
            # not in that class post-load (a bad request, a driver reset, an
            # allocator abort can all recover on restart), and SIGKILL (137)
            # is the OOM-killer, which is transient by definition. Those keep
            # the backoff runway here and are handled load-phase-only, where
            # "before /health ever answered" makes them deterministic, in the
            # runner images' shared entrypoint (hal0-runner-images repo).
            "RestartPreventExitStatus=64 132",
            # #2130: a requested stop is not a failure. `systemctl stop` sends
            # SIGTERM; podman propagates the container's graceful SIGTERM exit
            # as its own exit CODE 143 (128+15), not a signal death — so
            # without this, systemd records `status=143 … Failed with result
            # 'exit-code'` and parks every deliberately-stopped slot in
            # `failed`, which the slot-state probe then paints as a red ERROR
            # (a deliberate stop of every slot). Declaring 143 a
            # success makes a stop end `inactive`/`Result=success`. Restart
            # semantics are untouched: Restart=always restarts on success and
            # failure alike (never during a requested stop job), and the
            # terminal statuses above are exit codes systemd already treats as
            # distinct.
            "SuccessExitStatus=143",
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


def _profile_flag_tokens(flags: str) -> list[str]:
    """Tokenize a profile's flag text for the argv chain.

    A profile's flags are free text in an operator-editable file; malformed
    quoting surfaces here as a controlled config error instead of a 500.
    """
    if not flags or not flags.strip():
        return []
    try:
        return shlex.split(flags)
    except ValueError as exc:
        raise UnprocessableEntity(
            f"slot profile flags are not valid shell text: {exc}",
            code="slot.profile_flags_malformed",
            details={"flags": flags},
        ) from exc


def _llama_argv_segments(
    *,
    port: int,
    model_path: str,
    model_alias: str | None = None,
    context_size: int,
    family_flags: str = "",
    jinja: bool = False,
    chat_template_path: str | None = None,
    mmproj: str | None = None,
    mtp: bool = False,
    profile_flags: str = "",
    slot_n_gpu_layers: int | None = None,
    slot_threads: int | None = None,
    slot_metrics: bool = True,
    draft_device: str | None = None,
) -> list[tuple[str, list[str]]]:
    """The labelled llama-server argv segments (SINGLE SOURCE, ADR-0029).

    Both the launch path (:func:`_llama_launch_plan`) and the preview path
    (:func:`_resolve_slot_argv`) consume THESE segments, so what an operator
    previews is exactly what launches. Four layers, lowest precedence first
    (``resolve_argv`` keeps the LAST occurrence of each flag)::

        base     --host --port --model --alias --ctx-size   (hal0-managed)
        model    family quirks, --jinja, --chat-template-file, --mmproj
                 (model facts; quirks from family_defaults.toml)
        profile  the MTP draft bundle, then the profile's flags
        slot     -ngl, --threads, --metrics, --spec-draft-device (placement)

    The profile's own ``--spec-draft-*`` values follow the bundle and win.
    ``resolve_argv`` screens the profile segment (managed, hardware and
    model-fact flags refuse to launch).
    """
    base: list[str] = ["--host", "0.0.0.0", "--port", str(port)]
    if model_path:
        base += ["--model", model_path]
    # Advertise the hal0 registry model id (else llama-server reports the raw
    # GGUF basename, which the dispatcher can't match to hal0/* virtual names).
    if model_alias:
        base += ["--alias", str(model_alias)]
    base += ["--ctx-size", str(context_size)]

    model: list[str] = shlex.split(family_flags) if family_flags.strip() else []
    if jinja:
        model += ["--jinja"]
    if chat_template_path:
        model += ["--chat-template-file", chat_template_path]
    if mmproj:
        model += ["--mmproj", mmproj]

    profile: list[str] = shlex.split(MTP_DRAFT_BUNDLE) if mtp else []
    profile += _profile_flag_tokens(profile_flags)

    slot: list[str] = []
    if slot_n_gpu_layers is not None:
        slot += ["-ngl", str(int(slot_n_gpu_layers))]
    if slot_threads is not None and int(slot_threads) > 0:
        slot += ["--threads", str(int(slot_threads))]
    # METRICS (#1810): llama-server's ``/metrics`` endpoint, which
    # hal0.slots.metrics_collect scrapes. Loopback-only, so on by default.
    if slot_metrics:
        slot += ["--metrics"]
    if mtp and draft_device:
        slot += ["--spec-draft-device", draft_device]

    return [("base", base), ("model", model), ("profile", profile), ("slot", slot)]


def _llama_launch_plan(
    *,
    image: str,
    port: int,
    model_path: str,
    devices: list[str],
    group_ids: list[str],
    context_size: int,
    model_alias: str | None = None,
    family_flags: str = "",
    jinja: bool = False,
    chat_template_path: str | None = None,
    mmproj: str | None = None,
    mtp: bool = False,
    profile_flags: str = "",
    slot_n_gpu_layers: int | None = None,
    slot_threads: int | None = None,
    slot_metrics: bool = True,
    draft_device: str | None = None,
    env: dict[str, str] | None = None,
) -> RuntimeLaunchPlan:
    """Build the GPU/llama-server :class:`RuntimeLaunchPlan`.

    The in-container argv is :func:`_llama_argv_segments` resolved by
    :func:`~hal0.slots.argv.resolve_argv` (last-wins, screened), so launch
    and preview render byte-identical commands.
    """
    segments = _llama_argv_segments(
        port=port,
        model_path=model_path,
        model_alias=model_alias,
        context_size=context_size,
        family_flags=family_flags,
        jinja=jinja,
        chat_template_path=chat_template_path,
        mmproj=mmproj,
        mtp=mtp,
        profile_flags=profile_flags,
        slot_n_gpu_layers=slot_n_gpu_layers,
        slot_threads=slot_threads,
        slot_metrics=slot_metrics,
        draft_device=draft_device,
    )
    command = resolve_argv(segments).argv

    # Model-store roots (honours [models].store / HAL0_MODEL_STORE AND
    # [models].pull_root). Each root is mounted identical-path, read-only, via
    # the shared `mount_for` factory; `model_mount_roots()` dedups equal/nested
    # roots so store==pull_root renders exactly ONE Volume.
    model_stores = model_mount_roots()

    # Defensive reachability (rework O25 follow-up): a slot's resolved model
    # file can live OUTSIDE every configured model root. Mount the model file's
    # own directory (identical-path, read-only) whenever no configured root
    # already covers it, so the slot loads regardless of store-config drift
    # (`doctor models HAL0-MODEL-STORE-UNMOUNTED` still surfaces the misconfig).
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
    return _provider_for_family(slot_cfg, _profile_runtime_family(slot_cfg))


def _provider_for_family(slot_cfg: dict[str, Any], family: str | None) -> Any | None:
    """The dispatch half of :func:`_spec_provider_for`, given the runtime family.

    Never resolves a profile itself, so a caller that must not write (the
    flags-fold dry run — ``ProfileCatalog.resolve`` can persist a shipped seed
    profile) supplies a family derived from already-loaded config.
    """
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


# Context-window resolution (ADR-0029: the profile owns it).
#
# A slot must NEVER fall through to llama-server's silent 4096 default
# (chat@4096 incident, 2026-06-15). The profile's ``ctx_size`` is the window;
# a profile without one gets the model's native window capped at
# _CTX_DENSE_CAP (an unconfigured window must not request an impractically
# large KV cache off a 262144-token arch max), else _CTX_SAFE_FALLBACK.
_CTX_SAFE_FALLBACK = 8192
_CTX_DENSE_CAP = 32768
#: FLM's window when its profile names none (``providers.flm`` build_env).
_FLM_DEFAULT_CTX = 8192


def _native_ctx(model_info: Mapping[str, Any]) -> int | None:
    """The model's native window (registry ``metadata.context_length``).

    Falls back to the curated catalogue's ``context_length`` (#1617) when the
    registry row carries no metadata at all (a row registered before the pull
    path stamped it).
    """
    md = model_info.get("metadata")
    if isinstance(md, Mapping) and md.get("context_length"):
        try:
            return int(md["context_length"])
        except (TypeError, ValueError):
            pass
    model_id = model_info.get("_model_key") or model_info.get("id")
    if model_id:
        try:
            from hal0.registry.curated import get_curated

            curated = get_curated(str(model_id))
        except Exception:  # curated lookup must never break a launch
            curated = None
        if curated is not None and curated.context_length:
            return int(curated.context_length)
    return None


def profile_context(profile: Any, model_info: Mapping[str, Any]) -> tuple[int, str]:
    """The window a slot launches with, and where it came from.

    ``(ctx, "profile")`` when the profile sets ``ctx_size``; else
    ``(min(native, 32768), "native")``; else ``(8192, "fallback")``.
    """
    ctx_size = getattr(profile, "ctx_size", None)
    if ctx_size:
        return int(ctx_size), "profile"
    native = _native_ctx(model_info)
    if native:
        return min(native, _CTX_DENSE_CAP), "native"
    return _CTX_SAFE_FALLBACK, "fallback"


def slot_effective_context(
    slot_cfg: Mapping[str, Any],
    model_registry: Any = None,
) -> tuple[int | None, str | None]:
    """``(ctx, source)`` the slot runs with, for read-only surfaces.

    The ONE resolver views (``slot_view``, ``/v1/models``, the agent anchor
    window, capacity) use, so no surface re-implements the rule. A slot whose
    profile does not resolve yields ``(None, None)``. FLM slots report their
    profile's window, else 8192. An FLM trio shadow (``served_by`` its anchor)
    runs no process of its own: it reports its anchor's window.
    """
    if str(slot_cfg.get("device") or "") == "npu" and slot_cfg.get("type") in (
        "transcription",
        "embedding",
    ):
        extra = slot_cfg.get("extra") if isinstance(slot_cfg.get("extra"), Mapping) else {}
        anchor = str(slot_cfg.get("served_by") or extra.get("served_by") or "flm")
        anchor_cfg = _served_by_anchor_cfg(anchor)
        if anchor_cfg is not None:
            return slot_effective_context(anchor_cfg, model_registry)
    try:
        profile = resolve_slot_profile(slot_cfg)
    except Hal0Error:
        return None, None
    if str(slot_cfg.get("device") or "") == "npu":
        ctx_size = getattr(profile, "ctx_size", None)
        return (int(ctx_size), "profile") if ctx_size else (_FLM_DEFAULT_CTX, "fallback")
    model_table = slot_cfg.get("model") if isinstance(slot_cfg.get("model"), Mapping) else {}
    model_id = str(model_table.get("default") or "")
    model_info: dict[str, Any] = {}
    if model_registry is not None and model_id:
        try:
            entry = model_registry.get(model_id)
            model_info = entry.model_dump() if hasattr(entry, "model_dump") else dict(entry)
        except Exception:
            model_info = {}
    if model_id:
        # ``Model.model_dump()`` carries no ``_model_key``; the curated
        # backfill in :func:`_native_ctx` needs it (#1617).
        model_info.setdefault("_model_key", model_id)
    return profile_context(profile, model_info)


def _served_by_anchor_cfg(name: str) -> dict[str, Any] | None:
    """The flattened config of the slot named ``name`` when it is an anchor
    (serves itself), else ``None``."""
    from hal0.config import paths
    from hal0.config.loader import _flatten_slot_toml, _read_toml
    from hal0.slots.layout import resolve_slot_stem

    try:
        slots_dir = paths.slots_config_dir()
        stem = resolve_slot_stem(slots_dir, name)
        if stem is None:
            return None
        cfg = _flatten_slot_toml(_read_toml(slots_dir / f"{stem}.toml"), slot_name=name)
    except Exception:
        return None
    if cfg.get("device") != "npu" or cfg.get("type") in ("transcription", "embedding"):
        return None
    return cfg


def _guard_unsupported_quant(
    slot_cfg: dict[str, Any] | Mapping[str, Any] | None,
    model_info: dict[str, Any],
) -> None:
    """Refuse to launch a fork-only quant (ROCmFPX family, IU4, ActiveFPX).

    No runner hal0 ships can load these (ADR-0028): stock llama.cpp rejects
    their custom GGML tensor types or SIGSEGVs on them (#1790), so a launch
    would crash-loop. Model add and pull refuse them too; this is the backstop
    for a model registered before that refusal existed. Checked at the single
    point (:func:`_resolve_llama_scalars`) both the real launch and the preview
    path share.

    Raises :class:`UnprocessableEntity` (422) ``slot.unsupported_quant``.
    """
    from hal0.registry.detect import (
        unsupported_gguf_contents,
        unsupported_quant,
        unsupported_quant_message,
    )

    info = model_info or {}
    # File names only: a directory can be named for a fork family while
    # holding a plain GGUF (the brain sft repo dir carries a plain F16), and
    # the model id is the operator's free choice.
    model_path = Path(str(info.get("path") or ""))
    quant = unsupported_quant(
        str(info.get("quant") or ""),
        model_path.name,
        str(info.get("hf_filename") or ""),
    )
    if quant is None:
        # A fork build renamed to a generic name (or stored as a hash-named
        # HF cache blob): its tensor types tell. The reader checks the GGUF
        # magic itself, and every shard of a split model is checked.
        metadata = info.get("metadata")
        shards = metadata.get("shards") if isinstance(metadata, Mapping) else None
        candidates = [model_path] + [Path(str(p)) for p in (shards or []) if p]
        for candidate in candidates:
            if candidate.is_file():
                quant = unsupported_gguf_contents(candidate)
                if quant is not None:
                    break
    if quant is None:
        return
    slot_name = ""
    if isinstance(slot_cfg, Mapping):
        raw_name = slot_cfg.get("name")
        slot_name = raw_name if isinstance(raw_name, str) else ""
    model_key = str(info.get("_model_key") or info.get("id") or "?")
    raise UnprocessableEntity(
        unsupported_quant_message(model_key, quant),
        code="slot.unsupported_quant",
        details={"slot": slot_name, "model": model_key, "quant": quant},
    )


def _refuse_unconverted(slot_cfg: Mapping[str, Any]) -> None:
    """A slot file still carrying tuning keys was not converted; refuse it."""
    from hal0.config.schema import slot_tuning_keys

    found = slot_tuning_keys(slot_cfg)
    if found:
        pin = blocked_slot_pin(slot_cfg)
        why = f" The conversion blocked it: {pin['reason']}." if pin else ""
        raise UnprocessableEntity(
            f"slot {slot_cfg.get('name')!r} still carries {', '.join(found)}, which belong "
            f"on a profile now (ADR-0029).{why} See `hal0 profile migrate`, fix the slot "
            "file, then run `sudo -u hal0 hal0 profile migrate --apply`",
            code="slot.unconverted",
            details={
                "slot": str(slot_cfg.get("name") or ""),
                "keys": found,
                "blocked": pin["reason"] if pin else None,
            },
        )


def _has_tuning_keys(slot_cfg: Mapping[str, Any]) -> bool:
    from hal0.config.schema import slot_tuning_keys

    return bool(slot_tuning_keys(slot_cfg))


# HAL0-SUNSET: v1.7.0 — the blocked-slot pin (this function, _pinned_segments,
# _placement_only and their launch/preview branches) goes with the
# profile-centric conversion (config.migrations.profile_centric_apply).
def blocked_slot_pin(slot_cfg: Mapping[str, Any]) -> dict[str, Any] | None:
    """The profile-centric conversion's record for a slot it blocked, while the
    slot file is still exactly the one it blocked (else ``None``).

    The record keeps the slot's pre-conversion launch argv, so a converter
    problem never takes a working slot down: it keeps launching as it did
    until the operator fixes it.
    """
    import hashlib
    import json

    from hal0.config import paths
    from hal0.config.migrations.profile_centric import LEDGER_FILE
    from hal0.slots.layout import resolve_slot_stem

    name = str(slot_cfg.get("name") or "")
    if not name:
        return None
    try:
        doc = json.loads((paths.var_lib() / LEDGER_FILE).read_text(encoding="utf-8") or "{}")
        slots_dir = paths.slots_config_dir()
        stem = resolve_slot_stem(slots_dir, name)
        if stem is None:
            return None
        current = hashlib.sha256((slots_dir / f"{stem}.toml").read_bytes()).hexdigest()
    except (OSError, ValueError):
        return None
    blocked = doc.get("blocked") if isinstance(doc, dict) else None
    pin = blocked.get(f"{stem}.toml") if isinstance(blocked, dict) else None
    if not isinstance(pin, dict) or pin.get("file") != current:
        return None
    return pin


def _pinned_segments(
    pin: Mapping[str, Any],
    placement: Mapping[str, Any],
    port: int,
    model_path: str,
    scalars: Mapping[str, Any],
) -> list[tuple[str, list[str]]]:
    """The launch segments for a slot the conversion blocked.

    Only the recorded tune is taken from the ledger, and it is screened like a
    profile (managed, hardware and model-fact flags refuse; the #2545 strip
    applies). The base (host, port, model, alias), the model facts (template,
    projector) and the placement are rebuilt from today's slot and model, so a
    moved model file or an edited ledger can neither stale nor redirect the
    launch. Raises ``slot.unconverted`` when the record is not a list of
    strings.
    """
    from hal0.slots.argv import _split_pairs

    argv = pin.get("argv")
    if not isinstance(argv, list) or not all(isinstance(t, str) for t in argv):
        raise UnprocessableEntity(
            f"slot {placement.get('name')!r} was blocked by the profile-centric conversion and "
            "its recorded pre-conversion launch is unreadable; fix the slot file, then run "
            "`sudo -u hal0 hal0 profile migrate --apply`",
            code="slot.unconverted",
            details={"slot": str(placement.get("name") or ""), "blocked": pin.get("reason")},
        )

    def _value(pair: Any) -> str | None:
        if pair.values:
            return str(pair.values[0])
        flag = str(pair.flag or "")
        return flag.split("=", 1)[1] if "=" in flag else None

    def _int(raw: str | None) -> int | None:
        try:
            return int(raw) if raw is not None else None
        except ValueError:
            return None

    ctx = ngl = threads = None
    jinja = no_jinja = template = projector = no_projector = draft = False
    tune: list[str] = []
    for pair in _split_pairs(list(argv)):
        canon = pair.canon
        if canon in (
            "--host",
            "--port",
            "--model",
            "--alias",
            "--device",
            "--mmproj-device",
            "--metrics",
        ):
            continue  # rebuilt from today's slot and model
        if canon == "--ctx-size":
            ctx = _int(_value(pair))
        elif canon == "--jinja":
            jinja = True
        elif canon == "--no-jinja":
            no_jinja = True
        elif canon in ("--chat-template", "--chat-template-file"):
            template = True
        elif canon == "--mmproj":
            projector = True
        elif canon in ("--no-mmproj", "--no-mmproj-auto"):
            no_projector = True
        elif canon == "--mmproj-auto":
            continue
        elif canon == "--n-gpu-layers":
            ngl = _int(_value(pair))
        elif canon == "--threads":
            threads = _int(_value(pair))
        elif canon == "--spec-draft-device":
            draft = True
        else:
            tune += [str(pair.flag), *pair.values] if pair.flag else list(pair.values)

    base = ["--host", "0.0.0.0", "--port", str(port)]
    if model_path:
        base += ["--model", model_path]
    if scalars.get("model_alias"):
        base += ["--alias", str(scalars["model_alias"])]
    base += ["--ctx-size", str(ctx or scalars["context_size"])]
    # A recorded --no-jinja is a model fact the profile layer may not carry:
    # it is consumed here and the model's own jinja fact decides.
    if no_jinja:
        jinja = bool(scalars.get("jinja"))
    model: list[str] = ["--jinja"] if jinja else []
    if template and scalars.get("chat_template_path"):
        model += ["--chat-template-file", str(scalars["chat_template_path"])]
    if projector and not no_projector and scalars.get("mmproj"):
        model += ["--mmproj", str(scalars["mmproj"])]
    slot: list[str] = []
    slot_ngl = scalars.get("slot_n_gpu_layers")
    if slot_ngl is not None or ngl is not None:
        slot += ["-ngl", str(int(slot_ngl if slot_ngl is not None else ngl))]
    slot_threads = scalars.get("slot_threads") or threads
    if slot_threads:
        slot += ["--threads", str(int(slot_threads))]
    if scalars.get("slot_metrics", True):
        slot += ["--metrics"]
    if draft and scalars.get("backend"):
        slot += ["--spec-draft-device", mtp_draft_device(scalars["backend"])]
    return [("base", base), ("model", model), ("profile", tune), ("slot", slot)]


def _placement_only(slot_cfg: Mapping[str, Any]) -> dict[str, Any]:
    """The slot without its tuning keys, on its type's seed: what a blocked
    slot's runner image, devices and environment are resolved from."""
    from hal0.config.schema import (
        SLOT_TUNING_MODEL_KEYS,
        SLOT_TUNING_SERVER_KEYS,
        SLOT_TUNING_TOP_KEYS,
    )

    out = {k: v for k, v in slot_cfg.items() if k not in SLOT_TUNING_TOP_KEYS}
    out.pop("defaults", None)
    out["profile"] = TYPE_DEFAULT_PROFILE.get(str(slot_cfg.get("type") or ""), "chat")
    if isinstance(out.get("model"), Mapping):
        out["model"] = {k: v for k, v in out["model"].items() if k not in SLOT_TUNING_MODEL_KEYS}
    if isinstance(out.get("server"), Mapping):
        out["server"] = {k: v for k, v in out["server"].items() if k not in SLOT_TUNING_SERVER_KEYS}
    if isinstance(out.get("slot"), Mapping):
        out["slot"] = {k: v for k, v in out["slot"].items() if k not in SLOT_TUNING_TOP_KEYS}
    return out


def _resolve_llama_scalars(
    slot_cfg: dict[str, Any],
    model_info: dict[str, Any],
    profile: Any,
    *,
    for_launch: bool = False,
) -> dict[str, Any]:
    """Every llama-server launch scalar for a slot + model + profile.

    SINGLE SOURCE for both the launch path (:meth:`ContainerProvider.container_spec`)
    and the preview path (:func:`_resolve_slot_argv`), so the two can never
    drift. ADR-0029: the profile is the whole tune (flags, window, vision,
    MTP), the model contributes facts (path, alias, chat template, mmproj,
    jinja, MTP heads) and the slot contributes placement.

    ``for_launch`` marks the real launch call; it only gates side effects
    (logs, the unsupported-quant guard), never the resolved values.
    """
    _refuse_unconverted(slot_cfg)
    runner = _effective_runner(slot_cfg, profile)
    effective_backend, effective_device_class = _effective_backend_and_device_class(
        slot_cfg, profile
    )
    if for_launch:
        _guard_unsupported_quant(slot_cfg, model_info)
    image = _resolve_image_ref(slot_cfg, profile, model_info=model_info)

    profile_flags = str(getattr(profile, "flags", "") or "")
    profile_tokens = set(_profile_flag_tokens(profile_flags))
    embed_mode = bool(profile_tokens & {"--embedding", "--reranking"})

    context_size, _ctx_source = profile_context(profile, model_info)

    server_table = slot_cfg.get("server") or {}
    server_env = server_table.get("env") if isinstance(server_table, dict) else None
    if not isinstance(server_env, dict):
        server_env = None

    model_table = slot_cfg.get("model") if isinstance(slot_cfg.get("model"), dict) else {}
    # Registry model id → llama-server --alias so the container advertises the
    # hal0 id (not the raw GGUF basename) for dispatcher matching.
    model_alias = model_info.get("_model_key") or model_table.get("default") or None

    tmpl_id = resolve_chat_template(model_info)
    chat_template_path = (
        str(Path(model_store_root()) / "chat-templates" / f"{tmpl_id}.jinja") if tmpl_id else None
    )

    defaults = model_info.get("defaults") if isinstance(model_info.get("defaults"), Mapping) else {}
    # The projector follows the model's mmproj and the profile's vision.
    mmproj = model_info.get("mmproj")
    if str(getattr(profile, "vision", "auto") or "auto") == "off":
        mmproj = None

    # --jinja: a runner capability, suppressible per model file
    # (``defaults.jinja = false``); meaningless in embedding/reranking mode.
    jinja = (
        bool(getattr(runner.supports, "jinja", False))
        and defaults.get("jinja") is not False
        and not embed_mode
    )
    mtp = _effective_mtp(
        model_info, runner, profile, embed_mode=embed_mode, log_ineligible=for_launch
    )

    slot_metrics_raw = slot_cfg.get("metrics")
    gpu_index = slot_cfg.get("gpu_index")
    try:
        gpu_index = int(gpu_index) if gpu_index is not None else None
    except (TypeError, ValueError):
        gpu_index = None
    if gpu_index is not None and gpu_index < 0:
        gpu_index = None

    # Family quirks (ADR-0029 model layer): keyed by the model's architecture,
    # else its id/path. ``family_defaults.toml`` ships empty today.
    quirks = _family_flags(
        str(model_info.get("_model_key") or ""),
        str(model_info.get("path") or ""),
        architecture=model_info.get("architecture"),
    )

    return {
        "image": str(image),
        "profile_flags": profile_flags,
        "family_flags": quirks,
        "context_size": context_size,
        "server_env": server_env,
        "model_alias": model_alias,
        "jinja": jinja,
        "chat_template_path": chat_template_path,
        "mmproj": str(mmproj) if mmproj else None,
        "mtp": mtp,
        "draft_device": mtp_draft_device(effective_backend) if mtp else None,
        "slot_n_gpu_layers": slot_cfg.get("n_gpu_layers"),
        "slot_threads": slot_cfg.get("threads"),
        "slot_metrics": True if slot_metrics_raw is None else bool(slot_metrics_raw),
        # Hardware class + GPU-vendor discriminator, derived from the SLOT's
        # ``device`` enum (a profile selects no hardware). The ``or "gpu"``
        # floor is only for a slot dict that declares no device at all.
        "device_class": str(effective_device_class or "gpu"),
        "backend": effective_backend,
        "device": str(slot_cfg.get("device") or ""),
        "gpu_index": gpu_index,
    }


def _segments_from_scalars(
    port: int, model_path: str, sc: Mapping[str, Any]
) -> list[tuple[str, list[str]]]:
    return _llama_argv_segments(
        port=port,
        model_path=model_path,
        model_alias=sc["model_alias"],
        context_size=sc["context_size"],
        family_flags=sc["family_flags"],
        jinja=sc["jinja"],
        chat_template_path=sc["chat_template_path"],
        mmproj=sc["mmproj"],
        mtp=sc["mtp"],
        profile_flags=sc["profile_flags"],
        slot_n_gpu_layers=sc["slot_n_gpu_layers"],
        slot_threads=sc["slot_threads"],
        slot_metrics=sc["slot_metrics"],
        draft_device=sc["draft_device"],
    )


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

    #: The Standard runner: HIP first, Vulkan fallback second
    #: (#1888). ``runtime_lane_for_provider`` never actually sees this class —
    #: ``_spec_provider_for`` returns ``None`` for the llama-server default and
    #: that maps to the richer ``"llama"`` lane — but declaring it keeps every
    #: possible input to that translation fail-CLOSED rather than fail-open.
    gpu_runtime_needs_rocm = True

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
        renderer.  The plan is complete — registry alias, the profile's window
        and flags, the model's chat-template and mmproj, the slot's placement
        and ``[server].env``, the read-only model-store mount, and the
        health-check override are all included, so what loads matches what
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
        # A slot the profile-centric conversion blocked keeps launching with
        # its pre-conversion argv while its file is unchanged (ADR-0029 §7).
        pin = blocked_slot_pin(slot_cfg) if _has_tuning_keys(slot_cfg) else None
        if pin is not None and pin.get("argv") is None:
            pin = None
        if pin is not None:
            slot_cfg = _placement_only(slot_cfg)
            log.warning(
                "slot.launch_pinned_pre_conversion slot=%s reason=%s",
                slot_cfg.get("name"),
                pin.get("reason"),
            )
        profile = resolve_slot_profile(slot_cfg)
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
                devices = [p for p in resolve_gpu_device_paths() if _dev_node_exists(p)]
                group_ids = [str(g) for g in resolve_gpu_group_ids()]
        else:
            devices = []
            group_ids = []

        # gpu_index visibility env (per device family), merged UNDER the
        # slot's [server].env so an operator's explicit key always wins.
        vis_env = gpu_visibility_env(scalars["device"], scalars["gpu_index"])
        server_env = scalars["server_env"] or {}
        merged_env = {**vis_env, **server_env}

        plan = _llama_launch_plan(
            image=scalars["image"],
            port=port,
            model_path=model_path,
            devices=devices,
            group_ids=group_ids,
            context_size=scalars["context_size"],
            model_alias=scalars["model_alias"],
            family_flags=scalars["family_flags"],
            jinja=scalars["jinja"],
            chat_template_path=scalars["chat_template_path"],
            mmproj=scalars["mmproj"],
            mtp=scalars["mtp"],
            profile_flags=scalars["profile_flags"],
            slot_n_gpu_layers=scalars["slot_n_gpu_layers"],
            slot_threads=scalars["slot_threads"],
            slot_metrics=scalars["slot_metrics"],
            draft_device=scalars["draft_device"],
            env=merged_env or None,
        )
        if pin is not None:
            pinned = _pinned_segments(pin, slot_cfg, port, model_path, scalars)
            return dataclasses.replace(plan, command=resolve_argv(pinned).argv)
        return plan

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
        except httpx.TransportError as exc:
            # TransportError is the base class for ConnectError,
            # TimeoutException, ReadError, WriteError, etc. — every failure
            # mode where the connection dies without producing an HTTP
            # response (#1898: a runner that binds its port and then dies
            # mid-read raises ReadError, whose str() is empty; that escaped
            # the old narrow (ConnectError, TimeoutException) catch as an
            # unhandled exception, abandoning wait_ready()'s poll loop after
            # a single strike instead of reporting "unhealthy" and retrying
            # for the full window). status falls back to the exception's
            # class name so metadata.message is never empty even when
            # str(exc) is "".
            return {"ok": False, "status": str(exc) or type(exc).__name__}

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

        Every seam call below is bounded (#1869): the write, the reloads and
        ``reset_failed`` at :data:`_UNIT_STOP_TIMEOUT_S` (fast admin ops), the
        ``restart`` itself at :data:`_UNIT_START_TIMEOUT_S` (the one call that
        can legitimately take a while). A timeout raises
        :class:`hal0.system.seam.SeamTimeout` — deliberately NOT caught here,
        unlike ``CalledProcessError`` below: it is a distinct failure mode
        (the seam call never confirmed one way or the other) and propagates
        with its own ``slot.seam_timeout`` code through ``SlotManager.load``'s
        generic ``except Exception -> ERROR`` handling.
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
        _SYSTEMCTL_SEAM.write_quadlet(unit_path, unit_text, timeout=_UNIT_STOP_TIMEOUT_S)
        # daemon-reload runs the Quadlet generator, materialising the .service.
        self._run("systemctl", "daemon-reload", timeout=_UNIT_STOP_TIMEOUT_S)
        # #1424/#1791: a unit parked in ``failed`` after StartLimitBurst
        # refuses every ``restart`` for the whole StartLimitIntervalSec window
        # ("Start request repeated too quickly"), so a slot that crash-looped
        # was unloadable via the API until the window expired — the operator
        # had to `systemctl reset-failed` by hand. Clear it here, on the ONE
        # path every load/restart/swap goes through, before the start. No-op
        # for a healthy unit.
        self.reset_failed(slot_name)
        unit = self._unit_name(slot_name)
        try:
            self._run("systemctl", "restart", unit, timeout=_UNIT_START_TIMEOUT_S)
        except subprocess.CalledProcessError as exc:
            # #1424 facet 2: raw, this is not a Hal0Error — it falls through
            # SlotManager.load()'s re-raise into the API's generic Exception
            # handler as 500 ``system.internal``, and ``str(exc)`` (the full
            # sudo seam argv) is what gets stamped into the slot's
            # user-visible ``metadata.message``. Wrap it typed: the envelope
            # is ``slot.spawn_failed`` and the message is systemd's failure
            # reason (the #1791 probe), never the argv. The raw error stays
            # on the cause chain for logs.
            reason = self.unit_failure_reason(slot_name)
            if not reason:
                # Probe read nothing terminal (seam denied the verb, race
                # with systemd, …) — still name the unit and the next step,
                # not the command line.
                reason = (
                    f"systemctl restart {unit} failed (exit {exc.returncode}) "
                    f"— see `journalctl -u {unit}`"
                )
            log.error(
                "container.unit_start_failed",
                extra={"slot": slot_name, "unit": unit, "reason": reason},
            )
            raise SlotSpawnFailed(reason, details={"slot": slot_name}) from exc
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
        this method layers on the install-only steps — the NPU and ROCm
        loud-fail guards plus writing the Quadlet file and starting the service.
        """
        token = slot_instance_token(slot_cfg)

        # The slot's runtime family, resolved ONCE for both loud-fail guards
        # below (and re-resolved inside _render_quadlet_text, which owns the
        # dispatch). ``None`` means the default llama-server GPU provider.
        spec_provider = _spec_provider_for(slot_cfg)

        # Loud-fail for AMD-GPU slots with no /dev/kfd whose runtime needs it:
        # the Standard runner would silently fall back to its Vulkan backend,
        # which must not serve unless its digest passed the Vulkan gate
        # (#1888). Enforced HERE (the load path) rather
        # than in ``container_spec`` so unit rendering, previews and status
        # renders stay host-independent.
        #
        # The lane — not the device string — decides (#1941). ``device =
        # "gpu-vulkan"`` is the catalog's GPU-row label for EVERY non-llama
        # runtime, so it says nothing about the image: Kokoro and Moonshine
        # are CPU ONNX images that need no compute node, while ComfyUI and
        # Qwen3-TTS are ROCm builds that do. Each provider declares
        # ``gpu_runtime_needs_rocm``; ``runtime_lane_for_provider`` turns that
        # into the lane.
        #
        # The guard needs TWO further facts about this launch, and they are
        # different questions about the same image.
        #
        # WHICH IMAGE (#1948 Phase D): the Vulkan LLM lane is supported again,
        # but only on a runner whose Vulkan backend passed the §3-C validation
        # matrix — the ade07ba lineage never can. So the guard needs the ref
        # this slot will ACTUALLY launch, ``image_pin`` included.
        #
        # WHICH IDENTITY (#1953): given the lane needs the compute node, which
        # uid opens it. Derived from the image rather than assumed root —
        # rootful podman still honours a USER-declaring image, and assuming
        # root there would fail OPEN into the poisoned lane the guard blocks.
        #
        # Resolved ONCE into a local. ``_resolve_image_ref`` is cheap (a dict
        # read plus a registry lookup, no subprocess), so hoisting it costs
        # nothing and removes the chance of the two arguments describing
        # different images. What is expensive is ``resolve_image_runtime_uid``
        # — a sudo round-trip plus two podman calls — so THAT stays behind the
        # lambda: the guard returns early for cpu/npu/gpu-cuda and for
        # non-ROCm lanes, and charging every slot on the box for a probe only
        # the gated ones need is what the laziness exists to avoid.
        resolved_image = _resolve_image_ref(slot_cfg, None)
        require_kfd_for_gpu_slot(
            str(slot_cfg.get("name", "") or token),
            device=str(slot_cfg.get("device", "") or ""),
            runtime_lane=runtime_lane_for_provider(spec_provider),
            image=resolved_image,
            runner_uid=lambda: resolve_image_runtime_uid(resolved_image),
        )

        # Loud-fail for NPU slots only: a missing FLM tag must not silently
        # fall through to FLM's legacy build_env default. Kokoro/ComfyUI are
        # self-managed and need no registry tag — the check fires ONLY when
        # device == "npu" and the slot resolves to a spec provider.
        if str(slot_cfg.get("device", "")) == "npu" and spec_provider is not None:
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
        _SYSTEMCTL_SEAM.write_quadlet(unit_path, unit_text, timeout=_UNIT_STOP_TIMEOUT_S)
        log.info(
            "container.unit_rerendered",
            extra={"slot": token, "unit_path": str(unit_path)},
        )
        return True

    def daemon_reload(self) -> None:
        """``systemctl daemon-reload`` — public for the unit-rerender sweep."""
        self._run("systemctl", "daemon-reload", timeout=_UNIT_STOP_TIMEOUT_S)

    def expected_argv(
        self, slot_cfg: dict[str, Any], model_info: dict[str, Any]
    ) -> list[str] | None:
        """Return the freshly-rendered runtime command for drift checks.

        This is the command portion passed to the container image, not the
        podman/systemd preamble. It goes through the same provider plan path as
        ``load_sync`` so the window, profile flags, model alias and slot
        placement match what a restart would render now.
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
        except (subprocess.TimeoutExpired, SeamTimeout):
            # The real (unmocked) seam route raises SeamTimeout (#1869: every
            # ``_run`` call now goes through ``SystemCtlSeam.systemctl`` ->
            # ``bounded_call``); ``subprocess.TimeoutExpired`` stays caught too
            # for callers/tests that stub ``_run`` directly and raise the bare
            # stdlib error. Either way the stop is best-effort — see below.
            log.warning(
                "container.unit_stop_timeout",
                extra={"slot": token, "unit": unit, "timeout_s": _UNIT_STOP_TIMEOUT_S},
            )
        # Remove the Quadlet source so daemon-reload regenerates without it.
        unit_path = self._unit_path(token)
        if unit_path.exists():
            _SYSTEMCTL_SEAM.remove_quadlet(unit_path, timeout=_UNIT_STOP_TIMEOUT_S)
            self._run("systemctl", "daemon-reload", timeout=_UNIT_STOP_TIMEOUT_S)
        # A DELIBERATE stop must not leave the unit parked in `failed`.
        # The bounded stop above escalates to SIGKILL (exit 137) — and a
        # container that traps SIGTERM exits 143 — so systemd records
        # Result=exit-code and keeps the failed unit resident in memory
        # even after the Quadlet source is gone (daemon-reload does NOT
        # clear failed runtime state, contrary to what this teardown used
        # to assume). The slot-view status probe reads that as "crashed"
        # and the dashboard painted every cleanly-stopped slot red.
        # reset-failed marks the stop as intentional; a unit that failed
        # on its own (crash, OOM) never passes through this teardown and
        # stays `failed` → red.
        self._run("systemctl", "reset-failed", unit, check=False, timeout=_UNIT_STOP_TIMEOUT_S)

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
            "systemctl",
            "is-active",
            self._unit_name(_artefact_token(slot)),
            check=False,
            timeout=_UNIT_STOP_TIMEOUT_S,
        )
        return result.returncode == 0

    def unit_status(self, slot: Mapping[str, Any] | str) -> dict[str, str]:
        """Return the slot unit's systemd properties (``systemctl show``).

        Keys are :data:`_UNIT_SHOW_PROPS`; a property systemd doesn't report is
        simply absent. Read-only and unprivileged — ``show`` is not one of the
        seam's forwarded verbs, so it passes straight through on a
        hal0-service-user box exactly like ``is-active``.

        ``is-active`` alone cannot tell "not started yet" from "systemd gave up
        on it": both read inactive. This is the probe that distinguishes them —
        ``ActiveState=failed`` with ``Result=start-limit-hit`` is the crash-loop
        signature (#1791), and ``LoadState=not-found`` means the generated unit
        is gone entirely (the Quadlet source was removed underneath it).
        """
        unit = self._unit_name(_artefact_token(slot))
        args = [f"--property={prop}" for prop in _UNIT_SHOW_PROPS]
        result = self._run(
            "systemctl", "show", unit, *args, check=False, timeout=_UNIT_STOP_TIMEOUT_S
        )
        props: dict[str, str] = {}
        for line in (result.stdout or "").splitlines():
            key, sep, value = line.partition("=")
            if sep:
                props[key.strip()] = value.strip()
        return props

    def unit_failure_reason(self, slot: Mapping[str, Any] | str) -> str:
        """Human-readable reason when the slot's unit is dead-and-not-coming-back.

        Returns ``""`` when the unit is fine (active, activating, or merely
        stopped) — only a systemd-side *terminal* condition produces a string:

          * ``ActiveState=failed`` — the container exited non-zero, or systemd
            hit ``StartLimitBurst`` and stopped retrying (``start-limit-hit``);
          * ``LoadState=not-found`` — the generated ``.service`` no longer
            exists at all.

        CONTRACT: callers ask this only about a slot whose recorded state
        claims the unit *should* be running (STARTING/WARMING/READY…). A
        never-loaded slot also reads ``not-found``, which is not a failure —
        the caller's state record is what makes it one.

        The string is written for an operator reading ``hal0 status`` or the
        dashboard card, so it names the unit, the systemd result, and the
        manual recovery step.
        """
        token = _artefact_token(slot)
        unit = self._unit_name(token)
        try:
            props = self.unit_status(slot)
        except Exception as exc:  # pragma: no cover — defensive
            log.warning("container.unit_status_failed", extra={"slot": token, "error": str(exc)})
            return ""
        load_state = props.get("LoadState", "")
        active_state = props.get("ActiveState", "")
        result = props.get("Result", "") or "failure"
        restarts = props.get("NRestarts", "") or "0"
        fault = _fault_diagnosis(props)
        if active_state == "failed":
            if _is_sigterm_stop(props):
                # #2130: a deliberate `systemctl stop` on a unit that predates
                # `SuccessExitStatus=143` parks it in `failed` with a plain
                # SIGTERM exit. The service was ASKED to die and did so
                # cleanly — reporting it as a failure painted every slot
                # stopped deliberately (a bare
                # systemctl stop) as a red ERROR on a healthy box.
                return ""
            if result == "start-limit-hit":
                base = (
                    f"{unit} is crash-looping: systemd stopped retrying after "
                    f"{restarts} restarts (start-limit-hit). Fix the cause, then "
                    f"`hal0 slot load {token}`"
                )
                return f"{base} — {fault}" if fault else base
            base = (
                f"{unit} failed (result={result}, restarts={restarts}) — see `journalctl -u {unit}`"
            )
            return f"{base}. {fault}" if fault else base
        if load_state == "not-found":
            return (
                f"{unit} no longer exists — the slot's unit was removed while it "
                f"was loading. `hal0 slot load {token}` recreates it"
            )
        return ""

    def unit_stopped_cleanly(self, slot: Mapping[str, Any] | str) -> bool:
        """POSITIVE evidence that the slot's unit was stopped on purpose (#2130).

        The inverse question to :meth:`unit_failure_reason`, and deliberately
        not its negation: that probe's ``""`` means "no terminal evidence",
        which callers must never read as proof of health. This one answers
        ``True`` only when the unit's own properties show a deliberate,
        non-crash end:

          * ``ActiveState=inactive`` with ``Result=success`` — a clean stop on
            a unit carrying ``SuccessExitStatus=143``, a unit reset via
            ``systemctl reset-failed``, or one not started since boot;
          * ``ActiveState=failed`` with the plain-SIGTERM signature
            (``Result=exit-code`` / ``ExecMainStatus=143``) — a deliberate
            stop on a unit rendered before ``SuccessExitStatus=143`` existed.

        Everything else — crash-loop (``start-limit-hit``), any other exit
        code, a unit mid-restart (``activating``), a removed unit
        (``LoadState=not-found``, the output-sanity teardown shape), or a
        probe error — is ``False``. Callers use ``True`` to retire a cached
        ERROR, so absence of evidence must never manufacture an OFFLINE.
        """
        try:
            props = self.unit_status(slot)
        except Exception as exc:  # pragma: no cover — defensive
            log.warning(
                "container.unit_status_failed",
                extra={"slot": _artefact_token(slot), "error": str(exc)},
            )
            return False
        if props.get("LoadState", "") != "loaded":
            return False
        active_state = props.get("ActiveState", "")
        if active_state == "inactive":
            return props.get("Result", "") in ("success", "")
        return active_state == "failed" and _is_sigterm_stop(props)

    def reset_failed(self, slot: Mapping[str, Any] | str) -> None:
        """Clear a unit's ``failed`` sub-state and its start-limit counter.

        Best-effort (``check=False``): ``reset-failed`` on a healthy or absent
        unit is a harmless no-op, and a failure here must never block the
        start it precedes. Routed through the seam's ``reset-failed`` verb on
        a hal0-service-user box (#1424).
        """
        self._run(
            "systemctl",
            "reset-failed",
            self._unit_name(_artefact_token(slot)),
            check=False,
            timeout=_UNIT_STOP_TIMEOUT_S,
        )

    def image_present(self, image: str) -> bool | None:
        """Is ``image`` in the container image store slots use? Tri-state.

        ``True`` / ``False`` are answers: something actually looked in the
        right store. ``None`` means **nobody could look** — the rootful seam
        was unusable and this process has no other store worth asking.

        #1889: asks ROOT's store first, through the ``hal0-podman-ro``
        ``image-exists`` seam. Slots run ROOTFUL podman (Quadlet), while
        hal0-api runs as the unprivileged ``hal0`` user with no subuid ranges,
        so the bare ``<runtime> image inspect`` below reads hal0's own
        ROOTLESS store — a different store, which never contains a slot image.
        That is why ``image_status`` was ``"missing"`` for every running,
        healthy slot on every standard install. The seam reports ``unknown``
        when it cannot answer, and only then do we fall back to the rootless
        read — and only when this process is NOT the hal0 service user. On a
        provisioned box the rootless store is definitionally the wrong store,
        so consulting it after a seam failure would collapse "podman is
        broken / the grant is missing" (wrapper rc 66 / sudo rc 1) back into
        an authoritative-looking "missing", which is #1889 with extra steps.

        #1939: and so is answering ``False`` there, which is what this method
        used to do because its contract was a bool. It is now a tri-state, and
        every ``None`` is logged at WARNING with the seam's own reason —
        ``image_status: "unknown"`` tells a reader the answer is untrustworthy,
        the journal line tells an operator which grant or store to go fix.

        In a dev checkout, where hal0-api runs as the operator, the rootless
        store IS the store slots use, so the fallback is correct there — but
        only for the one reason that actually means "we never asked because
        this is not a provisioned box" (``not-service-user``). Any other
        reason means the seam was tried, or that our own mirror of the
        wrapper's validator rejected the ref — and a ref this side rejects is
        one podman would reject too, so the rootless store has nothing to add.

        The fallback probe is ``podman image exists``, NOT ``image inspect``,
        for exactly the reason the wrapper header gives: ``exists`` has the
        contract rc 0 = yes / rc 1 = no / anything else = operational failure,
        while ``inspect`` collapses "not found" and "podman is broken" into a
        single non-zero rc. Using ``inspect`` here would reintroduce the same
        conflation on dev boxes that the seam removed on deployed ones.

        Runs synchronously — callers must dispatch to a thread executor when
        called from an async context.
        """
        probe = podman_introspect.image_presence(image)
        if probe.presence != "unknown":
            return probe.presence == "present"
        if probe.reason != "not-service-user":
            log.warning(
                "hal0.podman_ro.image_present_unanswered image=%s reason=%s — the rootful "
                "seam did not answer; reporting image_status=unknown rather than consulting "
                "the unrelated rootless store (see `hal0 doctor seams`)",
                image,
                probe.reason,
            )
            return None
        try:
            runtime = _container_runtime()
        except RuntimeError:
            log.warning(
                "hal0.podman_ro.image_present_unanswered image=%s reason=podman-absent — "
                "no container runtime on this box, so nothing can be asked about the image; "
                "reporting image_status=unknown",
                image,
            )
            return None
        try:
            # #1869: bounded like the sibling ``inspect`` probes below —
            # ``TimeoutExpired`` is a ``SubprocessError`` subclass, so the
            # except clause here already degrades it to image_status=unknown;
            # it just never fired without a bound to expire.
            result = subprocess.run(
                [runtime, "image", "exists", image],
                capture_output=True,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning(
                "hal0.podman_ro.image_present_unanswered image=%s reason=seam-error error=%s — "
                "the local podman could not be run; reporting image_status=unknown",
                image,
                exc,
            )
            return None
        if result.returncode == 0:
            return True
        if result.returncode == 1:
            return False
        log.warning(
            "hal0.podman_ro.image_present_unanswered image=%s reason=podman-failed rc=%s — "
            "the local podman ran but failed operationally; reporting image_status=unknown",
            image,
            result.returncode,
        )
        return None

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

        #1889: routed through the ``hal0-podman-ro`` ``container-image`` seam
        first, for the same reason as :meth:`image_present` — the slot's
        container lives in ROOT's podman store, so hal0-api's own rootless
        inspect never found it and ``actual_image`` was always ``null``. The
        seam takes the bare instance TOKEN and builds ``hal0-slot-<token>``
        root-side; the rootless path below is the dev/CI fallback.
        """
        token = _artefact_token(slot)
        seam_ref = podman_introspect.container_image(token)
        if seam_ref:
            return seam_ref
        if is_hal0_service_user():
            # Same reasoning as image_present: on a provisioned box the
            # rootless store cannot hold this container, so asking it is a
            # wasted spawn in the status hot path that can only ever answer
            # None — or, worse, answer about a same-named container that is
            # not this slot's.
            return None
        try:
            runtime = _container_runtime()
        except RuntimeError:
            return None
        container_name = slot_container_name(token)
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

        #1889: routed through the ``hal0-podman-ro`` ``container-argv`` seam
        first, same rationale as :meth:`running_image`.
        """
        token = _artefact_token(slot)
        seam_json = podman_introspect.container_argv(token)
        if seam_json is not None:
            parsed = _decode_argv_json(seam_json)
            if parsed is not None:
                return parsed
        if is_hal0_service_user():
            return None
        try:
            runtime = _container_runtime()
        except RuntimeError:
            return None
        container_name = slot_container_name(token)
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
        return _decode_argv_json(result.stdout)

    async def pull_image_stream(self, image: str):
        """Async generator that runs ``<runtime> pull <image>`` and yields
        layer-progress dicts.

        Yields dicts::

            {"state": "pulling",  "layer": N, "total_layers": M, "line": "<raw line>"}
            {"state": "completed"}
            {"state": "failed",   "error": "<message>"}

        Layer counting heuristic lives in :class:`hal0.providers.podman_mutate.PullLineParser`,
        shared with the rootful pull path
        (:func:`hal0.providers.podman_mutate.pull_image_stream_rootful`) so both
        report identical event shapes regardless of which store they target.

        Routing (#2119, runner-images v3 D1a): the rootful ``hal0-podman-rw``
        seam is the PRIMARY path on an installed box — slots launch from
        ROOT's podman store, so a dashboard-triggered pull that landed in
        hal0-api's own rootless store was invisible to every slot that tried
        to use it. When :func:`hal0.providers.podman_mutate.rw_seam_available`
        says the seam is usable from here, this delegates wholesale to
        :func:`hal0.providers.podman_mutate.pull_image_stream_rootful` (same
        event shapes, so callers need no changes). The rootless path below is
        unchanged and remains the path for dev/CI and any box without the
        grant installed, where there is no seam to route through and the
        operator's own store IS the one slots use.
        """
        from hal0.providers import podman_mutate

        if podman_mutate.rw_seam_available():
            # ``async with contextlib.aclosing(...)`` (not a bare ``async for``)
            # is load-bearing: a bare ``async for ... yield`` never forwards
            # this generator's own ``GeneratorExit``/``.aclose()`` to the inner
            # one, so a dashboard cancel (``runner_pull.run_runner_pull`` calls
            # ``agen.aclose()``) would return without killing the ROOT-owned
            # ``sudo hal0-podman-rw image-pull`` subprocess — it keeps pulling
            # until GC eventually (maybe) finalizes the inner generator.
            # ``aclosing`` guarantees the inner generator's ``.aclose()`` (and
            # therefore its ``finally: proc.kill()``) runs before this
            # generator's own close completes, on every exit path.
            async with contextlib.aclosing(podman_mutate.pull_image_stream_rootful(image)) as inner:
                async for event in inner:
                    yield event
            return

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

        parser = PullLineParser()

        try:
            assert proc.stdout is not None
            async for raw in proc.stdout:
                line = raw.decode("utf-8", errors="replace").rstrip()
                if not line:
                    continue
                yield parser.feed(line)
        except Exception as exc:
            yield {"state": "failed", "error": str(exc)}
            return
        finally:
            with contextlib.suppress(ProcessLookupError, OSError):
                proc.kill()

        exit_code = await proc.wait()
        if exit_code == 0:
            yield {
                "state": "completed",
                "layer": parser.done_layers,
                "total_layers": parser.total_layers,
            }
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

    Returns ``None`` when the slot cannot launch (its named profile is
    missing, or the slot is unconverted).
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


def _resolve_preview_bundle(
    slot_cfg: dict[str, Any],
    model_path: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Resolve ``(model_info, scalars)`` for a slot's preview/status render.

    SINGLE SOURCE for every preview- or status-side consumer that needs
    scalar-derived facts without a live model download: :func:`_resolve_slot_argv`
    (the "resolved command" drawer) calls THIS instead of re-deriving
    profile / model info / runner itself — the actual
    scalar resolution is :func:`_resolve_llama_scalars` (``for_launch=False``,
    the preview/status contract, never the launch one), the SAME function
    :meth:`ContainerProvider.container_spec` calls for a real launch. A
    future change inside ``_resolve_llama_scalars`` (how model_info/runner
    resolve, a new capability gate, …) therefore reaches every consumer
    identically — there is no second call site to silently drift out of
    parity.

    Returns ``None`` on ANY resolution failure (a missing profile, an
    unconverted slot) — best-effort, fail-open, the contract every caller
    already relies on.
    """
    try:
        profile = resolve_slot_profile(slot_cfg)
        model_info = _best_effort_model_info(slot_cfg, model_path)
        scalars = _resolve_llama_scalars(slot_cfg, model_info, profile)
    except Exception:
        return None
    return model_info, scalars


def strict_slot_argv(slot_cfg: dict[str, Any]) -> ResolvedArgv:
    """The argv a llama slot launches with, raising on any failure.

    The profile-centric conversion verifies with this, not the fail-open
    preview: a registry or catalog error must abort (and roll back) the run,
    never pass for an argv mismatch. Only a model with no registry row falls
    back, exactly as the launch does, to ``--model <model id>``.
    """
    from hal0.registry.store import ModelNotFound, ModelRegistry

    profile = resolve_slot_profile(slot_cfg)
    model_table = slot_cfg.get("model") if isinstance(slot_cfg.get("model"), dict) else {}
    model_id = str(model_table.get("default") or "")
    model_info: dict[str, Any] = {}
    if model_id:
        model_info["_model_key"] = model_id
        try:
            model = ModelRegistry().get(model_id)
        except ModelNotFound:
            model = None
        if model is not None:
            model_info.update(model.model_dump())
        model_info["path"] = str(model_info.get("path") or model_id)
    scalars = _resolve_llama_scalars(slot_cfg, model_info, profile)
    port = int(slot_cfg.get("port") or slot_cfg.get("slot", {}).get("port") or 0)
    return resolve_argv(_segments_from_scalars(port, str(model_info.get("path") or ""), scalars))


def _resolve_slot_argv(
    slot_cfg: dict[str, Any],
    model_path: str | None = None,
) -> tuple[str, ResolvedArgv] | None:
    """Build the labelled argv segments for a slot and resolve them.

    Returns ``(image, ResolvedArgv)`` — the deduped flag portion (image
    excluded) plus per-flag provenance — or ``None`` when the slot cannot
    launch (a missing profile, an unconverted slot). Consumes the SAME
    :func:`_llama_argv_segments` builder and :func:`_resolve_llama_scalars`
    resolver (via :func:`_resolve_preview_bundle`) the launch path uses, so
    the previewed argv (including the mtp override, model-registry defaults,
    chat-template file, mmproj, slot ngl override, and the always-resolved
    ctx-size) is byte-identical to what :meth:`ContainerProvider.container_spec`
    would launch.
    """
    pin = blocked_slot_pin(slot_cfg) if _has_tuning_keys(slot_cfg) else None
    if pin is not None and pin.get("argv") is not None:
        # A slot the conversion blocked launches its pre-conversion tune, on
        # today's base and placement — exactly as container_spec builds it.
        placement = _placement_only(slot_cfg)
        placed = _resolve_preview_bundle(placement, model_path)
        if placed is None:
            return None
        info, sc = placed
        port = int(placement.get("port") or placement.get("slot", {}).get("port") or 0)
        try:
            segments = _pinned_segments(pin, placement, port, str(info.get("path") or ""), sc)
            return str(sc["image"]), resolve_argv(segments)
        except Hal0Error:
            return None
    resolved = _resolve_preview_bundle(slot_cfg, model_path)
    if resolved is None:
        return None
    model_info, scalars = resolved

    # port: may be at top-level or nested under [slot]
    port = int(slot_cfg.get("port") or slot_cfg.get("slot", {}).get("port") or 0)

    segments = _segments_from_scalars(port, str(model_info.get("path") or ""), scalars)
    return str(scalars["image"]), resolve_argv(segments)


def resolved_argv_detail_for_slot(
    slot_cfg: dict[str, Any],
    model_path: str | None = None,
) -> dict[str, Any] | None:
    """Structured resolution for the dashboard's "resolved command" drawer.

    Returns ``{"argv", "provenance", "removed"}`` where ``provenance`` lists each
    surviving flag with the layer that set its final value (``base`` /
    ``model`` / ``profile`` / ``slot``) —
    so an operator can see exactly which
    source won each flag and how many duplicates were collapsed. The UI renders
    these labels generically. ``None`` for a slot that cannot launch.
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


def _split_image_tag(name: str) -> tuple[str, str]:
    """Split ``repo[:tag]`` on the tag colon only.

    A registry port (``localhost:5000/foo``) puts a colon BEFORE the last
    ``/``; a naive ``split(":")`` would amputate the host and silently compare
    two different repositories as equal.
    """
    last_slash = name.rfind("/")
    last_colon = name.rfind(":")
    if last_colon > last_slash:
        return name[:last_colon], name[last_colon + 1 :]
    return name, ""


def canonical_image_ref(ref: str) -> str:
    """Expand an image ref to its fully-qualified form for comparison (#1889).

    Podman reports a container's ``ImageName`` in canonical form —
    ``docker.io/library/alpine:latest`` — while hal0 profiles routinely
    declare the shorthand an operator would type (``alpine``,
    ``alpine:latest``, ``myorg/img``). Before #1889 that never mattered:
    :meth:`ContainerProvider.running_image` read the wrong podman store and
    returned ``None`` on every deployed box, so :func:`_image_mismatch` was
    unreachable. Making it reachable without this normalisation would have
    turned a literal string compare into a drift alarm on every correctly
    running slot — trading an inert detector for a lying one.

    Applies exactly the two implicit rules the OCI/distribution reference
    grammar defines, and nothing else:

      * a name with no registry component gets ``docker.io/``. A first
        component is a registry only if it contains ``.`` or ``:``, or is
        literally ``localhost`` — the standard heuristic;
      * a Docker Hub name whose repository is a single component gets the
        implicit ``library/`` namespace. This is applied AFTER the registry
        rule so it also catches an explicitly-qualified ``docker.io/alpine``,
        which podman likewise reports as ``docker.io/library/alpine``;
      * a name with neither tag nor digest gets ``:latest``.

    Never raises and never invents: anything it cannot parse is returned
    stripped but otherwise untouched.
    """
    ref = ref.strip()
    if not ref:
        return ""

    name, sep, digest = ref.partition("@")
    digest = f"@{digest}" if sep else ""

    name, tag = _split_image_tag(name)

    first, slash, _rest = name.partition("/")
    has_registry = bool(slash) and ("." in first or ":" in first or first == "localhost")
    if not has_registry:
        name = f"docker.io/{name}"

    # Docker Hub's implicit `library/` namespace, applied to the repository
    # portion regardless of how the registry got there — `alpine`,
    # `docker.io/alpine` and `docker.io/library/alpine` all name one image,
    # and podman reports the last form.
    registry, _, repository = name.partition("/")
    if registry == "docker.io" and "/" not in repository:
        name = f"docker.io/library/{repository}"

    if not tag and not digest:
        tag = "latest"

    return f"{name}{f':{tag}' if tag else ''}{digest}"


def _image_mismatch(running_image: str | None, declared_image: str | None) -> bool:
    """Return True iff both image refs are known AND differ (#663).

    The deterministic replacement for the /proc ``actual_backend`` sniff on
    container slots: the running backend IS the image tag, so drift is a ref
    comparison (the running container's image vs the slot profile's declared
    image). Returns False whenever the running image can't be determined
    (container down / inspect failed) - never cry wolf on missing data, same
    omit-don't-guess contract as ``resolve_actual_backend``.

    Both sides are canonicalised first (:func:`canonical_image_ref`) so the
    shorthand a profile declares compares equal to the fully-qualified name
    podman reports — see that function for why #1889 makes this load-bearing.

    Digests decide identity whenever both sides carry one: a digest IS the
    immutable image id, so ``ghcr.io/x/y:v1@sha256:D`` and
    ``ghcr.io/x/y@sha256:D`` are the same image and the tag text is noise.
    When only ONE side is digest-pinned the comparison falls back to the
    REPOSITORY alone — deciding whether ``ghcr.io/x/y:v2`` and
    ``ghcr.io/x/y@sha256:…`` match needs a registry round-trip this hot path
    will never make, and guessing "drifted" there is the same cry-wolf
    failure the docstring above forbids.
    """
    if not running_image or not declared_image:
        return False

    running = canonical_image_ref(running_image)
    declared = canonical_image_ref(declared_image)
    if running == declared:
        return False

    running_repo, _, running_digest = running.partition("@")
    declared_repo, _, declared_digest = declared.partition("@")
    running_name = _split_image_tag(running_repo)[0]
    declared_name = _split_image_tag(declared_repo)[0]

    if running_digest and declared_digest:
        return running_name != declared_name or running_digest != declared_digest
    if running_digest or declared_digest:
        return running_name != declared_name

    return True
