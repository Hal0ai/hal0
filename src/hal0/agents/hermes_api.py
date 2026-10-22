"""Discover the Hermes API server's profiles, endpoint and per-profile keys.

Pure read-side helpers (plus one idempotent key writer) over Hermes's own
truth: its ``/p/<profile>/health`` probes, ``config.yaml`` and ``.env`` files.
Verified against Hermes 0.22.2 (commit ``d923a5eb``):

* The API server binds ``127.0.0.1:8642`` by default; host/port resolve from
  config ``gateway.platforms.api_server.{host,port}``, then env
  ``API_SERVER_HOST`` / ``API_SERVER_PORT``, then the defaults
  (``gateway/platforms/api_server.py:216-230``, ``:1196``).
* The gateway serves one ``/p/<profile>/`` prefix per profile returned by
  ``profiles_to_serve`` (``hermes_cli/profiles.py:1135``). Under it the
  advertised model id is the profile name; the default profile advertises
  ``hermes-agent`` (``_resolve_model_name``, ``api_server.py:1458-1468``,
  ``:2475-2480``).
* Named profiles fail closed: each needs its own ``API_SERVER_KEY`` in
  ``profiles/<p>/.env``; placeholder or <16-char keys count as unusable
  (``_expected_api_key``, ``api_server.py:1535-1549``;
  ``hermes_cli/auth.py:307-314``).
* ``_load_profile_secret_scope`` re-reads ``.env`` for every request
  (``gateway/run.py:1772-1782``), so a key written here needs no gateway
  restart.
* Dotenv semantics mirror ``agent/secret_scope.py:257-290``.
"""

from __future__ import annotations

import os
import re
import stat
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog

from hal0.agents import hermes_provision as hp

log = structlog.get_logger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8642
DEFAULT_PROFILE = "default"
DEFAULT_MODEL_ID = "hermes-agent"
UPSTREAM_PREFIX = "hermes"

_KEY_NAME = "API_SERVER_KEY"
_MIN_KEY_LENGTH = 16
_UNUSABLE_KEYS = frozenset({"", "changeme", "placeholder", "your-api-key", "your_api_key", "xxx"})


def hermes_home() -> Path:
    """``$HERMES_HOME`` when set and non-empty, else the provisioner default."""
    raw = os.environ.get("HERMES_HOME", "").strip()
    return Path(raw) if raw else Path(hp.HERMES_HOME_DEFAULT)


def _config(home: Path) -> dict[str, Any]:
    try:
        text = (home / "config.yaml").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    return hp._parse_yaml_config(text) or {}


def _api_server_block(home: Path) -> dict[str, Any]:
    node: Any = _config(home)
    for part in ("gateway", "platforms", "api_server"):
        node = node.get(part) if isinstance(node, dict) else None
    return node if isinstance(node, dict) else {}


def _as_port(value: Any) -> int | None:
    try:
        port = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return port if 0 < port < 65536 else None


def api_server_endpoint(home: Path | None = None) -> tuple[str, int]:
    """``(host, port)``: config, then ``API_SERVER_HOST/PORT`` env, then defaults."""
    block = _api_server_block(home if home is not None else hermes_home())
    host = str(block.get("host") or "").strip()
    host = host or os.environ.get("API_SERVER_HOST", "").strip() or DEFAULT_HOST
    port = _as_port(block.get("port")) or _as_port(os.environ.get("API_SERVER_PORT"))
    return host, port or DEFAULT_PORT


def profile_dirs(home: Path) -> list[str]:
    """Sorted names of the directories under ``home/profiles``; ``[]`` when absent."""
    try:
        return sorted(p.name for p in (home / "profiles").iterdir() if p.is_dir())
    except OSError:
        return []


def probe_status(url: str, *, timeout: float = 3.0) -> int:
    """HTTP status of ``GET url``, including 4xx/5xx; ``0`` only on a transport error.

    Unlike ``hermes_provision._http_get`` (0 for any failure, 404 included),
    this lets callers tell "the gateway is down" from "this profile is not
    served". A loopback target ignores the proxy environment.
    """
    import httpx

    from hal0.service_identity import is_loopback_url

    try:
        resp = httpx.get(url, timeout=timeout, trust_env=not is_loopback_url(url))
    except (httpx.HTTPError, OSError):
        return 0
    return int(resp.status_code)


def gateway_alive(home: Path, *, probe: Callable[[str], int] | None = None) -> bool:
    """Whether the API server's default ``/health`` answers exactly 200."""
    check = probe if probe is not None else probe_status
    host, port = api_server_endpoint(home)
    return check(f"http://{host}:{port}/health") == 200


def profile_health(home: Path, profile: str, *, probe: Callable[[str], int] | None = None) -> int:
    """Status of ``/p/<profile>/health`` (``0`` on a transport error)."""
    check = probe if probe is not None else probe_status
    host, port = api_server_endpoint(home)
    return check(f"http://{host}:{port}/p/{profile}/health")


def served_profiles(home: Path, *, probe: Callable[[str], int] | None = None) -> list[str]:
    """``["default"]`` plus each profile dir whose ``/p/<name>/health`` answers 200."""
    served = [DEFAULT_PROFILE]
    for name in profile_dirs(home):
        if profile_health(home, name, probe=probe) == 200:
            served.append(name)
    return served


def env_key_name(profile: str) -> str:
    """Env var carrying ``profile``'s key: ``HAL0_HERMES_API_KEY__<PROFILE>``."""
    return "HAL0_HERMES_API_KEY__" + re.sub(r"[^A-Za-z0-9]", "_", profile).upper()


def _usable(value: str | None) -> bool:
    if value is None:
        return False
    v = value.strip()
    return len(v) >= _MIN_KEY_LENGTH and v.lower() not in _UNUSABLE_KEYS


def _unquote(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    # Unquoted: an inline comment starts at whitespace + '#'.
    match = re.search(r"\s#", value)
    return value[: match.start()].strip() if match else value


def _dotenv_get(path: Path, name: str) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    found: str | None = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("export "):
            s = s[len("export ") :].strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, value = s.split("=", 1)
        if key.strip() == name:
            found = _unquote(value)
    return found


def api_key_for(profile: str, home: Path) -> str | None:
    """The usable ``API_SERVER_KEY`` for ``profile``, or ``None``.

    ``default``: config ``gateway.platforms.api_server.key`` when usable, else
    ``home/.env``. Named profiles: ``home/profiles/<p>/.env`` only.
    """
    if profile == DEFAULT_PROFILE:
        cfg = _api_server_block(home).get("key")
        if isinstance(cfg, str) and _usable(cfg):
            return cfg.strip()
        value = _dotenv_get(home / ".env", _KEY_NAME)
    else:
        value = _dotenv_get(home / "profiles" / profile / ".env", _KEY_NAME)
    return value.strip() if _usable(value) and value is not None else None


def ensure_profile_key(profile: str, home: Path) -> bool:
    """Append a fresh ``API_SERVER_KEY`` to a named profile lacking a usable one.

    Returns ``True`` when it wrote. Never touches ``default`` or an existing
    usable key. The file is replaced atomically (temp file + ``os.replace``),
    keeping its mode and, as root, its owner; other lines stay byte-identical.
    A new file is created ``0600``.
    """
    if profile == DEFAULT_PROFILE or api_key_for(profile, home) is not None:
        return False
    target = home / "profiles" / profile / ".env"
    try:
        existing = target.read_bytes()
        st: os.stat_result | None = target.stat()
    except FileNotFoundError:
        existing, st = b"", None
    except OSError as exc:  # unreadable / not a file: skip, never abort the sweep
        log.warning("hermes_api.key_skipped", path=str(target), error=str(exc))
        return False
    prefix = existing if not existing or existing.endswith(b"\n") else existing + b"\n"
    line = f"{_KEY_NAME}={hp._generate_api_server_key()}\n".encode()
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".env.")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(prefix + line)
        os.chmod(tmp, stat.S_IMODE(st.st_mode) if st is not None else 0o600)
        if st is not None and os.geteuid() == 0:
            os.chown(tmp, st.st_uid, st.st_gid)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return True


def ensure_profile_keys(home: Path) -> list[str]:
    """:func:`ensure_profile_key` for every profile dir; the names written.

    Every directory, not only served ones: a parked profile that later wakes
    already has its key.
    """
    return [name for name in profile_dirs(home) if ensure_profile_key(name, home)]


def model_id_for(profile: str) -> str:
    """Model id Hermes advertises for ``profile``."""
    return DEFAULT_MODEL_ID if profile == DEFAULT_PROFILE else profile


def upstream_name_for(profile: str) -> str:
    """hal0 upstream name: ``hermes`` or ``hermes.<profile>``."""
    return UPSTREAM_PREFIX if profile == DEFAULT_PROFILE else f"{UPSTREAM_PREFIX}.{profile}"


def profile_of_upstream(name: str) -> str | None:
    """Inverse of :func:`upstream_name_for`; ``None`` for non-Hermes names."""
    if name == UPSTREAM_PREFIX:
        return DEFAULT_PROFILE
    head, sep, tail = name.partition(".")
    return tail if head == UPSTREAM_PREFIX and sep and tail else None
