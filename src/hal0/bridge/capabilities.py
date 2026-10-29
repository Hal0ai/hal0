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
  secret-shaped (:func:`hal0.api._redact.is_sensitive_key`) or a string the log
  redactor would change (:func:`hal0.redaction.redact_log_line`: a ``Bearer``
  token, a ``*_KEY=`` value, URL userinfo, a well-known token prefix) is
  dropped, never echoed, and never replaced by a mask.

A named profile's ``config.yaml`` sits at ``<home>/profiles/<profile>/``; a
field it does not set falls back to the default profile's, which is how Hermes
profiles that hal0 clones from the default behave.

``api_server_version`` is read from the API server's ``GET /health/detailed``
(3 s timeout, no credentials sent) and is ``None`` when the gateway is down,
answers an error or sends no usable ``version``. Nothing here raises for an
unreadable config or an unreachable gateway: the view is simply emptier.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

from hal0.agents import hermes_command_approvals
from hal0.agents.hermes_api import DEFAULT_PROFILE, _config, api_server_base
from hal0.agents.hermes_platforms import _explicit_enabled
from hal0.api._redact import is_sensitive_key
from hal0.redaction import redact_log_line

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

#: Fetches a URL and returns its parsed JSON, or ``None``; may raise. Replaced in tests.
FetchJson = Callable[[str], Any]

#: A profile name is one URL segment (the same rule as ``HermesGateway.profile_of``).
_PROFILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")

_FETCH_TIMEOUT_S = 3.0
_MAX_TEXT = 200


def capabilities_view(
    home: Path, profile: str, *, fetch_json: FetchJson | None = None
) -> dict[str, Any]:
    """The sanitised capabilities of ``profile`` under the Hermes ``home``.

    ``profile`` is ``"default"`` or a name under ``<home>/profiles``; anything
    that is not one URL segment raises ``ValueError`` (the service checks the
    name first, this keeps the function safe on its own). ``fetch_json`` replaces
    the ``GET /health/detailed`` request (it may raise or return ``None``).
    """
    if profile != DEFAULT_PROFILE and _PROFILE_NAME.fullmatch(profile) is None:
        raise ValueError(f"{profile!r} is not a Hermes profile name")
    own = _config(home) if profile == DEFAULT_PROFILE else _config(home / "profiles" / profile)
    base = _config(home)
    config = _Layers(own, base)

    return {
        "agent": "hermes" if profile == DEFAULT_PROFILE else f"hermes/{profile}",
        "profile": profile,
        "api_server_version": _version(home, profile, fetch_json),
        "model_aliases": _model_aliases(config.get("model_aliases")),
        "toolsets_enabled": _strings(config.get("toolsets")),
        "toolsets_disabled": _strings(config.get("agent", "disabled_toolsets")),
        "mcp_servers": _names(config.get("mcp_servers")),
        "skills_dirs": _strings(config.get("skills", "external_dirs")),
        "platforms_enabled": _platforms(own, base),
        "approvals_mode": _approvals_mode(home, profile),
    }


class _Layers:
    """A profile's config over the default profile's: a key the profile lacks reads from the default."""

    def __init__(self, own: dict[str, Any], base: dict[str, Any]) -> None:
        self._layers = (own, base)

    def get(self, *path: str) -> Any:
        for layer in self._layers:
            node: Any = layer
            for part in path:
                node = node.get(part) if isinstance(node, dict) else None
            if node is not None:
                return node
        return None


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


def _strings(value: Any) -> list[str]:
    """A YAML list of strings, in order, with unsafe entries dropped."""
    if not isinstance(value, list):
        return []
    return [text for item in value if (text := _text(item)) is not None]


def _names(value: Any) -> list[str]:
    """The sorted names of a YAML mapping's entries; the entries themselves are never read."""
    if not isinstance(value, dict):
        return []
    return sorted({name for key in value if (name := _name(key)) is not None})


def _model_aliases(value: Any) -> dict[str, dict[str, str | None]]:
    """``{alias: {model, provider}}``; an alias with a secret-shaped name or value is dropped whole."""
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


def _platforms(own: dict[str, Any], base: dict[str, Any]) -> list[str]:
    """Platforms with ``enabled: true``; the profile's explicit setting wins over the default's."""
    enabled = {**_explicit_enabled(base), **_explicit_enabled(own)}
    return sorted({name for key, on in enabled.items() if on and (name := _name(key)) is not None})


def _approvals_mode(home: Path, profile: str) -> str | None:
    """``approvals.mode`` from the profile's ``config.yaml``, else the default profile's."""
    paths = [home / "config.yaml"]
    if profile != DEFAULT_PROFILE:
        paths.insert(0, home / "profiles" / profile / "config.yaml")
    for path in paths:
        mode = _text(hermes_command_approvals.read_rendered(path))
        if mode is not None:
            return mode
    return None


# -- the gateway's version ----------------------------------------------------------------


def _version(home: Path, profile: str, fetch_json: FetchJson | None) -> str | None:
    """``version`` from ``GET /health/detailed``; ``None`` for any failure or odd answer."""
    base = api_server_base(home)
    path = (
        "/health/detailed"
        if profile == DEFAULT_PROFILE
        else f"/p/{quote(profile, safe='')}/health/detailed"
    )
    fetch = fetch_json if fetch_json is not None else _fetch_json
    try:
        body = fetch(base + path)
    except Exception:  # the gateway being down is a normal state, not an error to report
        return None
    version = body.get("version") if isinstance(body, dict) else None
    return _text(version)


def _fetch_json(url: str) -> Any:
    """Sync ``GET url`` with a 3 s timeout and no credentials; the parsed JSON, or ``None``."""
    import httpx

    from hal0.service_identity import is_loopback_url

    try:
        resp = httpx.get(url, timeout=_FETCH_TIMEOUT_S, trust_env=not is_loopback_url(url))
        if resp.status_code != 200:
            return None
        return resp.json()
    except (httpx.HTTPError, OSError, ValueError):
        return None
