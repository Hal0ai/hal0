"""Companion-service management layer.

hal0 ships with (or sits next to) a set of companion services — OpenWebUI,
Hermes, Hindsight, ComfyUI — each with its own systemd unit, port,
probe, and browser-facing URL. This package is the single declarative
source of truth for that set:

* :mod:`hal0.services.registry` — the catalog (``ServiceDef`` + ``SERVICES``):
  which unit owns a service, how it is probed, which lifecycle actions the
  API may run on it, and how its URL is derived.
* :mod:`hal0.services.systemd` — shared, allow-listed systemctl helpers
  (state introspection + lifecycle verbs), fail-soft on hosts without
  systemd.
* :mod:`hal0.services.mdns` — avahi/mDNS discovery status and per-service
  ``.local`` advertisement (drop-in service files under
  ``/etc/avahi/services``, which avahi-daemon picks up without a reload).

The HTTP surface lives in ``hal0.api.routes.services`` (mounted at
``/api/services``); the read-only health aggregator predating this package
(``hal0.api.routes.services_health``, ``GET /api/services/health``) keeps
its contract and shares probe implementations.
"""

from hal0.services.registry import SERVICES, ServiceDef, service_by_id

__all__ = ["SERVICES", "ServiceDef", "service_by_id"]
