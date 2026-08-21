"""Shared helpers for GPU device + group exposure to provider containers.

Lives here so each provider (llama-server, moonshine, kokoro, flm, …) gets
the same numeric-GID treatment for ``docker run --group-add``: the toolbox
images ship with a stock ``ubuntu:24.04`` ``/etc/group`` that has no
``render``/``video`` entries, so passing the names fails fast inside the
container ("unable to find group ..."). The kernel only checks integers
when gating access to ``/dev/dri/renderD128`` etc., so resolve to host
GIDs once and pass them through.

Vendor split (GPU generalization wave):

* AMD path — ``/dev/kfd`` + explicit ``/dev/dri`` nodes passed as plain
  ``--device=<path>`` entries plus the render/video GIDs above.
* NVIDIA path — CDI (Container Device Interface) via the
  nvidia-container-toolkit: ``--device nvidia.com/gpu=all`` (or
  ``nvidia.com/gpu=<n>`` when a slot pins one GPU). CDI names are NOT
  filesystem paths: no existence filtering, no ``--group-add`` — the CDI
  spec injects the device nodes, libraries, and permissions itself.

Which path applies is decided from the slot's declared device/profile
(``gpu-cuda`` device or a profile with ``backend="cuda"``) — never by
probing the host at spec-build time.
"""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Callable
from typing import Any, Literal

import structlog

log = structlog.get_logger(__name__)

#: What a slot's runtime does with a GPU device, for
#: :func:`require_kfd_for_gpu_slot`. Deliberately NOT the slot's ``device``
#: string, which cannot answer the question — see that function's docstring
#: and :func:`runtime_lane_for_provider` (#1941).
#:
#: * ``"llama"``   — the unified ROCmFPX llama.cpp runner (#1888's lane).
#: * ``"rocm"``    — a non-llama runtime whose image resolves ROCm/HIP.
#: * ``"no-rocm"`` — a runtime that never touches HIP.
RuntimeLane = Literal["llama", "rocm", "no-rocm"]


def runtime_lane_for_provider(spec_provider: Any | None) -> RuntimeLane:
    """Translate a resolved spec provider into a :data:`RuntimeLane`.

    ``spec_provider`` is exactly what
    :func:`hal0.providers.container._spec_provider_for` returns: ``None`` for
    the default llama-server GPU provider, else the single-purpose runtime's
    provider. The ROCm question is answered by the provider's own
    :attr:`~hal0.providers.base.Provider.gpu_runtime_needs_rocm` declaration
    rather than by anything derived from the device string, because the
    catalog labels every non-llama GPU runtime ``gpu-vulkan`` regardless of
    what its image actually runs (#1941).

    Reads the attribute defensively (``getattr``) so a duck-typed test double
    standing in for a provider is treated as a plain non-ROCm runtime instead
    of raising on the load path.
    """
    if spec_provider is None:
        return "llama"
    return "rocm" if getattr(spec_provider, "gpu_runtime_needs_rocm", False) else "no-rocm"


# Linux-convention GID fallbacks (Strix Halo LXC values; also the historical
# hal0 defaults). Used only as the LAST resort in resolve_gpu_group_ids —
# see the fallback chain documented there.
_GPU_GROUP_FALLBACK_GIDS: dict[str, int] = {"render": 993, "video": 44}

#: The ROCm compute device node. Its presence inside the container is what
#: decides which llama.cpp backend the unified ROCmFPX runner image actually
#: executes on: ``ggml_rocm_init`` succeeds and the slot runs ROCm, or it
#: fails and llama.cpp SILENTLY falls back to that image's Vulkan backend.
KFD_DEVICE_PATH = "/dev/kfd"

#: The DRM device directory. The ``renderD*`` node under it is what the Vulkan
#: lane actually runs on — Vulkan needs no ``/dev/kfd`` at all, which is why a
#: kfd-less box can serve the Vulkan lane and nothing else (#1948).
DRI_DEVICE_DIR = "/dev/dri"

#: Escape hatch for the ROCm requirement below (#1888). Set to ``1`` only when
#: you knowingly want a lane whose output may be invalid — there is no
#: supported configuration where this is the right answer; it exists so a box
#: can be inspected, not run. It cannot conjure a missing device node, so it
#: applies to the correctness gates (missing ``/dev/kfd``, a runner image whose
#: Vulkan backend is not validated) and never to a missing render node.
ENV_ALLOW_VULKAN_FALLBACK = "HAL0_ALLOW_VULKAN_FALLBACK"


class GpuPreflightError(RuntimeError):
    """A GPU slot cannot be launched on this host as configured.

    Raised loudly at slot-load time rather than letting the launch "succeed"
    into a silently-degraded lane.
    """


#: sysfs marker for "the amdgpu kernel driver is bound on this host". Used to
#: scope the ROCm requirement to AMD: an Intel or NVIDIA GPU has no
#: ``/dev/kfd`` to forward in the first place, and its Vulkan lane is not what
#: #1888 characterised.
_AMDGPU_MODULE_DIR = "/sys/module/amdgpu"


#: uid the slot containers are launched as. hal0's ``hal0-slot@.service`` units
#: carry no ``User=``, so podman runs rootful and the container process opens
#: ``/dev/kfd`` as root — regardless of which user ``hal0-api`` itself runs as.
#: See :func:`kfd_present` for why that distinction is load-bearing.
SLOT_RUNNER_UID = 0

#: :func:`kfd_status` verdicts.
KFD_OK = "ok"
KFD_MISSING = "missing"
KFD_NOT_OPENABLE = "not-openable"


def resolve_image_runtime_uid(image_ref: str | None) -> int:
    """UID the container process will actually run as, best-effort.

    Rootful podman does NOT imply the process is root: an image declaring
    ``USER`` runs as that user unless the unit overrides it, and this repo ships
    exactly that pattern (``packaging/toolbox/cpu.Dockerfile`` ends ``USER
    hal0``). Equating "rootful podman" with uid 0 would let :func:`kfd_status`
    pass a ``0660 root:root`` node that the real process cannot open — waving
    through the invalid Vulkan fallback the guard exists to stop (#1953).

    Routed through the ``hal0-podman-ro`` root-store seam, NOT a bare ``podman
    image inspect`` (#1953 R2). hal0-api runs as the unprivileged ``hal0`` user
    with no subuid ranges, so a bare call reads hal0's own ROOTLESS store —
    a different store, which never contains a slot image. That would make this
    check a silent no-op in production, which is #1889 with extra steps.

    Best-effort by design: an unanswerable seam falls back to
    :data:`SLOT_RUNNER_UID`, the correct answer for every GPU runner hal0
    currently ships (none declare ``USER``). A DECLARED non-root user is
    honoured, which is the conservative direction — it can only make the guard
    refuse more, never less.
    """
    if not image_ref:
        return SLOT_RUNNER_UID
    from hal0.providers import podman_introspect

    user = podman_introspect.image_user(image_ref)
    if user is None:
        # Seam did not answer (not the service user, no grant, podman absent).
        # Deliberately no rootless fallback — see the docstring.
        return SLOT_RUNNER_UID
    user = user.strip().split(":")[0]
    if not user or user in ("root", "0"):
        return SLOT_RUNNER_UID
    if user.isdigit():
        return int(user)
    try:
        import pwd

        return pwd.getpwnam(user).pw_uid
    except Exception:
        # A name the HOST cannot resolve may still resolve inside the image.
        # Assume non-root and let the mode check decide rather than falling
        # back to the permissive root answer.
        return -1


def kfd_status(
    kfd_path: str = KFD_DEVICE_PATH,
    *,
    for_uid: int | None = None,
) -> str:
    """Classify the ROCm compute node: absent, present-but-unopenable, or fine.

    Split out from :func:`kfd_present` because the two failure modes need
    OPPOSITE remedies and conflating them sends operators to reboot a box
    whose device is already forwarded (#1953):

    * :data:`KFD_MISSING` — not forwarded. Remedy is an LXC ``dev`` entry
      plus ``pct stop/start``.
    * :data:`KFD_NOT_OPENABLE` — forwarded, but its owning gid grants nothing
      to ``for_uid``. Remedy is a group fix on the node; no reboot involved.

    ``for_uid`` names the identity that will actually open the device:

    * ``None`` — THIS process. The only form that can be probed directly, so
      the only one that consults ``os.access``. No uid-0 short-circuit: root
      bypasses DAC only with ``CAP_DAC_OVERRIDE``, which a hardened container
      drops.
    * ``0`` — the rootful slot container, always a DIFFERENT process. Podman
      grants it ``CAP_DAC_OVERRIDE`` by default and its capability set is not
      the caller's, so this answers :data:`KFD_OK` on existence regardless of
      who is asking. Branching on whether the CALLER happens to be root would
      make the parameter collapse for root callers (``install.sh``,
      ``install.profile_derive``) — the same harm this parameter exists to fix.
    * any other uid — evaluated against that uid's group set via the mode bits,
      because ``os.access`` can only answer about the current process.
    """
    if not os.path.exists(kfd_path):
        return KFD_MISSING
    # Branch on the QUESTION, not on who is asking. ``for_uid=None`` is the
    # only form that names THIS process, and therefore the only one that can
    # be probed directly. Branching on ``uid == os.geteuid()`` instead made the
    # identity parameter silently collapse whenever the caller happened to
    # share the uid it was asking about — so a root caller (install.sh,
    # install/profile_derive.py) on a box whose root lacks CAP_DAC_OVERRIDE
    # got "unopenable" for a node the rootful slot container opens perfectly
    # well. That is #1953's original harm wearing a different hat.
    if for_uid is None:
        # Root gets NO short-circuit here: it bypasses DAC only with
        # CAP_DAC_OVERRIDE, and a hardened container (CI runs in one) drops
        # it, so a blanket "root is fine" reports a genuinely unopenable node
        # as usable — failing OPEN, into the poisoned lane.
        return KFD_OK if os.access(kfd_path, os.R_OK | os.W_OK) else KFD_NOT_OPENABLE
    if for_uid == 0:
        # Always ANOTHER process: the rootful slot container, which podman
        # grants CAP_DAC_OVERRIDE by default. Its capability set is not the
        # caller's and cannot be probed from here, so the assumed-override
        # answer is right regardless of who is asking.
        return KFD_OK
    # Some other uid — evaluate the mode bits against its group set rather
    # than silently answering for the wrong identity.
    return KFD_OK if _mode_grants_rw(kfd_path, for_uid) else KFD_NOT_OPENABLE


def _mode_grants_rw(path: str, uid: int) -> bool:
    """Would ``uid`` get read+write on ``path`` under plain DAC rules?"""
    try:
        st = os.stat(path)
    except OSError:
        return False
    if st.st_uid == uid:
        return st.st_mode & stat.S_IRUSR and st.st_mode & stat.S_IWUSR
    if st.st_mode & stat.S_IROTH and st.st_mode & stat.S_IWOTH:
        return True
    if not (st.st_mode & stat.S_IRGRP and st.st_mode & stat.S_IWGRP):
        return False
    try:
        import grp
        import pwd

        name = pwd.getpwuid(uid).pw_name
        member_gids = {g.gr_gid for g in grp.getgrall() if name in g.gr_mem}
        member_gids.add(pwd.getpwuid(uid).pw_gid)
    except Exception:  # pragma: no cover - no pwd/grp (non-Linux, minimal img)
        return False
    return st.st_gid in member_gids


def kfd_present(
    kfd_path: str = KFD_DEVICE_PATH,
    *,
    for_uid: int | None = SLOT_RUNNER_UID,
) -> bool:
    """Is the ROCm compute node visible AND usable by the identity that runs it?

    Existence alone is not enough: an LXC passthrough with a mis-mapped gid
    leaves ``/dev/kfd`` visible but unopenable, HIP still fails to initialise,
    and llama.cpp still lands on the invalid Vulkan lane — the exact
    false-pass shape ``preflight_gpu``'s gid check exists to catch on the
    render node. So this also requires read+write access.

    **Whose access, though.** This used to test the CALLING process, which is
    wrong for the question every caller is actually asking: "will the slot
    container be able to use the GPU?" ``hal0-api`` runs as user ``hal0``,
    the slot containers run rootful as root, and a plain LXC passthrough
    routinely leaves ``/dev/kfd`` as ``root:root 0660`` while
    ``/dev/dri/renderD128`` lands ``root:render 0660``. On such a box ROCm
    worked perfectly while this function reported False from the API process,
    and #1923's guard refused every AMD GPU slot (#1953). So the identity is
    now explicit and defaults to :data:`SLOT_RUNNER_UID`.

    Pass ``for_uid=None`` to ask about the current process instead.

    Still cheap — no ioctl, no driver probe, never raises. A genuinely
    functional ROCm probe belongs in the output-sanity readiness gate
    (#1922), not on the slot-load hot path.
    """
    return kfd_status(kfd_path, for_uid=for_uid) == KFD_OK


def resolve_kfd_target_gid(
    node_paths: list[str] | None = None,
) -> int | None:
    """Which gid SHOULD own ``/dev/kfd`` on this box? ``None`` when unknowable.

    Deliberately derived from the render node rather than from
    ``grp.getgrnam("render")``: the kernel gates on the integer, and the group
    NAME for that integer is not portable across hosts. On a halo143-class box
    ``renderD128`` is owned by gid 993 whose ``/etc/group`` name is ``clock``,
    while ``render`` resolves to a different, useless gid — so a name lookup
    produces a number that grants nothing. The render node is already the
    authority :func:`resolve_gpu_group_ids` follows for ``--group-add``; the
    compute node must agree with it or the two devices grant different access.

    Never returns the ``_GPU_GROUP_FALLBACK_GIDS`` constants: guessing 993 on a
    box we could not read is exactly how a wrong gid gets baked in. No render
    node → no opinion.
    """
    if node_paths is None:
        node_paths = resolve_gpu_device_paths()
    node = _device_node_for_group("render", node_paths)
    if node is None:
        return None
    try:
        return os.stat(node).st_gid
    except OSError:
        return None


def converge_kfd_device_group(
    kfd_path: str = KFD_DEVICE_PATH,
    *,
    node_paths: list[str] | None = None,
    dry_run: bool = False,
) -> tuple[str, str]:
    """Align ``/dev/kfd``'s group with the render node's. Returns (status, detail).

    Idempotent and safe to run on every install/update: a box that is already
    consistent is a no-op, and a box with no render node or no compute node is
    left alone rather than guessed at.

    Statuses: ``"noop"``, ``"changed"``, ``"skipped"``, ``"failed"``.

    ``failed`` is not fatal by design — on an UNPRIVILEGED LXC the node's
    ownership is host-mapped and ``chgrp`` returns EPERM, where the real remedy
    is a ``gid=`` on the host's ``dev`` entry. Callers surface that remedy
    instead of aborting.
    """
    if not os.path.exists(kfd_path):
        return "skipped", f"{kfd_path} is not present"
    target = resolve_kfd_target_gid(node_paths)
    if target is None:
        return "skipped", "no render node to take the gid from"
    try:
        st = os.stat(kfd_path)
    except OSError as exc:
        return "failed", f"cannot stat {kfd_path}: {exc}"
    if st.st_gid == target and st.st_mode & stat.S_IRGRP and st.st_mode & stat.S_IWGRP:
        return "noop", f"{kfd_path} already group {target} with group rw"
    if dry_run:
        return "changed", f"would set {kfd_path} to gid {target}, mode 0660"
    try:
        os.chown(kfd_path, -1, target)
        os.chmod(kfd_path, 0o660)
    except OSError as exc:
        return "failed", (
            f"cannot set {kfd_path} to gid {target} ({exc}). On an unprivileged "
            f"LXC set it on the host instead: dev entry '{kfd_path},gid={target}' "
            "(the gid INSIDE the container), then pct stop/start."
        )
    return "changed", f"{kfd_path}: gid {st.st_gid} -> {target}, mode 0660"


def render_node_present(dri_dir: str = DRI_DEVICE_DIR) -> bool:
    """Is a DRM render node visible AND usable by this process?

    The Vulkan analogue of :func:`kfd_present`, and gated the same way for the
    same reason: a ``renderD*`` node that exists but cannot be opened by the
    runner identity (the mis-mapped-gid LXC passthrough shape) is not a device
    the slot can use, and treating existence as sufficient reproduces exactly
    the false pass that made #1888 expensive.

    Only ``renderD*`` counts. ``card*`` is the KMS/master node — a display
    device, not a compute one; a container that gets only ``card0`` has no
    Vulkan device.

    And only a CHARACTER DEVICE counts. :func:`resolve_gpu_device_paths`
    forwards exactly the character devices under ``dri_dir``, so a regular
    file or a stale bind-mount target that merely happens to be *named*
    ``renderD128`` would pass this check and then never be forwarded into the
    container — a preflight that says yes to a device the launch then omits
    (review N3).

    Cheap and total: no ioctl, no driver probe, never raises. Whether the
    backend then produces valid tokens is the output-sanity readiness gate's
    question (#1922), not this one's.

    KNOWN GAP — #1981: the accessibility check is against the CALLING process
    (``hal0-api``, running as ``hal0``), while the slot container runs rootful
    and is additionally handed the node's numeric gid via
    :func:`resolve_gpu_group_ids` → ``--group-add``. On a host where ``hal0``
    is not in the render node's owning group this refuses a slot the container
    would have opened fine — #1953's shape, on the render node. Tracked
    separately rather than guessed at here, because the correct fix is the
    identity-aware evaluation #1954 introduces for ``/dev/kfd``.
    """
    try:
        entries = sorted(os.listdir(dri_dir))
    except OSError:
        return False
    for name in entries:
        if not name.startswith("renderD"):
            continue
        node = os.path.join(dri_dir, name)
        try:
            if not stat.S_ISCHR(os.stat(node).st_mode):
                continue
        except OSError:
            continue
        if os.access(node, os.R_OK | os.W_OK):
            return True
    return False


def image_serves_vulkan_lane(image: str | None) -> bool:
    """May this runner image serve the ``gpu-vulkan`` llama.cpp lane?

    Membership in :data:`~hal0.config.schema.VULKAN_CAPABLE_IMAGE_REFS` — an
    allowlist earned per-image by the #1948 §3-C validation matrix, NOT an
    ordering comparison over tags. That constant's docstring carries the full
    argument for why an order cannot be written honestly here; the short form
    is that the tags are shortrefs and date stamps rather than versions, and
    that Vulkan correctness was empirically NOT monotonic in build recency (the
    bisect found the older tree correct and the newer one broken).

    Fails closed on ``None``/blank: a caller that cannot say which image a slot
    launches has not established that the lane is safe, and the #1888 failure
    mode is silent garbage at full speed — the expensive direction to guess
    wrong in.
    """
    from hal0.config.schema import VULKAN_CAPABLE_IMAGE_REFS

    return (image or "").strip() in VULKAN_CAPABLE_IMAGE_REFS


def effective_runner_image(slot_cfg: Any | None = None) -> str | None:
    """What image will this slot ACTUALLY end up running?

    The one question every pre-launch decision needs answered, asked once so
    the answers cannot disagree (review B5). ``None`` means "a slot that does
    not exist yet and pins nothing" — the case the derivation ladders and the
    bench harness are deciding for.

    For a real slot config it is deliberately NOT just
    :func:`~hal0.providers.container._resolve_image_ref`. That returns the
    ref the TOML names *right now*, and on an upgrade the upgrade itself moves
    it: :func:`hal0.updater.updater.retag_stale_slot_images` rewrites any pin
    that is exactly a known former default
    (:data:`~hal0.config.schema.STALE_ROCMFPX_IMAGE_REFS`) to the current one.
    A decision made against the pre-retag ref is a decision about an image the
    slot is about to stop using.

    That distinction is not academic — it is the #1888 re-admission the review
    found. On a kfd-less box, ``relabel_stale_vulkan_slots`` runs BEFORE the
    retag. Judging a stale-pinned ``gpu-vulkan`` slot on its stale pin says
    "cannot serve Vulkan", so the relabel rewrites ``device`` to ``cpu``; the
    retag then runs, and because it computes the replacement from the
    *freshly rewritten* TOML it now reads ``device = "cpu"`` and installs the
    CPU toolbox image. Net effect: the GPU is stranded, and the slot's pin
    looks like a deliberate CPU choice nobody made. Looking through the retag
    first makes the relabel see the image the slot will really have, skip it,
    and leave the retag to do its job.

    Fixing it here rather than by reordering the passes is deliberate: #1954's
    ``converge_kfd_group`` pins itself before the relabel pass in its own
    docstring contract, and swapping the sequence mid-chain would re-open that.

    Resolution:

    1. ``None`` → :func:`~hal0.config.schema.resolve_default_image` for the
       AMD GPU lane, i.e. the image a new GPU slot gets with no pin.
    2. otherwise the slot's resolved ref, and if that ref is a stale former
       default, what the retag will replace it with — computed from the slot's
       CURRENT backend/device class, which is what the retag would read if it
       ran first.
    """
    from hal0.config.schema import STALE_ROCMFPX_IMAGE_REFS, resolve_default_image

    if slot_cfg is None:
        return resolve_default_image("vulkan", "gpu")

    # Local import: hal0.providers.container imports THIS module at its top
    # level, so the dependency has to be inverted lazily here.
    from hal0.providers.container import (
        _effective_backend_and_device_class,
        _resolve_image_ref,
    )

    resolved = _resolve_image_ref(slot_cfg, None)
    if resolved in STALE_ROCMFPX_IMAGE_REFS:
        backend, device_class = _effective_backend_and_device_class(slot_cfg, None)
        return resolve_default_image(backend, device_class)
    return resolved


def vulkan_lane_serves(slot_cfg: Any | None = None) -> bool:
    """Will this slot's real runner image serve the Vulkan LLM lane?

    :func:`image_serves_vulkan_lane` composed with
    :func:`effective_runner_image` — the single predicate shared by the
    derivation ladders, the bench harness, the installer preflight (mirrored
    in shell) and the updater's Vulkan-slot migration, so no two of them can
    reach different conclusions about the same box.

    Fails closed on any error: an image that cannot be resolved has not been
    established safe, and #1888's failure mode is silent garbage at full
    speed — the expensive direction to guess wrong in.
    """
    try:
        return image_serves_vulkan_lane(effective_runner_image(slot_cfg))
    except Exception:
        return False


def default_image_serves_vulkan_lane() -> bool:
    """Can THIS install's default runner image serve the Vulkan LLM lane?

    ``vulkan_lane_serves(None)`` under a name that reads correctly at the call
    sites deciding a lane for a slot that does not exist yet.

    The perimeter predicate. :func:`image_serves_vulkan_lane` answers the
    question for one slot's resolved image on the load path; this answers it
    for the image a slot would get if nobody pinned anything — which is what
    every path that decides a lane BEFORE a slot exists needs to know:

    * the bench harness, choosing which lanes to sweep
      (:func:`hal0.bench.harness.lane_is_supported`);
    * the three device-derivation ladders
      (:func:`hal0.install.profile_derive.derive_device`,
      :func:`hal0.hardware.recommend._backend_for`,
      :func:`hal0.cli.slot_commands._detect_default_hardware`);
    * the installer's GPU preflight (mirrored in shell — see
      ``_hal0_vulkan_lane_serves_default_image`` in ``installer/lib/
      preflight.sh``).

    Routing all of them through one predicate is what makes the lane re-enable
    (#1948) independent of the repin (#1959) in BOTH directions. Without it,
    an install carrying the re-enable but not the repin derives ``gpu-vulkan``
    for a kfd-less AMD box and then refuses every slot it just created — a
    regression from "slow but working on CPU" to "no loadable LLM slot at
    all" — and benches ``-dev Vulkan0`` on the known-garbage backend
    meanwhile. With it, the derivation, the bench lane and the load-time gate
    all flip at the same instant the pin does, and there is no state of main
    where they disagree.

    Resolved through :func:`~hal0.config.schema.resolve_default_image`, so the
    ``HAL0_TOOLBOX_IMAGE_*`` env overrides and the release manifest's digest
    pin are honoured exactly as they are at launch. Fails closed if that
    resolution raises at all.
    """
    return vulkan_lane_serves(None)


def host_is_amd_gpu(module_dir: str = _AMDGPU_MODULE_DIR) -> bool:
    """Is the amdgpu kernel driver bound on this host?

    Filesystem sniff only. Used to scope the ROCm requirement: on an Intel or
    NVIDIA box there is no ``/dev/kfd`` by design, so demanding one there
    would strand every GPU slot on hardware the defect was never characterised
    on.
    """
    return os.path.isdir(module_dir)


def _require_vulkan_lane_prerequisites(
    slot_name: str,
    *,
    dri_dir: str,
    image: str | None,
    env: dict[str, str] | None,
) -> None:
    """Admission check for a ``gpu-vulkan`` llama.cpp slot on an AMD host.

    Two gates, checked in that order because the first is the one #1888 is
    about and the second is an ordinary passthrough problem:

    1. **Image.** The resolved runner image must be one whose Vulkan backend
       has passed the #1948 §3-C matrix. The ade07ba lineage — still the
       default pin — emits invalid tokens on Vulkan for every model at full
       nominal speed while every health surface reads green, so serving this
       lane from it is the exact regression #1923 retired the lane to prevent.
       Refusing here means a stale-image box gets a one-line explanation
       instead of two hours of garbage.
    2. **Render node.** Vulkan runs on ``/dev/dri/renderD*``. Without one the
       container has no device at all — a forwarding problem, described as
       such, with no mention of #1888.

    Split out of :func:`require_kfd_for_gpu_slot` rather than inlined so the
    ROCm path's control flow stays readable, and so the Vulkan lane's
    prerequisites can be read (and tested) as one unit.
    """
    environ = os.environ if env is None else env
    opted_in = str(environ.get(ENV_ALLOW_VULKAN_FALLBACK, "")).strip() in ("1", "true", "yes")

    if not image_serves_vulkan_lane(image):
        from hal0.config.schema import VULKAN_FIXED_IMAGE

        resolved = (image or "").strip() or "<unresolved>"
        why = (
            f"Runner image {resolved} is not validated for the Vulkan lane: the "
            "pinned ROCmFPX lineage's Vulkan backend emits invalid tokens for "
            "every model it serves, at full nominal speed, while HTTP 200, "
            "container health and hal0 doctor all read green (#1888)."
        )
        if opted_in:
            log.warning(
                "gpu_slot_vulkan_lane_unvalidated_image_allowed",
                slot=slot_name,
                device="gpu-vulkan",
                image=resolved,
                runtime_lane="llama",
                detail=why,
            )
        else:
            raise GpuPreflightError(
                f"slot {slot_name!r} (device=gpu-vulkan) cannot run on this runner "
                f"image. {why} Repin this slot to {VULKAN_FIXED_IMAGE} (or a later "
                "image validated for the Vulkan lane), or move the slot to "
                "device='gpu-rocm' on a host with /dev/kfd."
            )

    if not render_node_present(dri_dir):
        raise GpuPreflightError(
            f"slot {slot_name!r} (device=gpu-vulkan) needs a DRM render node "
            f"({dri_dir}/renderD*) that the hal0 runner identity can open, and "
            "none is available here. Forward the render node from the host "
            "(Proxmox LXC: add 'dev0: /dev/dri/renderD128' to "
            "/etc/pve/lxc/<CTID>.conf with a gid the runner is a member of, then "
            "pct stop/start), or move this slot to device='cpu'."
        )


def require_kfd_for_gpu_slot(
    slot_name: str,
    *,
    device: str,
    runtime_lane: RuntimeLane = "llama",
    kfd_path: str = KFD_DEVICE_PATH,
    dri_dir: str = DRI_DEVICE_DIR,
    image: str | None = None,
    env: dict[str, str] | None = None,
    amd_host: bool | None = None,
    runner_uid: int | Callable[[], int] = SLOT_RUNNER_UID,
) -> None:
    """Loud-fail a GPU slot that cannot validly run on this host as configured.

    The release-pinned ROCmFPX runner (``DEFAULT_ROCMFPX_IMAGE``) is a single
    HIP+Vulkan build. llama.cpp picks ROCm when ``/dev/kfd`` is visible and
    silently falls back to that image's **Vulkan** backend when
    ``ggml_rocm_init`` fails. That Vulkan backend emits invalid tokens for
    every model it serves, at full nominal speed, while HTTP 200, container
    health, ``hal0 doctor`` and the SSE ``done`` frame all read green (#1888).

    So ``/dev/kfd`` is a hard requirement for a GPU llama.cpp slot on this
    image, not an optimisation: without it there is no lane that produces
    valid output, and the honest answer is to refuse the load and say why.

    ``runtime_lane`` is what makes that decision correct for the OTHER
    runtimes (#1941). The slot's ``device`` string cannot answer it:
    :mod:`hal0.capabilities.catalog` labels every non-llama GPU runtime
    ``gpu-vulkan``, and that label means "the GPU row in the picker", not
    "this image runs Vulkan". Two of those runtimes are ROCm builds
    (``ComfyUI``'s Strix Halo image and ``Qwen3-TTS`` — the latter reachable
    on a ``gpu-vulkan`` slot because ``tts_profile_for_device`` maps ANY GPU
    device to the ``qwen3-tts`` profile), and two are CPU ONNX images that
    forward no GPU node at all (Kokoro, Moonshine). Only the provider knows
    which, so it declares
    :attr:`~hal0.providers.base.Provider.gpu_runtime_needs_rocm` and the sole
    call site (``ContainerProvider.load_sync``) translates:

    * ``"llama"`` — the default llama-server provider, i.e. the unified
      ROCmFPX runner. Gated, with #1888's silent-garbage explanation.
    * ``"rocm"`` — a non-llama runtime whose image resolves ROCm/HIP.
      Gated, but the refusal does not blame llama.cpp's fallback: quoting
      #1888 at a ComfyUI operator sends them chasing the wrong defect.
    * ``"no-rocm"`` — a runtime that never touches HIP (Kokoro / Moonshine,
      and any genuinely-Vulkan image). Its ``gpu-vulkan`` slots are NOT
      gated; this is the class #1941 was filed for.

    Scope by device, given that lane:

    * ``gpu-rocm`` — always gated on ``/dev/kfd``, in every lane. The device
      name IS the ROCm claim, whatever the runtime does with it. Unaffected by
      the image gate below: a ROCm slot on a Vulkan-fixed image is still a
      ROCm slot.
    * ``gpu-vulkan`` + the **llama** lane on an **AMD** host — the lane #1923
      retired and #1948 Phase D restores. NOT gated on ``/dev/kfd`` any more:
      Vulkan does not use the compute node, and the kfd-less box is precisely
      where Vulkan is the only lane there is. Gated instead on the two things
      that actually decide whether it produces language:
      :func:`image_serves_vulkan_lane` (the resolved runner image must be one
      whose Vulkan backend passed the #1948 §3-C matrix — the ade07ba lineage
      never can) and :func:`render_node_present` (a ``renderD*`` node the
      runner identity can open).
    * ``gpu-vulkan`` + the **rocm** lane — unchanged (#1952). ComfyUI and
      Qwen3-TTS run HIP images behind the picker's GPU row; the llama.cpp
      image allowlist says nothing about them, so they keep the kfd gate.
    * ``gpu-vulkan`` + the **no-rocm** lane — unchanged (#1941): never gated.
    * ``gpu-vulkan`` on a **non-AMD** host — unchanged: ungated. #1925 — the
      Intel/NVIDIA Vulkan lane was never characterised and runs a different
      image entirely, so applying an AMD-derived allowlist there would strand
      hardware the defect was never about.
    * ``cpu`` / ``npu`` / ``gpu-cuda`` — never gated.

    ``runtime_lane`` defaults to ``"llama"`` and ``image`` defaults to ``None``
    on purpose, and both fail CLOSED: a caller that forgets the lane gets the
    pre-#1941 behaviour, and a caller that cannot say which image the slot
    launches gets the Vulkan refusal rather than a silent pass into the lane
    #1888 poisoned.

    ``runner_uid`` is the identity the slot container runs as — root for
    hal0's rootful ``hal0-slot@`` units. It is NOT the uid of whoever calls
    this function: ``hal0-api`` runs as ``hal0`` and would otherwise refuse
    every GPU slot on a box whose ``/dev/kfd`` is ``root:root`` but whose
    containers use it perfectly well (#1953). The same identity governs the
    render-node check on the Vulkan side (#1981).

    An explicit :data:`ENV_ALLOW_VULKAN_FALLBACK` opt-in downgrades a
    CORRECTNESS refusal (missing ``/dev/kfd``, unvalidated Vulkan image) to a
    warning — a warn, never a silent pass. It does not apply to a missing
    render node, which is a passthrough fact no env var can change.
    """
    if device == "gpu-vulkan":
        if runtime_lane == "no-rocm":
            # A runtime that never touches HIP (Kokoro / Moonshine). The
            # gpu-vulkan label is the picker's GPU row, not a ROCm claim, so
            # /dev/kfd is irrelevant to it (#1941).
            return
        if not (host_is_amd_gpu() if amd_host is None else amd_host):
            return
        if runtime_lane == "llama":
            # The re-enabled lane (#1948 Phase D). It has its own two gates and
            # deliberately does NOT fall through to the kfd requirement below.
            _require_vulkan_lane_prerequisites(slot_name, dri_dir=dri_dir, image=image, env=env)
            return
    elif device != "gpu-rocm":
        return
    # Resolved HERE, not at the call site: every early return above has already
    # happened, so a cpu/npu/gpu-cuda slot never pays for it. It is a `sudo -n`
    # round-trip plus two podman calls now, so an eager argument expression put
    # that on the load path of every slot on the box (#1953 N2).
    uid = runner_uid() if callable(runner_uid) else runner_uid
    status = kfd_status(kfd_path, for_uid=uid)
    if status == KFD_OK:
        return
    # Computed once and reused on both the warn and raise paths below, so
    # neither can drift from the lane it is actually describing (#1952
    # review): #1888 is llama.cpp's failure mode specifically, and quoting
    # it at a non-llama operator sends them chasing the wrong defect.
    if runtime_lane == "llama":
        why = (
            "The runner image falls back to its Vulkan backend without it, and "
            "that backend emits invalid tokens for every model (#1888) — "
            "refusing to start rather than serve garbage."
        )
    elif runtime_lane == "rocm":
        why = (
            "This slot's runtime image resolves ROCm at launch and cannot "
            "initialise HIP without it."
        )
    else:
        # "no-rocm" only reaches this far via device == "gpu-rocm" — the
        # gpu-vulkan branch above already returned for this lane. The image
        # never touches HIP, so the actual problem is the device
        # declaration, not the runtime.
        why = (
            "This runtime's image never touches ROCm/HIP — the slot's device "
            "is declared 'gpu-rocm' but doesn't need to be."
        )
    environ = os.environ if env is None else env
    if str(environ.get(ENV_ALLOW_VULKAN_FALLBACK, "")).strip() in ("1", "true", "yes"):
        log.warning(
            "gpu_slot_vulkan_fallback_allowed",
            slot=slot_name,
            device=device,
            kfd_path=kfd_path,
            runtime_lane=runtime_lane,
            detail=why,
        )
        return
    preamble = (
        f"slot {slot_name!r} (device={device}) needs the ROCm compute node {kfd_path}. {why} "
    )
    if status == KFD_NOT_OPENABLE:
        target = resolve_kfd_target_gid()
        owner = ""
        try:
            st = os.stat(kfd_path)
            owner = f" It is currently gid {st.st_gid}, mode {st.st_mode & 0o777:04o}."
        except OSError:  # pragma: no cover - raced away between calls
            pass
        if target is not None:
            fix = (
                f"'chgrp {target} {kfd_path} && chmod 0660 {kfd_path}' "
                f"(gid {target} is the one the render node uses on this box)"
            )
            host_fix = f"'{kfd_path},gid={target}'"
        else:
            fix = f"give it the same gid as /dev/dri/renderD*, then chmod 0660 {kfd_path}"
            host_fix = f"'{kfd_path},gid=<the render node's gid in this container>'"
        remedy = (
            f"It IS forwarded, but uid {uid} cannot open it.{owner} Do NOT "
            f"re-forward the device — fix its group instead: {fix}. On an "
            f"unprivileged LXC apply it to the host's dev entry ({host_fix}) and "
            "pct stop/start. See #1953."
        )
    else:
        remedy = (
            "It is not visible here. Forward the device from the host (Proxmox "
            f"LXC: add 'dev1: {kfd_path}' to /etc/pve/lxc/<CTID>.conf, then pct "
            "stop/start), or move this slot to device='cpu'."
        )
    raise GpuPreflightError(preamble + remedy)


def resolve_gpu_device_paths(
    kfd_path: str = "/dev/kfd",
    dri_dir: str = "/dev/dri",
) -> list[str]:
    """Return explicit GPU device-node paths to pass via ``--device=``.

    Docker recurses a ``--device=/dev/dri`` *directory* and adds every node
    under it; podman does not, and errors ``no devices found in /dev/dri`` on
    hosts whose /dev/dri holds non-standard nodes (e.g. an ``amdgpu`` node and
    no ``card0``). So we enumerate the actual character devices and pass each
    one explicitly — which is correct for docker too.

    Includes ``kfd_path`` when it exists, then every character device directly
    under ``dri_dir`` (sorted). Subdirectories (``by-path``) and regular files
    are skipped.

    Falls back to the legacy directory paths ``["/dev/kfd", "/dev/dri"]`` when
    neither exists (CI / no-GPU dev box) so unit rendering stays deterministic
    off-GPU; no container actually runs there.
    """
    paths: list[str] = []
    if os.path.exists(kfd_path):
        paths.append(kfd_path)
    try:
        entries = sorted(os.listdir(dri_dir))
    except OSError:
        entries = []
    for name in entries:
        node = os.path.join(dri_dir, name)
        try:
            if stat.S_ISCHR(os.stat(node).st_mode):
                paths.append(node)
        except OSError:
            continue
    if not paths:
        return ["/dev/kfd", "/dev/dri"]
    return paths


def _device_node_for_group(name: str, node_paths: list[str]) -> str | None:
    """Pick the discovered device node that gates a GPU access group.

    ``render`` nodes are named ``renderD*`` (e.g. ``renderD128``); ``video``
    nodes are the older KMS/master nodes, named ``card*`` (occasionally
    ``video*``). Returns the first (sorted-order) match from
    :func:`resolve_gpu_device_paths`'s output, or ``None`` when no node of
    that kind was discovered (CI/no-GPU box, or the bare-directory fallback
    path, which never matches these prefixes).
    """
    prefixes = ("renderD",) if name == "render" else ("card", "video")
    for path in node_paths:
        if os.path.basename(path).startswith(prefixes):
            return path
    return None


def _probed_gpu_group_gids() -> dict[str, int]:
    """GIDs `hal0 probe` recorded in hardware.json (``gpu_group_gids``).

    Raw-JSON read (no pydantic) so this stays cheap on every spec build and
    never raises: a missing / unparseable / pre-wave hardware.json simply
    yields ``{}`` and the caller moves on to the next fallback.
    """
    try:
        from hal0.config import paths as _paths

        raw = json.loads(_paths.hardware_json().read_text())
        table = raw.get("gpu_group_gids")
        if not isinstance(table, dict):
            return {}
        return {str(k): int(v) for k, v in table.items()}
    except Exception:
        return {}


def resolve_gpu_group_ids(node_paths: list[str] | None = None) -> list[int]:
    """Return numeric GIDs for the host's GPU access groups (render, video).

    ``node_paths`` lets a caller that has ALREADY resolved its device nodes
    (the benchmark harness — ``hal0.bench.devices``, which may be pointed at
    an operator-overridden node set) reuse them instead of re-enumerating
    ``/dev/dri``. Default ``None`` keeps the historical behaviour: resolve
    the host's nodes here.

    Fallback chain, PER GROUP, most-authoritative source first:

      1. the OWNING gid of the actual device node (``os.stat(node).st_gid``
         on the ``renderD*`` node for ``render``, the ``card*``/``video*``
         node for ``video``) — this is what the kernel actually gates on,
         so it is correct even when the host's group NAME for that gid
         differs from "render"/"video" (e.g. a halo143-class host where
         ``renderD128`` is owned by gid 993 but gid 993's /etc/group name
         is "clock", not "render" — ``grp.getgrnam("render")`` there
         resolves a DIFFERENT, wrong gid and the container ends up unable
         to read the device on any non-root-owner slot);
      2. live ``grp.getgrnam`` against the running host's /etc/group, used
         only when the device node is absent (CI/no-GPU box) — still
         better than nothing when the name happens to line up;
      3. the probe-time record in hardware.json (``gpu_group_gids``, written
         by ``hal0 probe``) — covers deployments where the API process runs
         in a context whose /etc/group lacks the entries the host actually
         uses for /dev/dri (e.g. minimal containers/chroots);
      4. the Linux-convention constants (render=993, video=44) — last resort
         so unit rendering stays deterministic on hosts with neither source
         (also the sole path on platforms without the ``grp`` module).

    Duplicate GIDs (render and video mapping to the same id) are collapsed,
    order preserved.
    """
    probed = _probed_gpu_group_gids()
    if node_paths is None:
        node_paths = resolve_gpu_device_paths()
    gids: list[int] = []
    try:
        import grp

        for name, fallback in _GPU_GROUP_FALLBACK_GIDS.items():
            node = _device_node_for_group(name, node_paths)
            if node is not None:
                try:
                    gids.append(os.stat(node).st_gid)
                    continue
                except OSError:
                    log.debug("provider.gpu_group_node_stat_failed", group=name, node=node)
            try:
                gids.append(grp.getgrnam(name).gr_gid)
                continue
            except KeyError:
                log.debug("provider.gpu_group_missing", group=name)
            recorded = probed.get(name)
            gids.append(recorded if recorded is not None else fallback)
    except ImportError:
        # No grp module (non-POSIX host): device node, then probe, then constants.
        for name, fallback in _GPU_GROUP_FALLBACK_GIDS.items():
            node = _device_node_for_group(name, node_paths)
            if node is not None:
                try:
                    gids.append(os.stat(node).st_gid)
                    continue
                except OSError:
                    log.debug("provider.gpu_group_node_stat_failed", group=name, node=node)
            gids.append(probed.get(name, fallback))
    # De-dup while preserving order (render/video can share a GID).
    seen: set[int] = set()
    out: list[int] = []
    for g in gids:
        if g not in seen:
            seen.add(g)
            out.append(g)
    return out


# ── NVIDIA / CDI ─────────────────────────────────────────────────────────────


def is_nvidia_gpu_device(device: str | None, profile_backend: str | None = None) -> bool:
    """True when a slot's declared device/profile selects the NVIDIA path.

    Decided from configuration only (``device == "gpu-cuda"`` or the profile's
    ``backend == "cuda"``) — deliberately NOT from probing the host at
    spec-build time, so unit rendering is deterministic and previewable.
    """
    if (device or "").strip().lower() == "gpu-cuda":
        return True
    return (profile_backend or "").strip().lower() == "cuda"


def nvidia_cdi_devices(gpu_index: int | None = None) -> list[str]:
    """CDI device names for NVIDIA GPU passthrough.

    ``--device nvidia.com/gpu=all`` maps every GPU; a non-None ``gpu_index``
    maps exactly that GPU (``nvidia.com/gpu=<n>``). These are CDI names, not
    paths: callers must NOT existence-filter them or attach ``--group-add``
    GIDs — the CDI spec (generated by ``nvidia-ctk cdi generate``) injects
    nodes, libraries, and permissions itself.
    """
    if gpu_index is not None and int(gpu_index) >= 0:
        return [f"nvidia.com/gpu={int(gpu_index)}"]
    return ["nvidia.com/gpu=all"]


# ── multi-GPU pinning (SlotConfig.gpu_index) ─────────────────────────────────


def gpu_visibility_env(device: str | None, gpu_index: int | None) -> dict[str, str]:
    """Visibility env a pinned slot needs, keyed by device family.

    Returns ``{}`` when ``gpu_index`` is None (no pinning — unchanged
    behaviour) or the device is not a GPU family. Callers merge the result
    UNDER ``[server].env`` so an operator's explicit env always wins::

        env = {**gpu_visibility_env(device, idx), **server_env}

    Per family:

    * ``gpu-rocm``   → ``HIP_VISIBLE_DEVICES`` + ``ROCR_VISIBLE_DEVICES``
      (HIP runtime and ROCr each honour their own variable).
    * ``gpu-vulkan`` → ``GGML_VK_VISIBLE_DEVICES`` (llama.cpp's Vulkan
      backend device filter).
    * ``gpu-cuda``   → ``CUDA_VISIBLE_DEVICES=0``: the CDI mapping
      (``nvidia.com/gpu=<n>``) already exposes only the pinned GPU, which
      appears as ordinal 0 inside the container.
    """
    if gpu_index is None:
        return {}
    idx = str(int(gpu_index))
    d = (device or "").strip().lower()
    if d == "gpu-rocm":
        return {"HIP_VISIBLE_DEVICES": idx, "ROCR_VISIBLE_DEVICES": idx}
    if d == "gpu-vulkan":
        return {"GGML_VK_VISIBLE_DEVICES": idx}
    if d == "gpu-cuda":
        return {"CUDA_VISIBLE_DEVICES": "0"}
    return {}


__all__ = [
    "default_image_serves_vulkan_lane",
    "effective_runner_image",
    "gpu_visibility_env",
    "image_serves_vulkan_lane",
    "is_nvidia_gpu_device",
    "nvidia_cdi_devices",
    "render_node_present",
    "resolve_gpu_device_paths",
    "resolve_gpu_group_ids",
    "vulkan_lane_serves",
]
