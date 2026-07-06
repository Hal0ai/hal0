"""Network-coherence derivation for the installer / answer-file (WS-C).

One ``HAL0_BIND_HOST`` drives BOTH the ``hal0-api`` systemd unit and
``hal0 serve`` (the unit used to hardcode ``0.0.0.0`` while ``serve``
defaulted to ``127.0.0.1`` — two sources that could disagree). From that
same bind + hostname choice we derive:

* ``HAL0_HOSTNAME`` — advertised over mDNS (``services/mdns.py`` reads it)
  and used to build the origin allowlist.
* ``HAL0_ALLOWED_ORIGINS`` — the browser-origin allowlist the chat-proxy
  WS gate (``api/agents/_auth.check_ws_origin_and_cookie``) checks. It is
  seeded with the chosen hostname, every detected LAN IP, and localhost —
  all on the API port — so that whatever host the operator typed into
  their browser (and therefore whatever ``GET /api/config/urls``
  advertises) is guaranteed to be in the allowlist. That closes the 4403
  "policy violation" WS-upgrade rejection that happened when the
  advertised URL (e.g. ``http://192.168.1.20:8080``) was not one of the
  three hardcoded defaults.

This module is the single derivation path: the installer (``install.sh``)
calls :func:`main` to seed ``/etc/hal0/api.env``, and the headless
answer-file loader (``hal0.install.answers.load_answers``, WS-C
answer-key wiring) calls :func:`network_env` with ``network.bind_host`` /
``network.hostname`` / ``network.public_url`` from ``hal0-setup.yaml``.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Iterable, Sequence

# mDNS alias every hal0 box answers to (matches the port-less default the
# WS gate has always shipped and the avahi ``hal0.service`` advertisement).
MDNS_ALIAS = "hal0.local"

# Vite dev server — kept in the allowlist so UI hot-reload (`npm run dev`
# on :5173) still upgrades the chat-proxy WS during development. This is
# one of the three historical defaults in ``api/agents/_auth`` and must
# survive the env override (which *replaces* rather than unions).
DEV_ORIGINS: tuple[str, ...] = ("http://localhost:5173",)

DEFAULT_BIND_HOST = "0.0.0.0"
DEFAULT_PORT = 8080


def resolve_hostname(choice: str | None = None) -> str:
    """Return the hostname to advertise (mDNS + origin allowlist).

    Precedence: explicit ``choice`` → ``HAL0_HOSTNAME`` env →
    ``socket.gethostname()``. Mirrors ``services.mdns.mdns_hostname`` so
    the advertised name and the seeded ``HAL0_HOSTNAME`` never diverge.
    """
    resolved = (choice or os.environ.get("HAL0_HOSTNAME", "")).strip()
    return resolved or socket.gethostname()


def detect_lan_ips(override: str | None = None) -> list[str]:
    """Best-effort list of the host's routable LAN IPv4 addresses.

    ``override`` (or the ``HAL0_LAN_IPS`` env var — comma/space
    separated) short-circuits detection; the installer passes
    ``hostname -I`` through it so the box's real addresses are used even
    inside containers where Python's resolver is unreliable.

    Detection uses the connect-a-UDP-socket trick to learn the primary
    outbound interface IP (no packets are actually sent) and augments it
    with ``getaddrinfo`` on the hostname. Loopback (``127.*``) is dropped
    — localhost origins are added separately. All failures degrade to an
    empty list rather than raising.
    """
    raw = override if override is not None else os.environ.get("HAL0_LAN_IPS", "")
    raw = (raw or "").strip()
    if raw:
        candidates = [ip.strip() for ip in raw.replace(",", " ").split() if ip.strip()]
        return _dedupe(ip for ip in candidates if _is_lan_ipv4(ip))

    ips: list[str] = []
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # TEST-NET-1 (RFC 5737) — routable-looking but never real, so the
        # kernel picks our egress interface without any traffic leaving.
        sock.connect(("192.0.2.1", 9))
        ips.append(sock.getsockname()[0])
    except OSError:
        pass
    finally:
        sock.close()

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, family=socket.AF_INET):
            ips.append(info[4][0])
    except OSError:
        pass

    return _dedupe(ip for ip in ips if _is_lan_ipv4(ip))


def derive_allowed_origins(
    hostname: str,
    port: int,
    *,
    public_url: str | None = None,
    lan_ips: Sequence[str] | None = None,
) -> list[str]:
    """Build the WS-origin allowlist for the given bind/hostname choice.

    The list covers every way a browser can legitimately reach the
    dashboard on ``port``: localhost, each LAN IP, the chosen hostname
    (plus its ``.local`` mDNS form), and the ``hal0.local`` alias — so
    the URL ``GET /api/config/urls`` advertises always matches. When the
    operator declared a reverse-proxy ``public_url`` its scheme://host
    origin is added too (the proxy terminates TLS on 443, so it carries
    no port). Dev origins (Vite) are always appended.
    """
    port_hosts: list[str] = ["localhost", "127.0.0.1"]
    port_hosts.extend(lan_ips or [])
    hostname = (hostname or "").strip()
    if hostname and hostname not in ("localhost", "127.0.0.1"):
        port_hosts.append(hostname)
        if "." not in hostname:
            port_hosts.append(f"{hostname}.local")
    port_hosts.append(MDNS_ALIAS)

    origins: list[str] = [f"http://{host}:{port}" for host in port_hosts]
    # Port-less mDNS alias — the historical default and what a browser
    # sends when hal0.local is reached on the default HTTP port.
    origins.append(f"http://{MDNS_ALIAS}")
    origins.extend(DEV_ORIGINS)

    proxy_origin = _origin_of(public_url)
    if proxy_origin:
        origins.append(proxy_origin)

    return _dedupe(origins)


def network_env(
    *,
    bind_host: str | None = None,
    hostname: str | None = None,
    public_url: str | None = None,
    port: int | None = None,
    lan_ips: Sequence[str] | None = None,
) -> dict[str, str]:
    """Resolve the three coherent network env vars from a bind choice.

    Returns ``HAL0_BIND_HOST`` (read by the unit *and* ``hal0 serve``),
    ``HAL0_HOSTNAME`` (mDNS + origin derivation), and
    ``HAL0_ALLOWED_ORIGINS`` (comma-joined WS allowlist). Unset args fall
    back to env vars then the shipped defaults, so an all-default call
    still produces a working, coherent triple.
    """
    resolved_bind = (bind_host or os.environ.get("HAL0_BIND_HOST", "")).strip() or DEFAULT_BIND_HOST
    resolved_port = _coerce_port(port if port is not None else os.environ.get("HAL0_PORT"))
    resolved_host = resolve_hostname(hostname)
    resolved_public = (public_url or os.environ.get("HAL0_PUBLIC_URL", "")).strip() or None
    ips = list(lan_ips) if lan_ips is not None else detect_lan_ips()

    origins = derive_allowed_origins(
        resolved_host,
        resolved_port,
        public_url=resolved_public,
        lan_ips=ips,
    )
    return {
        "HAL0_BIND_HOST": resolved_bind,
        "HAL0_HOSTNAME": resolved_host,
        "HAL0_ALLOWED_ORIGINS": ",".join(origins),
    }


def main() -> int:
    """Emit ``KEY=value`` env lines for the installer to append to api.env.

    Invoked by ``install.sh`` via the freshly-installed venv so the whole
    derivation lives in exactly one place. Reads ``HAL0_BIND_HOST`` /
    ``HAL0_HOSTNAME`` / ``HAL0_PORT`` / ``HAL0_PUBLIC_URL`` / ``HAL0_LAN_IPS``
    from the environment.
    """
    for key, value in network_env().items():
        print(f"{key}={value}")
    return 0


# ── internals ──────────────────────────────────────────────────────────────


def _coerce_port(value: object) -> int:
    """Parse a port from an int/str, falling back to the default."""
    if value is None:
        return DEFAULT_PORT
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return DEFAULT_PORT


def _is_lan_ipv4(ip: str) -> bool:
    """True for a plausible non-loopback IPv4 dotted-quad."""
    if not ip or ip.startswith("127.") or ":" in ip:
        return False
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    return all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)


def _origin_of(url: str | None) -> str | None:
    """Return the ``scheme://host[:port]`` origin of a URL, or None.

    Tolerant of a bare host (assumes https) and trailing paths so an
    operator's ``public_url: chat.example.com`` or
    ``https://hal0.example.com/`` both resolve to a clean origin.
    """
    if not url:
        return None
    url = url.strip().rstrip("/")
    if not url:
        return None
    if "://" not in url:
        url = f"https://{url}"
    scheme, _, rest = url.partition("://")
    host = rest.split("/", 1)[0]
    if not host:
        return None
    return f"{scheme}://{host}"


def _dedupe(items: Iterable[str]) -> list[str]:
    """Order-preserving de-duplication."""
    return list(dict.fromkeys(items))


__all__ = [
    "DEFAULT_BIND_HOST",
    "DEFAULT_PORT",
    "DEV_ORIGINS",
    "MDNS_ALIAS",
    "derive_allowed_origins",
    "detect_lan_ips",
    "main",
    "network_env",
    "resolve_hostname",
]
