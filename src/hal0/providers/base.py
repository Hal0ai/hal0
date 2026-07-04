"""Provider abstract base class.

A Provider encapsulates the logic for a single inference backend
(container/llama.cpp, FLM, ComfyUI, Kokoro, Qwen3-TTS).  It is stateless:
all configuration is passed per-call via slot_cfg and model_info.
SlotManager dispatches every slot through ``ContainerProvider``, which
builds a :class:`RuntimeLaunchPlan` via ``container_spec()`` and hands it
to the single unit renderer
(:func:`hal0.providers.container._render_unit_from_plan`); ``health()``
and ``infer()`` probe and forward requests.

Port target: haloai lib/providers/base.py.
See PLAN.md §3, ARCHITECTURE.md §Key boundaries ("Providers are stateless").

RuntimeLaunchPlan (alias ``ContainerSpec``) captures everything needed to
render a container-run-based systemd ExecStart line.  Frozen so Providers
cannot accidentally share mutable state across slots.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Mount:
    """A single volume mount, with read-only as a first-class flag.

    Replaces the old convention of smuggling ``:ro`` into the container
    target string.  The renderer calls :meth:`render` to produce the
    ``--volume`` value, so providers declare intent (``read_only=True``)
    instead of hand-appending ``:ro`` and hoping the renderer passes it
    through verbatim.
    """

    source: str
    target: str
    read_only: bool = False
    #: SELinux relabel option ("z" shared, "Z" private) appended to the mount
    #: opts. Required on SELinux-enforcing hosts (Fedora) so the container can
    #: read the bind; a harmless no-op where SELinux is disabled. Empty = none.
    selinux: str = ""

    def render(self) -> str:
        """Return the ``{src}:{dst}[:ro[,z]]`` value for ``--volume=``."""
        target = self.target
        read_only = self.read_only
        # Tolerate a target that already smuggles ``:ro`` (legacy callers /
        # coerced tuples) so we never emit a doubled ``:ro``.
        if target.endswith(":ro"):
            target = target[: -len(":ro")]
            read_only = True
        opts: list[str] = []
        if read_only:
            opts.append("ro")
        if self.selinux:
            opts.append(self.selinux)
        suffix = (":" + ",".join(opts)) if opts else ""
        return f"{self.source}:{target}{suffix}"

    @classmethod
    def coerce(cls, mount: Mount | tuple[str, str]) -> Mount:
        """Normalise a ``Mount`` or a legacy ``(src, dst)`` tuple to ``Mount``.

        A tuple whose target ends in ``:ro`` is interpreted as a read-only
        mount — the renderer stays correct whether callers were migrated to
        :class:`Mount` or still pass the old tuple shape.
        """
        if isinstance(mount, Mount):
            return mount
        source, target = mount
        if target.endswith(":ro"):
            return cls(source, target[: -len(":ro")], read_only=True)
        return cls(source, target, read_only=False)


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


@dataclass(frozen=True)
class RuntimeLaunchPlan:
    """Typed launch plan for a container-per-slot systemd unit.

    Carries everything the systemd/podman adapter needs to render one
    ``hal0-slot@<name>.service`` unit: image, in-container argv, mounts
    (read-only as a first-class flag), devices, security, port, and the
    optional health-check override.  Providers build one plan per
    (slot, model) pair from a :class:`hal0.profiles.ResolvedProfile`; the
    adapter (:func:`hal0.providers.container._render_unit_from_plan`)
    executes it.  There is exactly one argv builder.

    Frozen for safety: a plan is computed once and never mutated, so two
    slots can never share mutable launch state.

    See ARCHITECTURE.md §Key boundaries and PLAN.md §2 (deployment model).
    """

    image: str
    """Fully-qualified toolbox image ref, e.g. ghcr.io/hal0ai/hal0-toolbox-vulkan:v1."""

    command: list[str]
    """argv for the inference process inside the container."""

    env: dict[str, str] = field(default_factory=dict)
    """Environment variables injected via docker run --env."""

    mounts: list[Mount | tuple[str, str]] = field(default_factory=list)
    """Volume mounts.  Prefer :class:`Mount`; legacy ``(src, dst)`` tuples are
    coerced by the renderer (``:ro`` target suffix → ``read_only``)."""

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

    extra_args: list[str] = field(default_factory=list)
    """Escape hatch: additional docker run arguments appended verbatim."""

    health: HealthCheck | None = None
    """Optional ``--health-*`` override (None = inherit the image's HEALTHCHECK)."""


#: Backwards-compatible alias.  ``RuntimeLaunchPlan`` is the canonical name
#: (it carries health + first-class read-only mounts, not just a container
#: spec); existing imports of ``ContainerSpec`` keep working.
ContainerSpec = RuntimeLaunchPlan


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

    @abstractmethod
    def build_env(
        self,
        slot_cfg: dict[str, Any],
        model_info: dict[str, Any],
    ) -> dict[str, str]:
        """Compute the EnvironmentFile contents for a slot.

        Returns a mapping of HAL0_* variable names to string values.
        Caller (SlotManager.spawn) writes the file atomically via
        hal0.config.env.write_env_atomic (PLAN.md §5 Tier 1).

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
    ) -> ContainerSpec:
        """Build the ContainerSpec for this slot + model combination.

        Called by SlotManager.spawn() to render the systemd unit override.
        """

    def image_ref(self, slot_cfg: dict[str, Any]) -> str:
        """Return the toolbox image reference for this Provider + slot config.

        Example: "ghcr.io/hal0ai/hal0-toolbox-vulkan:v1".
        Varies by slot.backend (Vulkan vs ROCm for llama-server).
        Concrete Providers MUST override.

        Raises:
            NotImplementedError: Until Phase 1 per-provider implementation.
        """
        raise NotImplementedError(f"Phase 1: {type(self).__name__} must implement image_ref()")

    # NOTE (WS-15): the legacy ``render_systemd_override`` default — which
    # rendered a docker-run drop-in from ``container_spec`` — was deleted.
    # Unit rendering is owned by the single adapter
    # :func:`hal0.providers.container._render_unit_from_plan`, which
    # understands the full :class:`RuntimeLaunchPlan` shape (first-class
    # :class:`Mount` read-only/SELinux flags, :class:`HealthCheck`); the old
    # renderer only handled ``(src, dst)`` tuples and drifted from it.
