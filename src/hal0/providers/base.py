"""Provider abstract base class.

A Provider encapsulates the logic for a single inference backend
(container/llama.cpp, FLM, ComfyUI, Kokoro, Qwen3-TTS).  It is stateless:
all configuration is passed per-call via slot_cfg and model_info.
SlotManager dispatches every slot through ``ContainerProvider``, which
builds a :class:`RuntimeLaunchPlan` via ``container_spec()`` and hands it
to the single unit renderer
(:func:`hal0.providers.container._render_quadlet_from_plan`); ``health()``
and ``infer()`` probe and forward requests.

Port target: haloai lib/providers/base.py.
See PLAN.md §3, ARCHITECTURE.md §Key boundaries ("Providers are stateless").

RuntimeLaunchPlan captures everything needed to render a Podman Quadlet
``.container`` unit.  Frozen so Providers cannot accidentally share mutable
state across slots.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Mount:
    """A single volume mount, with read-only as a first-class flag.

    Providers declare intent (``read_only=True``); the renderer calls
    :meth:`render_quadlet` to produce the Quadlet ``Volume=`` value.
    """

    source: str
    target: str
    read_only: bool = False
    #: SELinux relabel option ("z" shared, "Z" private) appended to the mount
    #: opts. Required on SELinux-enforcing hosts (Fedora) so the container can
    #: read the bind; a harmless no-op where SELinux is disabled. Empty = none.
    selinux: str = ""

    def render_quadlet(self) -> str:
        """Return the Podman Quadlet ``Volume=`` value: ``src:dst[:ro,z]``.

        The mount options are a single colon-separated group (``ro,z``) —
        Quadlet's ``Volume=`` syntax.

        **NFS gotcha:** an NFS source can't be SELinux-relabelled (``chcon`` →
        ENOTSUP; podman aborts the bind). When the source is on NFS the ``:z`` /
        ``:Z`` option is omitted entirely (:func:`hal0.config.paths.is_nfs`),
        and a one-shot warning is logged — the Quadlet file is written once at
        load and can't re-render on a later mount-state change.
        """
        opts: list[str] = []
        if self.read_only:
            opts.append("ro")
        if self.selinux:
            from hal0.config.paths import is_nfs

            if is_nfs(self.source):
                _log.warning(
                    "mount.selinux_relabel_omitted_on_nfs",
                    extra={"source": self.source, "selinux": self.selinux},
                )
            else:
                opts.append(self.selinux)
        suffix = (":" + ",".join(opts)) if opts else ""
        return f"{self.source}:{self.target}{suffix}"


@dataclass(frozen=True, slots=True)
class HealthCheck:
    """A podman ``--health-*`` override for a slot container.

    The toolbox images bake a HEALTHCHECK that probes a hardcoded port; a
    slot runs its server on its own port, so the unit must override the
    health command (else ``podman ps`` shows a permanent ``unhealthy``).
    Carrying it on the launch plan keeps that knowledge declarative instead
    of inlined in the renderer (#684).
    """

    cmd: str
    start_period: str = "180s"
    interval: str = "30s"
    retries: int = 3
    timeout: str = "5s"

    def render_flags(self) -> list[str]:
        """Return the ``--health-*`` podman-run flags in a stable order."""
        return [
            f"--health-cmd={self.cmd}",
            f"--health-start-period={self.start_period}",
            f"--health-interval={self.interval}",
            f"--health-retries={self.retries}",
            f"--health-timeout={self.timeout}",
        ]

    def render_quadlet(self) -> list[str]:
        """Return the Podman Quadlet ``Health*=`` keys in a stable order.

        The declarative equivalent of :meth:`render_flags` — Quadlet emits the
        matching ``--health-*`` podman flags itself from these ``[Container]``
        keys, so the slot's own port health probe overrides the image's baked
        HEALTHCHECK exactly as before (#684).
        """
        return [
            f"HealthCmd={self.cmd}",
            f"HealthStartPeriod={self.start_period}",
            f"HealthInterval={self.interval}",
            f"HealthRetries={self.retries}",
            f"HealthTimeout={self.timeout}",
        ]


@dataclass(frozen=True)
class RuntimeLaunchPlan:
    """Typed launch plan for a container-per-slot Quadlet unit.

    Carries everything the Quadlet adapter needs to render one
    ``hal0-slot@<token>.container`` unit: image, in-container argv, mounts
    (read-only as a first-class flag), devices, security, port, and the
    optional health-check override.  Providers build one plan per
    (slot, model) pair from a :class:`hal0.profiles.ResolvedProfile`; the
    adapter (:func:`hal0.providers.container._render_quadlet_from_plan`)
    executes it.  There is exactly one renderer.

    Frozen for safety: a plan is computed once and never mutated, so two
    slots can never share mutable launch state.

    See ARCHITECTURE.md §Key boundaries and PLAN.md §2 (deployment model).
    """

    image: str
    """Fully-qualified toolbox image ref, e.g. ghcr.io/hal0ai/hal0-runner-standard:<tag>."""

    command: list[str]
    """argv for the inference process inside the container."""

    env: dict[str, str] = field(default_factory=dict)
    """Environment variables injected via docker run --env."""

    mounts: list[Mount] = field(default_factory=list)
    """Volume mounts (read-only is a first-class :class:`Mount` flag)."""

    devices: list[str] = field(default_factory=list)
    """Device paths to pass through, e.g. ["/dev/dri/renderD128"]."""

    cap_add: list[str] = field(default_factory=list)
    """Linux capabilities to add, e.g. ["SYS_PTRACE"]."""

    security_opt: list[str] = field(default_factory=list)
    """docker run --security-opt values."""

    group_add: list[str] = field(default_factory=list)
    """Supplementary group names or GIDs."""

    port: int = 0
    """Host port the container listens on (127.0.0.1 only)."""

    network_mode: str = "host"
    """Docker network mode.  "host" is the default for slot containers."""

    ipc: str = ""
    """IPC namespace mode (``podman run --ipc``), e.g. ``"host"``.  Empty keeps
    podman's private namespace."""

    ulimits: list[str] = field(default_factory=list)
    """``podman run --ulimit`` values, e.g. ``["memlock=-1"]``."""

    health: HealthCheck | None = None
    """Optional ``--health-*`` override (None = inherit the image's HEALTHCHECK)."""


class Provider(ABC):
    """Abstract base for a hal0 inference backend.

    Concrete implementations: ContainerProvider, FLMProvider,
    ComfyUIProvider, KokoroProvider, Qwen3TTSProvider.

    Each provider is instantiated once and reused across all slots that use
    that backend type.  Providers must not hold per-slot mutable state.
    """

    name: str = ""
    """Short backend identifier, e.g. "llama-server".  Used in slot configs
    and structured logs."""

    gpu_runtime_needs_rocm: bool = False
    """Does this runtime's toolbox image resolve ROCm/HIP at launch, and so
    need ``/dev/kfd`` forwarded when the slot sits on a GPU device?

    Declared per provider because a slot's ``device`` string does NOT answer
    it (#1941). ``hal0.capabilities.catalog`` labels every non-llama GPU
    runtime ``gpu-vulkan`` — that label means "the GPU row in the picker",
    not "this image runs Vulkan". ComfyUI's Strix Halo image and Qwen3-TTS
    are ROCm builds that reach the GPU through HIP and forward ``/dev/kfd``
    in their specs; Kokoro and Moonshine are CPU ONNX images that forward no
    GPU nodes at all. Only the provider knows which of those its image is, so
    it says so here and :func:`hal0.providers._gpu.require_kfd_for_gpu_slot`
    stops having to guess from the device string.

    Default ``False`` covers the CPU/NPU runtimes. The llama.cpp lane is not
    expressed here: it is the ``spec_provider is None`` default in
    ``ContainerProvider.load_sync`` and carries its own #1888 refusal wording.
    """

    @abstractmethod
    def build_env(
        self,
        slot_cfg: dict[str, Any],
        model_info: dict[str, Any],
    ) -> dict[str, str]:
        """Compute the HAL0_* settings for a slot.

        Returns a mapping of HAL0_* variable names to string values (port,
        model path, ...). The provider's launch-plan builder reads them to
        assemble the container's command and mounts; they are not written to
        an env file.

        Args:
            slot_cfg:   Raw slot TOML dict (or SlotConfig model as dict).
            model_info: Model registry entry (id, path, size_bytes, …).
        """

    @abstractmethod
    def start_cmd(self, env: dict[str, str]) -> list[str]:
        """Return the argv list for spawning this backend outside systemd.

        Mirrors the systemd ExecStart.  Used by unit template rendering
        and integration tests.
        """

    @abstractmethod
    async def health(self, port: int) -> dict[str, Any]:
        """Run a health check against the backend on *port*.

        Returns {"ok": bool, "status": str, ...}.

        For llama-server and FLM: requires non-empty /v1/models plus a
        /v1/chat/completions with max_tokens=1 (PLAN.md §5 Tier 1 fix).
        """

    @abstractmethod
    async def infer(self, port: int, body: dict[str, Any]) -> dict[str, Any]:
        """Passthrough inference against the provider's OpenAI-compatible API.

        Thin wrapper; the Dispatcher is the primary request path — this is
        used for direct provider-level tests and CLI smoke checks.
        """

    @abstractmethod
    def container_spec(
        self,
        slot_cfg: dict[str, Any],
        model_info: dict[str, Any],
    ) -> RuntimeLaunchPlan:
        """Build the RuntimeLaunchPlan for this slot + model combination.

        Called by SlotManager.spawn() to render the systemd unit override.
        """

    def image_ref(self, slot_cfg: dict[str, Any]) -> str:
        """Return the toolbox image reference for this Provider + slot config.

        Example: "ghcr.io/hal0ai/hal0-runner-standard:<tag>".
        Varies by slot.backend (Vulkan vs ROCm for llama-server).
        Concrete Providers MUST override.

        Raises:
            NotImplementedError: Until Phase 1 per-provider implementation.
        """
        raise NotImplementedError(f"Phase 1: {type(self).__name__} must implement image_ref()")

    # NOTE: unit rendering is owned by the single Quadlet adapter
    # :func:`hal0.providers.container._render_quadlet_from_plan`, which
    # understands the full :class:`RuntimeLaunchPlan` shape (first-class
    # :class:`Mount` read-only/SELinux flags via ``render_quadlet``,
    # :class:`HealthCheck` via ``render_quadlet``); providers only build the
    # plan, never render.
