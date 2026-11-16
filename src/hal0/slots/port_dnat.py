"""Slot-side owner of the netavark DNAT repair (#1820, #3724).

:mod:`hal0.system.netavark` detects; this module decides what the running
system does about it. Two callers:

* the slot watchdog, at its ``/health`` strike threshold on an active unit
  (:meth:`hal0.slots.watchdog.SlotWatchdog._classify_health_fault`): audit that
  slot's published port before parking it in ERROR;
* :class:`hal0.slots.manager.SlotManager` and the OpenWebUI component converge,
  after hal0 itself recreated a container: re-check every running container's
  published ports, since a recreate is the observed trigger for the others'
  rules vanishing (#3724).

The decision #1820 asked for, made explicitly here: the running system
auto-repairs ONLY the unambiguous missing-rule case, and only with ``podman
network reload --all`` (rebuilds every running container's rules, restarts
nothing), at most once per :data:`hal0.system.netavark.RELOAD_MIN_INTERVAL_S`
box-wide, and only while ``[slots].port_dnat_auto_repair`` is on (default).
Stale rules (duplicate / dead first match) are surfaced as
:data:`REASON_STALE` and never auto-repaired: deleting a firewall rule stays
the operator's ``hal0 doctor ports --fix``.

Every function here is synchronous and never raises; callers run them in an
executor.
"""

from __future__ import annotations

import logging

from hal0.system import netavark
from hal0.system.netavark import PortVerdict, ReloadResult

log = logging.getLogger(__name__)

#: Machine-readable ``error_reason`` on a slot parked in ERROR because its
#: published port has no DNAT rule reaching the container.
REASON_MISSING = "port_dnat_missing"
#: ... because a stale DNAT rule shadows the live one. Surfaced only.
REASON_STALE = "port_dnat_stale"


def _read_snapshot() -> netavark.DnatSnapshot:
    """Indirection the test suite stubs (tests/conftest.py) so no test reads a
    real firewall."""
    return netavark.read_snapshot()


def audit_port(port: int) -> PortVerdict | None:
    """The DNAT verdict for one published port, or ``None`` when unknowable."""
    try:
        snapshot = _read_snapshot()
    except Exception as exc:
        log.info("slot.port_dnat_audit_unavailable", extra={"port": port, "error": str(exc)})
        return None
    for verdict in netavark.audit_snapshot(snapshot, [port]):
        if verdict.port == port:
            return verdict
    return None


def try_reload(trigger: str) -> ReloadResult:
    """One rate-limited ``podman network reload --all``."""
    return netavark.auto_reload(trigger=trigger)


def recheck_after_recreate(trigger: str) -> ReloadResult | None:
    """Reload when any running container's published port lost its rule.

    ``None`` when the audit could not run or found nothing missing.
    """
    try:
        snapshot = _read_snapshot()
    except Exception as exc:
        log.info("slot.port_dnat_recheck_unavailable", extra={"error": str(exc)})
        return None
    missing = [v for v in netavark.audit_snapshot(snapshot) if v.missing]
    if not missing:
        return None
    names = ", ".join(f"{v.owner.name}:{v.port}" for v in missing if v.owner)
    log.warning("slot.port_dnat_missing", extra={"trigger": trigger, "ports": names})
    return netavark.auto_reload(trigger=f"{trigger}; missing {names}")


__all__ = [
    "REASON_MISSING",
    "REASON_STALE",
    "audit_port",
    "recheck_after_recreate",
    "try_reload",
]
