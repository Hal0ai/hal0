"""mDNS / avahi discovery for companion services.

If something else drops ``/etc/avahi/services/hal0.service`` (the main UI
announcement), this module extends that to the *addon* services: one avahi
service-group file per advertised
addon (``hal0-addon-<id>.service``), so LAN clients see distinct
"OpenWebUI on <host>" / "ComfyUI on <host>" entries and each service is
reachable as ``http://<host>.local:<port>`` without DNS.

avahi-daemon inotify-watches ``/etc/avahi/services`` — writing or removing
a file takes effect immediately, no daemon reload and no systemctl call.

Only files matching our ``hal0-addon-*.service`` prefix are ever written or
removed; the installer-owned ``hal0.service`` is never touched.

``HAL0_AVAHI_SERVICES_DIR`` overrides the directory (tests / non-standard
layouts). Everything is fail-soft: a host without avahi reports
``available=False`` and writes simply fail with an honest message.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path
from xml.sax.saxutils import escape

from hal0.services import systemd

_ADDON_PREFIX = "hal0-addon-"
_AVAHI_UNIT = "avahi-daemon.service"


def services_dir() -> Path:
    """The avahi services directory (env-overridable for tests)."""
    override = os.environ.get("HAL0_AVAHI_SERVICES_DIR", "").strip()
    return Path(override) if override else Path("/etc/avahi/services")


def mdns_hostname() -> str:
    """The ``<host>.local`` name this machine answers to via avahi.

    avahi advertises the machine's hostname; ``HAL0_HOSTNAME`` (the install
    wizard's choice) wins when set so the dashboard matches what the
    installer announced.
    """
    host = os.environ.get("HAL0_HOSTNAME", "").strip() or socket.gethostname()
    host = host.removesuffix(".local").strip(".") or "hal0"
    return f"{host}.local"


def _addon_path(service_id: str) -> Path:
    return services_dir() / f"{_ADDON_PREFIX}{service_id}.service"


def advertised_ids() -> list[str]:
    """Service ids currently advertised via our addon files."""
    try:
        files = sorted(services_dir().glob(f"{_ADDON_PREFIX}*.service"))
    except OSError:
        return []
    return [f.stem.removeprefix(_ADDON_PREFIX) for f in files]


async def status() -> dict[str, object]:
    """Discovery status for the dashboard.

    ``available`` is a real signal (avahi-daemon unit active), not a guess
    from binary presence — matches the services_health "no fabricated up"
    rule.
    """
    d = services_dir()
    return {
        "available": await systemd.unit_is_active(_AVAHI_UNIT),
        "hostname": mdns_hostname(),
        "base_advertised": (d / "hal0.service").is_file(),
        "advertised": advertised_ids(),
    }


def _service_group_xml(name: str, port: int) -> str:
    """Render one avahi service-group announcing an HTTP service."""
    safe = escape(name)
    return f"""<?xml version="1.0" standalone='no'?>
<!-- Written by hal0 (services mDNS advertisement) — do not edit by hand.
     Managed via the dashboard Services page / POST /api/services/mdns. -->
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">{safe} on %h</name>
  <service>
    <type>_http._tcp</type>
    <port>{port}</port>
    <txt-record>path=/</txt-record>
    <txt-record>name={safe}</txt-record>
  </service>
</service-group>
"""


def advertise(entries: list[tuple[str, str, int]]) -> dict[str, object]:
    """Write one addon file per (id, name, port); prune stale addon files.

    Atomic per file (tmp + rename in the same directory). Returns
    ``{"ok": bool, "advertised": [...], "message": str | None}``.
    """
    d = services_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"ok": False, "advertised": [], "message": f"cannot create {d}: {exc}"}

    wanted = {sid for sid, _name, _port in entries}
    errors: list[str] = []
    for sid, name, port in entries:
        target = _addon_path(sid)
        tmp = target.with_suffix(".tmp")
        try:
            tmp.write_text(_service_group_xml(name, port), encoding="utf-8")
            tmp.replace(target)
        except OSError as exc:
            errors.append(f"{sid}: {exc}")
    # Prune addon files for services no longer advertised (never touches
    # the installer-owned hal0.service).
    for sid in advertised_ids():
        if sid not in wanted:
            try:
                _addon_path(sid).unlink(missing_ok=True)
            except OSError as exc:
                errors.append(f"{sid}: {exc}")

    return {
        "ok": not errors,
        "advertised": advertised_ids(),
        "message": "; ".join(errors) or None,
    }


def withdraw() -> dict[str, object]:
    """Remove every hal0-addon-*.service file."""
    return advertise([])


__all__ = [
    "advertise",
    "advertised_ids",
    "mdns_hostname",
    "services_dir",
    "status",
    "withdraw",
]
