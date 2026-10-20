"""Network-coherence derivation for the installer / answer-file (WS-C).

One ``HAL0_BIND_HOST`` drives BOTH the ``hal0-api`` systemd unit and
``hal0 serve`` (the unit used to hardcode ``0.0.0.0`` while ``serve``
defaulted to ``127.0.0.1`` — two sources that could disagree). From that
same bind + hostname choice we derive:

* ``HAL0_HOSTNAME`` — advertised over mDNS (``services/mdns.py`` reads it)
  and used to build the origin allowlist.
* ``HAL0_ALLOWED_ORIGINS`` — the browser-origin allowlist the API's Origin
  gate (``api/auth._origin_allowed``, via ``api/agents/_auth.allowed_origins``)
  checks. It is
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
import stat
import tomllib
from collections.abc import Iterable, Sequence
from pathlib import Path

# mDNS alias every hal0 box answers to (matches the port-less default the
# WS gate has always shipped and the avahi ``hal0.service`` advertisement).
MDNS_ALIAS = "hal0.local"

# Vite dev server — kept in the allowlist so UI hot-reload (`npm run dev`
# on :5173) still passes the Origin gate during development. This is
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
    config_path: Path | None = None,
) -> dict[str, str]:
    """Resolve the three coherent network env vars from a bind choice.

    Returns ``HAL0_BIND_HOST`` (read by the unit *and* ``hal0 serve``),
    ``HAL0_HOSTNAME`` (mDNS + origin derivation), and
    ``HAL0_ALLOWED_ORIGINS`` (comma-joined WS allowlist). Unset args fall
    back to env vars then the shipped defaults, so an all-default call
    still produces a working, coherent triple.

    The reverse-proxy URL (``public_url`` arg, else ``HAL0_PUBLIC_URL``) is
    validated like ``[network].public_url`` (a bare host means ``https://``);
    an invalid one is dropped rather than written into api.env. When it
    equals the ``[network].public_url`` persisted in ``config_path`` (default:
    the canonical hal0.toml) it is left out of the env list on purpose:
    hal0-api adds the persisted origin at runtime
    (:func:`hal0.api.agents._auth.allowed_origins`), and a copy in the
    managed block would outlive a later ``hal0 network public-url clear``
    until the next installer run (#2382).
    """
    resolved_bind = (bind_host or os.environ.get("HAL0_BIND_HOST", "")).strip() or DEFAULT_BIND_HOST
    resolved_port = _coerce_port(port if port is not None else os.environ.get("HAL0_PORT"))
    resolved_host = resolve_hostname(hostname)
    resolved_public = _validated_public_url(public_url or os.environ.get("HAL0_PUBLIC_URL", ""))
    if resolved_public is not None and resolved_public == persisted_public_url(config_path):
        resolved_public = None
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


def main(argv: Sequence[str] | None = None) -> int:
    """Emit ``KEY=value`` env lines for the installer to append to api.env.

    Invoked by ``install.sh`` via the freshly-installed venv so the whole
    derivation lives in exactly one place. Reads ``HAL0_BIND_HOST`` /
    ``HAL0_HOSTNAME`` / ``HAL0_PORT`` / ``HAL0_PUBLIC_URL`` / ``HAL0_LAN_IPS``
    from the environment. The optional first argument is the hal0.toml path
    (``install.sh`` passes ``sys.argv[1:]``) holding ``[network].public_url``;
    without it the canonical hal0.toml is read.
    """
    args = list(argv or ())
    config_path = Path(args[0]) if args else None
    for key, value in network_env(config_path=config_path).items():
        print(f"{key}={value}")
    return 0


# ── persisted public_url (hal0.toml [network], #2382) ──────────────────────


def _public_url_or_error(value: str | None) -> tuple[str | None, str | None]:
    """Validate an install-time public URL: ``(normalised, None)`` or ``(None, reason)``.

    Accepts the documented bare-host form (``chat.example.com`` means
    ``https://chat.example.com``), then applies the ``[network].public_url``
    rules, so the installer and the config agree on what is valid.
    """
    from hal0.config.schema import NetworkConfig

    raw = (value or "").strip()
    if not raw:
        return None, None
    if "://" not in raw:
        raw = f"https://{raw}"
    try:
        return NetworkConfig(public_url=raw).public_url, None
    except ValueError as exc:
        errors = getattr(exc, "errors", None)
        reason = errors()[0]["msg"] if callable(errors) else str(exc)
        return None, str(reason).removeprefix("Value error, ")


def _validated_public_url(value: str | None) -> str | None:
    return _public_url_or_error(value)[0]


def persisted_public_url(config_path: Path | None = None) -> str | None:
    """Return hal0.toml's ``[network].public_url``, or None.

    Best-effort: a missing or unloadable config reads as unset.
    """
    from hal0.config.loader import load_hal0_config

    try:
        return load_hal0_config(config_path).network.public_url
    except Exception:
        return None


def _read_regular_nofollow(target: Path) -> tuple[str, os.stat_result]:
    """Read ``target`` without following a symlink; return its text and stat."""
    fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise OSError(f"{target} is not a regular file")
        with os.fdopen(fd, "r", encoding="utf-8") as fh:
            fd = -1
            return fh.read(), st
    finally:
        if fd >= 0:
            os.close(fd)


def persist_public_url(url: str, config_path: Path | None = None) -> str:
    """Write ``url`` into hal0.toml ``[network].public_url`` unless one is set.

    Migration for the install-time ``HAL0_PUBLIC_URL`` (#2382): before this,
    the value only reached the managed ``api.env`` block and was lost on the
    next installer run without the env var. Persisting it once makes
    ``hal0.toml`` its home. An existing value is never overwritten.

    Returns a one-line, operator-readable outcome. Never raises: the
    install itself is fine either way. Takes the same ``hal0.toml.lock`` as
    every other writer (the API may be live) and reads the config with
    ``O_NOFOLLOW``. This runs as root while ``/etc/hal0`` is writable by the
    hal0 account, so the write sets owner and mode on the temp file's
    descriptor (``write_toml_atomic(owner=, mode=)``; ``mkstemp`` opens it
    ``O_EXCL | O_NOFOLLOW``) and the rename is the last operation on the
    path: nothing resolves ``hal0.toml`` by name afterwards, so a symlink
    swapped in then never receives a chown or chmod.
    """
    from hal0.config import paths
    from hal0.config.loader import _drop_dead_keys, hal0_config_file_lock, write_toml_atomic
    from hal0.config.schema import Hal0Config

    target = Path(config_path) if config_path is not None else paths.hal0_toml()
    wanted, reason = _public_url_or_error(url)
    if wanted is None:
        if reason is None:
            return "HAL0_PUBLIC_URL is empty; nothing to persist"
        return f"HAL0_PUBLIC_URL not persisted: {reason}"
    try:
        with hal0_config_file_lock(target):
            try:
                text, st = _read_regular_nofollow(target)
            except FileNotFoundError:
                return f"HAL0_PUBLIC_URL not persisted: no {target} yet"
            except OSError as exc:
                return f"HAL0_PUBLIC_URL not persisted: refusing to use {target}: {exc}"
            cfg = Hal0Config.model_validate(_drop_dead_keys(tomllib.loads(text)))
            existing = cfg.network.public_url
            if existing is not None:
                if existing == wanted:
                    return f"[network].public_url already {existing}"
                return (
                    f"[network].public_url is already {existing}; left alone "
                    f"(HAL0_PUBLIC_URL={wanted} was not persisted). "
                    "Change it with: hal0 network public-url set <url>"
                )
            cfg.network.public_url = wanted
            write_toml_atomic(
                target,
                cfg.model_dump(mode="python", exclude_none=True),
                mode=stat.S_IMODE(st.st_mode),
                owner=(st.st_uid, st.st_gid),
            )
    except Exception as exc:
        return f"HAL0_PUBLIC_URL not persisted: {exc}"
    return f"persisted HAL0_PUBLIC_URL as [network].public_url = {wanted} in {target}"


def persist_public_url_main(argv: Sequence[str] | None = None) -> int:
    """``install.sh`` entry point: persist ``HAL0_PUBLIC_URL`` (argv[0] = hal0.toml).

    Prints the one-line outcome on stdout and always exits 0.
    """
    args = list(argv or ())
    url = os.environ.get("HAL0_PUBLIC_URL", "").strip()
    if not url:
        return 0
    print(persist_public_url(url, Path(args[0]) if args else None))
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
    "persist_public_url",
    "persist_public_url_main",
    "persisted_public_url",
    "resolve_hostname",
]
