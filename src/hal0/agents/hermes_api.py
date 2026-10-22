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
* The default profile's key is process environment, loaded once at gateway
  start: config ``api_server.key``, else ``$HERMES_HOME/.env`` (which
  ``hermes_cli/env_loader.py:506`` loads with ``override=True``), else the
  environment the unit inherits from hal0's root-only vault.
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


def _append_env_key(target: Path, value: str) -> bool:
    """Append ``API_SERVER_KEY=value`` to ``target`` atomically.

    Other lines stay byte-identical; the file keeps its mode and, as root, its
    owner. A new file is created ``0600`` and, as root, owned like its
    directory, so the gateway (which runs as that owner) can read it. An
    unreadable or unwritable target is skipped with ``hermes_api.key_skipped``
    rather than raised: one bad profile must never abort the sweep.
    """
    try:
        existing = target.read_bytes()
        st: os.stat_result | None = target.stat()
    except FileNotFoundError:
        existing, st = b"", None
    except OSError as exc:  # unreadable / not a file: skip, never abort the sweep
        log.warning("hermes_api.key_skipped", path=str(target), error=str(exc))
        return False
    prefix = existing if not existing or existing.endswith(b"\n") else existing + b"\n"
    line = f"{_KEY_NAME}={value}\n".encode()
    try:
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".env.")
    except OSError as exc:  # dir not writable by this user: skip, never abort the sweep
        log.warning("hermes_api.key_skipped", path=str(target), error=str(exc))
        return False
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(prefix + line)
        os.chmod(tmp, stat.S_IMODE(st.st_mode) if st is not None else 0o600)
        if os.geteuid() == 0:
            owner = st if st is not None else target.parent.stat()
            os.chown(tmp, owner.st_uid, owner.st_gid)
        os.replace(tmp, target)
    except OSError as exc:
        Path(tmp).unlink(missing_ok=True)
        log.warning("hermes_api.key_skipped", path=str(target), error=str(exc))
        return False
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return True


def ensure_profile_key(profile: str, home: Path) -> bool:
    """Append a fresh ``API_SERVER_KEY`` to a named profile lacking a usable one.

    Returns ``True`` when it wrote. Never touches ``default`` (see
    :func:`ensure_default_key`) or an existing usable key.
    """
    if profile == DEFAULT_PROFILE or api_key_for(profile, home) is not None:
        return False
    return _append_env_key(home / "profiles" / profile / ".env", hp._generate_api_server_key())


def ensure_default_key(home: Path, *, value: str | None = None) -> bool:
    """Mirror the gateway's default ``API_SERVER_KEY`` into ``home/.env``.

    Provisioning keeps the default key in hal0's root-only vault
    (``hermes_provision.HERMES_SECRETS_ENV``); the gateway gets it as process
    environment, and hal0-api cannot read it there. Hermes loads ``home/.env``
    over the process environment at gateway start
    (``hermes_cli/env_loader.py:506``, ``override=True``), so the same value in
    that file changes nothing for the gateway and makes the key readable to
    hal0-api. Writes only when neither config ``api_server.key`` nor
    ``home/.env`` holds a usable key. ``value`` is the key the caller just
    minted; otherwise the vault's, which only root can read. Never mints a key
    of its own: a fresh value would desynchronise hal0 from the running
    gateway until its next restart. Returns ``True`` when it wrote.
    """
    if api_key_for(DEFAULT_PROFILE, home) is not None:
        return False
    key = value if value is not None else hp._read_secrets_env().get(_KEY_NAME)
    if not key or not hp._is_strong_api_server_key(key):
        return False
    return _append_env_key(home / ".env", key.strip())


def ensure_profile_keys(home: Path) -> list[str]:
    """Give every profile a key hal0-api can read; returns the profiles written.

    ``default`` (mirrored, see :func:`ensure_default_key`) comes first, then
    each named profile dir that got a fresh key. Idempotent: a profile with a
    usable key is left alone, and a profile whose ``.env`` cannot be read or
    written is skipped (``hermes_api.key_skipped``) rather than aborting the
    sweep.
    """
    written = [DEFAULT_PROFILE] if ensure_default_key(home) else []
    written.extend(name for name in profile_dirs(home) if ensure_profile_key(name, home))
    return written


def default_model_id(home: Path | None = None) -> str:
    """Model id the default profile advertises at ``/v1/models``.

    Hermes feeds ``extra.model_name`` into ``_resolve_model_name`` as the
    explicit override, which wins over ``hermes-agent``
    (``api_server.py:1198-1200``, ``:1458-1468``; precedence in
    ``hermes_cli/model_switch.py:630-641``). Env beats config.yaml
    (``gateway/config.py:840`` "Priority: env > config.yaml"):
    ``_env_extras`` overwrites ``extra["model_name"]`` whenever
    ``API_SERVER_MODEL_NAME`` is truthy (``gateway/config_env.py:141-151``,
    ``:314``), gated on a usable ``API_SERVER_KEY`` (``:308-310``). So: env,
    then config ``gateway.platforms.api_server.model_name``, then
    ``hermes-agent``. The env read is hal0-api's own process env; hal0
    renders no ``API_SERVER_*`` gateway env, so on a managed box config.yaml
    is the source.
    """
    env = os.environ.get("API_SERVER_MODEL_NAME", "").strip()
    if env:
        return env
    block = _api_server_block(home if home is not None else hermes_home())
    configured = block.get("model_name")
    name = configured.strip() if isinstance(configured, str) else ""
    return name or DEFAULT_MODEL_ID


def model_id_for(profile: str, home: Path | None = None) -> str:
    """Model id Hermes advertises for ``profile``.

    The override applies to the default profile only: under a ``/p/<profile>/``
    prefix Hermes calls ``_resolve_model_name("")`` (``api_server.py:2480``),
    so the explicit tier is empty and the profile name wins.
    """
    return default_model_id(home) if profile == DEFAULT_PROFILE else profile


def upstream_name_for(profile: str) -> str:
    """hal0 upstream name: ``hermes`` or ``hermes.<profile>``."""
    return UPSTREAM_PREFIX if profile == DEFAULT_PROFILE else f"{UPSTREAM_PREFIX}.{profile}"


def profile_of_upstream(name: str) -> str | None:
    """Inverse of :func:`upstream_name_for`; ``None`` for non-Hermes names."""
    if name == UPSTREAM_PREFIX:
        return DEFAULT_PROFILE
    head, sep, tail = name.partition(".")
    return tail if head == UPSTREAM_PREFIX and sep and tail else None
