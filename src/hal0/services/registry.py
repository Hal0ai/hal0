"""Declarative catalog of hal0 companion services.

One ``ServiceDef`` per service, consumed by the ``/api/services`` routes.
Everything the API needs to reason about a service lives here: the owning
systemd unit, how to probe it, which lifecycle verbs are permitted, and how
to derive a browser-facing URL.

Design constraints baked into the entries (see the unit map in
ARCHITECTURE.md and packaging/ + installer/systemd/):

* ComfyUI's lifecycle is owned by the seeded img slot unit
  (``hal0-slot@img.service``); a raw ``systemctl stop`` would bypass the
  GpuArbiter switchover logic in ``hal0.api.routes.comfyui``, so only
  ``restart`` is exposed — the same verb the FirstRun repair button uses.
* Hermes (``hal0-agent@hermes.service``, :9119) and Hindsight
  (``hindsight-api.service``, :9177) bind loopback-only: no host:port URL
  fallback exists, so a link is advertised only via their
  ``HAL0_*_PUBLIC_URL`` env override.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Every lifecycle verb any service may carry. ``systemd.unit_action``
#: enforces this set again at the execution boundary.
LIFECYCLE_ACTIONS = ("start", "stop", "restart", "enable", "disable")

#: Full verb set for services whose unit hal0 owns outright.
_FULL = LIFECYCLE_ACTIONS


@dataclass(frozen=True)
class ServiceDef:
    """One companion service as the management layer sees it."""

    id: str
    name: str
    description: str
    #: Owning systemd unit; None = unmanaged/external (no lifecycle actions).
    unit: str | None = None
    #: LAN-published port for the host:port URL fallback; None when the
    #: service binds loopback-only (or isn't deployed by hal0 at all).
    port: int | None = None
    #: Env var carrying an operator-declared public URL (reverse proxy).
    public_url_env: str | None = None
    #: Probe strategy: "http" | "systemd" | "comfyui" | "none".
    probe: str = "none"
    #: Default URL for http probes (loopback), overridable via probe_url_env.
    probe_url: str | None = None
    probe_url_env: str | None = None
    #: Lifecycle verbs the API may run for this service (subset of
    #: LIFECYCLE_ACTIONS). Empty = read-only.
    actions: tuple[str, ...] = ()
    #: Advertise via avahi/mDNS when the operator enables addon
    #: advertisement. Only meaningful for LAN-published ports.
    mdns: bool = False
    #: Loopback bind port, informational only (shown in the UI so operators
    #: know where a loopback-only service lives).
    loopback_port: int | None = None
    #: Extra UI hints (e.g. where deeper controls live).
    hints: tuple[str, ...] = field(default=())


SERVICES: tuple[ServiceDef, ...] = (
    ServiceDef(
        id="openwebui",
        name="OpenWebUI",
        description="Chat UI companion (podman container, LAN :3001).",
        unit="hal0-openwebui.service",
        port=3001,
        public_url_env="HAL0_OPENWEBUI_PUBLIC_URL",
        probe="http",
        probe_url="http://127.0.0.1:3001/health",
        probe_url_env="HAL0_OPENWEBUI_PROBE_URL",
        actions=_FULL,
        mdns=True,
    ),
    ServiceDef(
        id="comfyui",
        name="ComfyUI",
        description="Image generation engine (img slot container, LAN :8188).",
        unit="hal0-slot@img.service",
        port=8188,
        public_url_env="HAL0_COMFYUI_PUBLIC_URL",
        probe="comfyui",
        actions=("restart",),
        mdns=True,
        hints=("start/stop is GPU-arbiter managed — use the Image-Gen pane switchover",),
    ),
    ServiceDef(
        id="hermes",
        name="Hermes",
        description="Bundled agent + dashboard (loopback :9119).",
        unit="hal0-agent@hermes.service",
        public_url_env="HAL0_HERMES_PUBLIC_URL",
        probe="systemd",
        actions=_FULL,
        loopback_port=9119,
    ),
    ServiceDef(
        id="hindsight",
        name="Hindsight",
        description="Memory engine (native daemon, loopback :9177).",
        unit="hindsight-api.service",
        probe="systemd",
        actions=_FULL,
        loopback_port=9177,
    ),
)


def service_by_id(service_id: str) -> ServiceDef | None:
    """Look up a service definition, or None for unknown ids."""
    for sdef in SERVICES:
        if sdef.id == service_id:
            return sdef
    return None


__all__ = ["LIFECYCLE_ACTIONS", "SERVICES", "ServiceDef", "service_by_id"]
