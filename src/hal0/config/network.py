"""Network-shape resolution — the single source both the systemd unit and
``hal0 serve`` read from (#1099, installer-setup WS-C, decisions Q3/Q17).

Before this module, ``hal0-api.service``'s ``ExecStart`` baked in a
hardcoded ``--host 0.0.0.0`` at install time while ``hal0 serve`` (run
directly, e.g. in dev) defaulted to ``127.0.0.1`` — the two could disagree
about how far the API is reachable. Nothing derived
``HAL0_ALLOWED_ORIGINS``/``HAL0_HOSTNAME`` from that choice either, so a
LAN-reachable install with an unseeded allowlist would advertise
``http://<lan-ip>:8080`` on the dashboard while the WS origin gate
(``hal0.api.agents._auth.allowed_origins``) still only recognised the
loopback/dev defaults — the WS-4403 mismatch the issue closes.

``HAL0_BIND_HOST`` is now the one env var both sides read:

- The unit's ``EnvironmentFile=/etc/hal0/api.env`` sets it; ``ExecStart``
  substitutes it into ``--host ${HAL0_BIND_HOST}`` at service-start time.
- ``hal0 serve`` (``src/hal0/cli/main.py``) reads the same var as its
  Typer ``envvar=`` default, so a bare ``hal0 serve`` (no ``--host`` flag)
  run outside systemd resolves the exact same value.

Everything else in this module derives from that one value: the canonical
hostname (mDNS-style, matching ``services/mdns.py``) and the WS/CORS
origin allowlist (loopback + LAN IPs + hostname), so ``GET
/api/config/urls`` (``api/routes/config.py``), the WS origin gate, and
mDNS/avahi (``services/mdns.py``) all agree on the same network shape.
"""

from __future__ import annotations

import os
import socket

_DEFAULT_BIND_HOST = "127.0.0.1"
_DEFAULT_API_PORT = 8080
_LOOPBACK_BIND_HOSTS = ("127.0.0.1", "localhost", "::1")
_WILDCARD_BIND_HOSTS = ("0.0.0.0", "::")


def bind_host() -> str:
    """The canonical bind host — read by BOTH the unit and ``hal0 serve``.

    Defaults to loopback-only (``127.0.0.1``) when unset, matching
    ``hal0 serve``'s historical default; the installer seeds an explicit
    LAN-reachable value (``0.0.0.0`` by default) into ``/etc/hal0/api.env``.
    """
    raw = os.environ.get("HAL0_BIND_HOST", "").strip()
    return raw or _DEFAULT_BIND_HOST


def hostname() -> str:
    """The canonical operator-facing hostname (bare, no ``.local`` suffix).

    ``HAL0_HOSTNAME`` (the install wizard's choice) wins when set — same
    precedence ``services/mdns.py::mdns_hostname`` already uses — so the
    dashboard's advertised URL, mDNS, and the derived origin allowlist
    never disagree about the machine's name.
    """
    raw = os.environ.get("HAL0_HOSTNAME", "").strip() or socket.gethostname()
    return raw.removesuffix(".local").strip(".") or "hal0"


def _api_port() -> int:
    raw = os.environ.get("HAL0_PORT", "").strip()
    if not raw:
        return _DEFAULT_API_PORT
    try:
        return int(raw)
    except ValueError:
        return _DEFAULT_API_PORT


def detect_lan_ips() -> list[str]:
    """Best-effort enumeration of this host's non-loopback IPv4 addresses.

    Tries ``psutil`` (already a hal0 dependency, see
    ``api/routes/hardware.py``) for a full multi-interface scan first; a
    box that binds a single expected LAN nic won't need it, so a UDP
    "connect" trick (never sends a packet, just resolves routing) is the
    fallback for the common single-NIC case, or when psutil is
    unavailable. Any failure yields an empty list — the allowlist derived
    from it still contains loopback + hostname either way.
    """
    ips: set[str] = set()
    try:
        import psutil

        for addrs in psutil.net_if_addrs().values():
            for addr in addrs:
                if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                    ips.add(addr.address)
    except Exception:
        pass
    if not ips:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("8.8.8.8", 80))
                ip = sock.getsockname()[0]
            if ip and not ip.startswith("127."):
                ips.add(ip)
        except OSError:
            pass
    return sorted(ips)


def derive_allowed_origins(port: int | None = None) -> tuple[str, ...]:
    """Derive the WS/CORS origin allowlist from the bind host + hostname.

    Always includes loopback (``localhost``/``127.0.0.1``) and the mDNS
    hostname — dev access and the standard LAN URL always work. LAN IPs
    (and the bind host itself, if it's a concrete address rather than a
    wildcard) are only added when the API is actually bound to something
    other than loopback, so the allowlist's reach matches how far the
    service is actually reachable rather than over- or under-shooting it.
    """
    p = port if port is not None else _api_port()
    host = hostname()
    origins = {
        f"http://localhost:{p}",
        f"http://127.0.0.1:{p}",
        f"http://{host}.local:{p}",
    }
    bh = bind_host()
    if bh not in _LOOPBACK_BIND_HOSTS:
        for ip in detect_lan_ips():
            origins.add(f"http://{ip}:{p}")
        if bh not in _WILDCARD_BIND_HOSTS:
            origins.add(f"http://{bh}:{p}")
    return tuple(sorted(origins))


__all__ = [
    "bind_host",
    "derive_allowed_origins",
    "detect_lan_ips",
    "hostname",
]
