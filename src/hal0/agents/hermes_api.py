"""Discover the Hermes API server's profiles, endpoint and per-profile keys.

Pure read-side helpers (plus one idempotent key writer) over Hermes's own
truth: its ``/p/<profile>/health`` probes, ``config.yaml`` and ``.env`` files.
Verified against Hermes 0.22.4 (commit ``4d043038``; first written at 0.22.2,
``d923a5eb``, and every cited construct re-read at 0.22.4):

* The API server binds ``127.0.0.1:8642`` by default; host/port resolve from
  config ``gateway.platforms.api_server.{host,port}``, then env
  ``API_SERVER_HOST`` / ``API_SERVER_PORT``, then the defaults
  (``gateway/platforms/api_server.py:219-233``, ``:1124``). "Env" is the
  gateway's, which ``hermes_cli/env_loader.py:516`` fills from
  ``$HERMES_HOME/.env`` with ``override=True``; hal0 reads that file, never
  hal0-api's own process environment.
* The gateway serves one ``/p/<profile>/`` prefix per profile returned by
  ``profiles_to_serve`` (``hermes_cli/profiles.py:1136``). Under it the
  advertised model id is the profile name; the default profile advertises
  ``hermes-agent`` (``_resolve_model_name``, ``api_server.py:1386-1396``,
  ``:2417-2422``).
* Named profiles fail closed: each needs its own ``API_SERVER_KEY`` in
  ``profiles/<p>/.env``; placeholder or <16-char keys count as unusable
  (``_expected_api_key``, ``api_server.py:1463-1477``;
  ``hermes_cli/auth.py:307-314``).
* ``_load_profile_secret_scope`` re-reads ``.env`` for every request
  (``gateway/run.py:1787-1797``), so a key written here needs no gateway
  restart.
* The default profile's key is process environment, loaded once at gateway
  start: config ``api_server.key``, else ``$HERMES_HOME/.env`` (which
  ``hermes_cli/env_loader.py:516`` loads with ``override=True``), else the
  environment the unit inherits from hal0's root-only vault.
* Dotenv semantics mirror ``agent/secret_scope.py:271-304``.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog

from hal0.agents import hermes_provision as hp
from hal0.config.locking import file_lock
from hal0.security import nofollow

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


def gateway_env(home: Path, name: str) -> str | None:
    """``name`` as the gateway sees it: from ``home/.env`` (Hermes's own env file)."""
    return _dotenv_get(home / ".env", name)


def api_server_endpoint(home: Path | None = None) -> tuple[str, int]:
    """``(host, port)``: config, then ``API_SERVER_HOST/PORT`` in ``.env``, then defaults."""
    root = home if home is not None else hermes_home()
    block = _api_server_block(root)
    host = str(block.get("host") or "").strip()
    host = host or (gateway_env(root, "API_SERVER_HOST") or "").strip() or DEFAULT_HOST
    port = _as_port(block.get("port")) or _as_port(gateway_env(root, "API_SERVER_PORT"))
    return host, port or DEFAULT_PORT


def authority(host: str, port: int) -> str:
    """``host:port`` for a URL; an IPv6 literal (``::1``) is bracketed."""
    return f"[{host}]:{port}" if ":" in host and not host.startswith("[") else f"{host}:{port}"


def api_server_base(home: Path) -> str:
    """``http://<authority>`` of the API server, IPv6-safe."""
    return "http://" + authority(*api_server_endpoint(home))


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
    return check(f"{api_server_base(home)}/health") == 200


def profile_health(home: Path, profile: str, *, probe: Callable[[str], int] | None = None) -> int:
    """Status of ``/p/<profile>/health`` (``0`` on a transport error)."""
    check = probe if probe is not None else probe_status
    return check(f"{api_server_base(home)}/p/{profile}/health")


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


def _strip_inline_comment(value: str) -> str:
    """Hermes's rule (``agent/secret_scope.py:271-292``): a quoted value scans
    to its closing quote (backslash-aware for double quotes) and drops a
    trailing ``# ...``; an unquoted one is cut at a ``#`` preceded by whitespace."""
    value = value.strip()
    if not value:
        return value
    quote = value[0]
    if quote in ("'", '"'):
        i = 1
        while i < len(value):
            ch = value[i]
            if quote == '"' and ch == "\\":
                i += 2
                continue
            if ch == quote:
                return value[: i + 1] if value[i + 1 :].lstrip().startswith("#") else value
            i += 1
        return value
    return re.split(r"\s+#", value, maxsplit=1)[0].strip()


def _parse_env_value(raw: str) -> str:
    """Hermes's rule (``agent/secret_scope.py:294-304``): bare, ``'single'`` or
    ``"double"`` with ``\\"`` / ``\\\\`` escapes."""
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        quoted = value[1:-1]
        parsed: list[str] = []
        i = 0
        while i < len(quoted):
            escaped = quoted[i] == "\\" and quoted[i + 1 : i + 2] in ('"', "\\")
            parsed.append(quoted[i + 1] if escaped else quoted[i])
            i += 2 if escaped else 1
        return "".join(parsed)
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1]
    return value


def _unquote(raw: str) -> str:
    return _parse_env_value(_strip_inline_comment(raw))


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
    # Through hal0.security.nofollow (#2975): this runs as root from the
    # provisioning prelude (agent_commands._mirror_default_key_as_root), and
    # the hal0 account can swap .hermes for a link. Every step goes by
    # descriptor and refuses a link, so the vault's key never lands elsewhere.
    try:
        existing = nofollow.read_bytes(target)
    except OSError as exc:  # unreadable / not a file / a link: skip, never abort the sweep
        log.warning("hermes_api.key_skipped", path=str(target), error=str(exc))
        return False
    prefix = existing if not existing or existing.endswith(b"\n") else existing + b"\n"
    line = f"{_KEY_NAME}={value}\n".encode()
    try:
        if existing is None:
            # New file: 0600, owned like its directory (as root), so the gateway
            # running as that owner can read it.
            owner = nofollow.dir_owner(target.parent) if os.geteuid() == 0 else None
            nofollow.write_file(target, line, mode=0o600, owner=owner, create_dirs=False)
        else:
            # Keeps the replaced file's mode and, as root, its owner.
            nofollow.write_file(target, prefix + line, create_dirs=False)
    except OSError as exc:  # dir not writable by this user, or unsafe: skip
        log.warning("hermes_api.key_skipped", path=str(target), error=str(exc))
        return False
    return True


def ensure_profile_key(profile: str, home: Path) -> bool:
    """Append a fresh ``API_SERVER_KEY`` to a named profile lacking a usable one.

    Returns ``True`` when it wrote. Never touches ``default`` (see
    :func:`ensure_default_key`) or an existing usable key.
    """
    if profile == DEFAULT_PROFILE or api_key_for(profile, home) is not None:
        return False
    target = home / "profiles" / profile / ".env"
    try:
        # Re-check under the cross-process lock: hal0-api's periodic sweep and
        # a CLI provision may run at once, and both must not mint a key.
        with file_lock(target):
            if api_key_for(profile, home) is not None:
                return False
            return _append_env_key(target, hp._generate_api_server_key())
    except OSError as exc:  # lock file not creatable (dir not writable): skip
        log.warning("hermes_api.key_skipped", path=str(target), error=str(exc))
        return False


def ensure_default_key(home: Path, *, value: str | None = None) -> bool:
    """Mirror the gateway's default ``API_SERVER_KEY`` into ``home/.env``.

    Provisioning keeps the default key in hal0's root-only vault
    (``hermes_provision.HERMES_SECRETS_ENV``); the gateway gets it as process
    environment, and hal0-api cannot read it there. Hermes loads ``home/.env``
    over the process environment at gateway start
    (``hermes_cli/env_loader.py:516``, ``override=True``), so the same value in
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
    target = home / ".env"
    try:
        with file_lock(target):
            if api_key_for(DEFAULT_PROFILE, home) is not None:
                return False
            return _append_env_key(target, key.strip())
    except OSError as exc:
        log.warning("hermes_api.key_skipped", path=str(target), error=str(exc))
        return False


def ensure_profile_mcp(home: Path) -> dict[str, Any]:
    """Wire hal0's MCP servers into every named profile (``hermes_provision``)."""
    return hp.wire_named_profiles(home)


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
    (``api_server.py:1126-1128``, ``:1386-1396``; precedence in
    ``hermes_cli/model_switch.py:638-649``). Env beats config.yaml
    (``gateway/config.py:826`` "Priority: env > config.yaml"):
    ``env_extras`` overwrites ``extra["model_name"]`` whenever
    ``API_SERVER_MODEL_NAME`` is truthy (``gateway/platform_env.py:85-100``,
    called from ``gateway/config_env.py:120``), gated on a usable
    ``API_SERVER_KEY`` (``config_env.py:114-116``). So: env,
    then config ``gateway.platforms.api_server.model_name``, then
    ``hermes-agent``. "Env" is the gateway's, read here from
    ``$HERMES_HOME/.env`` (:func:`gateway_env`); hal0 renders no
    ``API_SERVER_*`` into the gateway's unit environment.
    """
    root = home if home is not None else hermes_home()
    env = (gateway_env(root, "API_SERVER_MODEL_NAME") or "").strip()
    if env:
        return env
    block = _api_server_block(root)
    configured = block.get("model_name")
    name = configured.strip() if isinstance(configured, str) else ""
    return name or DEFAULT_MODEL_ID


def model_id_for(profile: str, home: Path | None = None) -> str:
    """Model id Hermes advertises for ``profile``.

    The override applies to the default profile only: under a ``/p/<profile>/``
    prefix Hermes calls ``_resolve_model_name("")`` (``api_server.py:2422``),
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
