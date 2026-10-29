"""A read-only, sanitised view of what a Hermes profile can do (ADR-0043 decision 10).

``agent_capabilities`` tells a remote coding agent which toolsets, MCP servers,
skills directories, platforms and model aliases a Hermes profile has right now,
so it can decide what to delegate and what to teach. The view is built from the
profile's ``config.yaml`` and it is the only place the box's agent configuration
leaves it, so it is built by *allow-listing*, not by redacting a copy of the
config:

* only the fields named in :data:`CAPABILITY_KEYS` exist, and each is rebuilt
  from scalars: MCP servers are their **names** (never a URL, header or env),
  model aliases are ``{model, provider}`` (never a ``base_url`` or key);
* then every string that survives is checked once more: a name whose key is
  secret-shaped (:func:`hal0.redaction.is_sensitive_key`) or a string the log
  redactor would change (:func:`hal0.redaction.redact_log_line`: a ``Bearer``
  token, a ``*_KEY=`` value, URL userinfo, a well-known token prefix) is
  dropped, never echoed, and never replaced by a mask.

Each profile reports only what its own ``config.yaml`` sets: the default
profile's file is ``<home>/config.yaml``, a named profile's is
``<home>/profiles/<profile>/config.yaml``. A field the file does not set is
``None``, meaning "Hermes's built-in default". Nothing is borrowed from the
default profile: at the pinned Hermes (v0.22.4, ``4d043038``) a profile's
config is its own ``HERMES_HOME/config.yaml`` (``get_config_path``,
``hermes_cli/config.py:431-433``) merged over Hermes's built-in
``DEFAULT_CONFIG`` (``load_config``, ``:2088-2089``), never over another
profile's file. That is why hal0 writes settings such as the terminal gate
into every profile's file.

``api_server_version`` is read from the API server's ``GET /health/detailed``
(3 s timeout) and is ``None`` when the gateway is down, answers an error or sends
no usable ``version``. The route needs the profile's API key (it answers 401
without it, checked live), so the caller passes ``api_key``, the one hal0 holds for
that profile (the same one ``HermesGateway.key_for`` reads), and it goes out as a
bearer header on that one request, never through a proxy (``trust_env=False``:
a Hermes listening beyond loopback is still not a proxy's to see) and never
into the view. With no key there is no request and the version is ``None``.
Nothing here raises for an unreadable config or an unreachable gateway: the
view is simply emptier.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote

from hal0.agents import hermes_command_approvals
from hal0.agents.hermes_api import DEFAULT_PROFILE, _config, api_server_base
from hal0.agents.hermes_platforms import _explicit_enabled
from hal0.redaction import is_sensitive_key, redact_log_line

__all__ = ["CAPABILITY_KEYS", "FetchJson", "capabilities_view"]

#: The view's keys, in order. The contract with the ``agent_capabilities`` tool.
CAPABILITY_KEYS: tuple[str, ...] = (
    "agent",
    "profile",
    "api_server_version",
    "model_aliases",
    "toolsets_enabled",
    "toolsets_disabled",
    "mcp_servers",
    "skills_dirs",
    "platforms_enabled",
    "approvals_mode",
)

#: Fetches a URL with the given request headers and returns its parsed JSON, or ``None``;
#: may raise. Replaced in tests.
FetchJson = Callable[[str, Mapping[str, str]], Any]

#: A profile name is one URL segment (the same rule as ``HermesGateway.profile_of``).
_PROFILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")

_FETCH_TIMEOUT_S = 3.0
_MAX_TEXT = 200


def capabilities_view(
    home: Path,
    profile: str,
    *,
    fetch_json: FetchJson | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """The sanitised capabilities of ``profile`` under the Hermes ``home``.

    ``profile`` is ``"default"`` or a name under ``<home>/profiles``; anything
    that is not one URL segment raises ``ValueError`` (the service checks the
    name first, this keeps the function safe on its own). ``fetch_json`` replaces
    the ``GET /health/detailed`` request (it may raise or return ``None``);
    ``api_key`` is the profile's API key, sent as its bearer. Without one the
    version is ``None`` and nothing is requested.
    """
    if profile != DEFAULT_PROFILE and _PROFILE_NAME.fullmatch(profile) is None:
        raise ValueError(f"{profile!r} is not a Hermes profile name")
    profile_home = home if profile == DEFAULT_PROFILE else home / "profiles" / profile
    own = _config(profile_home)

    return {
        "agent": "hermes" if profile == DEFAULT_PROFILE else f"hermes/{profile}",
        "profile": profile,
        "api_server_version": _version(home, profile, fetch_json, api_key),
        "model_aliases": _model_aliases(_get(own, "model_aliases")),
        "toolsets_enabled": _strings(_get(own, "toolsets")),
        "toolsets_disabled": _strings(_get(own, "agent", "disabled_toolsets")),
        "mcp_servers": _names(_get(own, "mcp_servers")),
        "skills_dirs": _strings(_get(own, "skills", "external_dirs")),
        "platforms_enabled": _platforms(own),
        "approvals_mode": _text(
            hermes_command_approvals.read_rendered(profile_home / "config.yaml")
        ),
    }


def _get(config: dict[str, Any], *path: str) -> Any:
    """The value at ``path`` in ``config``, or ``None`` when the file does not set it."""
    node: Any = config
    for part in path:
        node = node.get(part) if isinstance(node, dict) else None
    return node


# -- allow-listed projections -------------------------------------------------------------


def _text(value: Any) -> str | None:
    """``value`` when it is a short string the log redactor leaves alone, else ``None``."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned or len(cleaned) > _MAX_TEXT or redact_log_line(cleaned) != cleaned:
        return None
    return cleaned


def _name(value: Any) -> str | None:
    """A name (an MCP server, an alias, a platform): safe text whose own spelling is not secret-shaped."""
    text = _text(value)
    return text if text is not None and not is_sensitive_key(text) else None


def _strings(value: Any) -> list[str] | None:
    """A YAML list of strings, in order, with unsafe entries dropped; ``None`` when unset."""
    if value is None:
        return None
    if not isinstance(value, list):
        return []
    return [text for item in value if (text := _text(item)) is not None]


def _names(value: Any) -> list[str] | None:
    """The sorted names of a YAML mapping's entries (never the entries); ``None`` when unset."""
    if value is None:
        return None
    if not isinstance(value, dict):
        return []
    return sorted({name for key in value if (name := _name(key)) is not None})


def _model_aliases(value: Any) -> dict[str, dict[str, str | None]] | None:
    """``{alias: {model, provider}}``; an alias with a secret-shaped name or value is dropped whole.

    ``None`` when the file sets no ``model_aliases``.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        return {}
    out: dict[str, dict[str, str | None]] = {}
    for key, block in value.items():
        alias = _name(key)
        if alias is None or not isinstance(block, dict):
            continue
        fields = {field: block.get(field) for field in ("model", "provider")}
        if any(raw is not None and _text(raw) is None for raw in fields.values()):
            continue
        out[alias] = {field: _text(raw) for field, raw in fields.items()}
    return dict(sorted(out.items()))


def _platforms(own: dict[str, Any]) -> list[str] | None:
    """Platforms the file sets ``enabled: true``; ``None`` when it sets ``enabled`` for none."""
    enabled = _explicit_enabled(own)
    if not enabled:
        return None
    return sorted({name for key, on in enabled.items() if on and (name := _name(key)) is not None})


# -- the gateway's version ----------------------------------------------------------------


def _version(
    home: Path, profile: str, fetch_json: FetchJson | None, api_key: str | None
) -> str | None:
    """``version`` from ``GET /health/detailed``; ``None`` for no key, any failure or an odd answer."""
    key = (api_key or "").strip()
    if not key:
        return None  # the route answers 401 without the key: do not ask
    base = api_server_base(home)
    path = (
        "/health/detailed"
        if profile == DEFAULT_PROFILE
        else f"/p/{quote(profile, safe='')}/health/detailed"
    )
    fetch = fetch_json if fetch_json is not None else _fetch_json
    try:
        body = fetch(base + path, {"Authorization": f"Bearer {key}"})
    except Exception:  # the gateway being down is a normal state, not an error to report
        return None
    version = body.get("version") if isinstance(body, dict) else None
    return _text(version)


def _fetch_json(url: str, headers: Mapping[str, str]) -> Any:
    """Sync ``GET url`` with ``headers`` and a 3 s timeout; the parsed JSON, or ``None``."""
    import httpx

    try:
        # Never through a proxy: the request carries the profile's API key, and a
        # Hermes that listens beyond loopback is still on this box's network.
        resp = httpx.get(url, headers=dict(headers), timeout=_FETCH_TIMEOUT_S, trust_env=False)
        if resp.status_code != 200:
            return None
        return resp.json()
    except (httpx.HTTPError, OSError, ValueError):
        return None
