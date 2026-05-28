"""Manifest resolution for MCP install-from-URL (issue #224).

When the dashboard's InstallDrawer paste-box receives a URL or package
spec, this module produces a :class:`ResolvedManifest` the route layer
can hand back to the UI for preview + use as the basis for a new
:class:`hal0.mcp.installed.InstalledServer` record.

Supported spec shapes
---------------------

``oci://ghcr.io/org/img:tag``
    OCI container reference — synthesised manifest (no manifest fetch
    over the wire yet; metadata not in the OCI ref defaults to empty).

``npm:@scope/pkg`` / ``npx:pkg``
    npm package — synthesised manifest derived from the package name.

``uvx:pkg`` / ``uv:pkg``
    Python package via uvx — synthesised manifest derived from name.

``git+https://github.com/owner/repo[.git]``
    Git repository — synthesised manifest derived from repo name.

``https://…/manifest.json`` (or any http(s) URL)
    Live manifest fetch — JSON with fields ``{name, description, tools,
    transport, resources, prompts, env}`` (subset).

This file deliberately tolerates partial manifests: every field except
``id`` + ``name`` falls back to a safe default, because the network
side of the MCP ecosystem is heterogeneous and the dashboard needs to
render *something* for any plausibly-shaped paste.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import structlog
from pydantic import BaseModel, Field

from hal0.errors import BadRequest

# Type alias for a manifest fetcher — async callable taking a URL,
# returning the parsed JSON (or any payload). Tests inject one.
HttpFetcher = Callable[[str], Awaitable[Any]]

log = structlog.get_logger(__name__)


_OCI_RE = re.compile(r"^oci://(?P<rest>.+)$")
_NPM_RE = re.compile(r"^(?:npm|npx):(?P<pkg>@?[A-Za-z0-9_/.\-]+)$")
_UV_RE = re.compile(r"^(?:uvx|uv):(?P<pkg>[A-Za-z0-9_./\-]+)$")
_GIT_RE = re.compile(r"^git\+(?P<url>https?://[A-Za-z0-9_./\-:%@]+?)(?:\.git)?/?$")
_HTTP_RE = re.compile(r"^https?://[^\s]+$")

# Maximum response body size we'll accept — protects against an
# unbounded download via a content-length lie.
_MAX_MANIFEST_BYTES = 256 * 1024
_FETCH_TIMEOUT = httpx.Timeout(connect=4.0, read=6.0, write=2.0, pool=6.0)


# ── Schema ──────────────────────────────────────────────────────────────────


class ResolvedManifest(BaseModel):
    """Manifest preview surfaced to the InstallDrawer.

    The dashboard reads ``name``, ``description``, ``tools``, plus the
    truthiness of ``env_required`` to render the install card. The full
    record is round-tripped to the install POST so the route can build
    an :class:`hal0.mcp.installed.InstalledServer` without re-resolving.
    """

    id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1)
    description: str = Field(default="")
    spec: str = Field(..., min_length=1)
    transport: str = Field(default="stdio")
    tools: int = Field(default=0, ge=0, le=4096)
    resources: int = Field(default=0, ge=0)
    prompts: int = Field(default=0, ge=0)
    env_required: list[str] = Field(default_factory=list)
    source_kind: str = Field(default="url")
    """One of ``"oci"``, ``"npm"``, ``"uvx"``, ``"git"``, ``"manifest"``,
    ``"http"`` — drives the preview's "via …" sub-label."""
    source_url: str | None = Field(default=None)
    """Original URL when the manifest came from an HTTP fetch."""
    author: str = Field(default="user")
    verified: bool = Field(default=False)


# ── ID slugging ─────────────────────────────────────────────────────────────


_SLUG_RE = re.compile(r"[^a-z0-9_-]+")


def _slug(text: str) -> str:
    """Lowercase + collapse non-id chars to ``-``. Trim to <= 64.

    The InstallDrawer's preview shows the slug so the operator can
    spot collisions before clicking Install. Empty inputs fall back to
    ``"mcp"`` so callers always get a non-empty id.
    """
    out = _SLUG_RE.sub("-", text.lower()).strip("-")
    out = re.sub(r"-+", "-", out)
    return (out or "mcp")[:64]


# ── Resolvers ───────────────────────────────────────────────────────────────


def _resolve_oci(url: str) -> ResolvedManifest:
    m = _OCI_RE.match(url)
    assert m is not None  # caller has already matched
    rest = m.group("rest")
    # Take the last path segment, drop the tag for the name.
    last = rest.rsplit("/", 1)[-1]
    name = last.split(":", 1)[0] or "mcp"
    return ResolvedManifest(
        id=_slug(name),
        name=name,
        description=f"OCI image {rest}",
        spec=url,
        transport="streamable-http",
        tools=0,
        source_kind="oci",
    )


def _resolve_npm(url: str) -> ResolvedManifest:
    m = _NPM_RE.match(url)
    assert m is not None
    pkg = m.group("pkg")
    # Drop the scope (@foo/) for the visible name.
    visible = pkg.split("/", 1)[-1] if pkg.startswith("@") else pkg
    return ResolvedManifest(
        id=_slug(visible),
        name=visible,
        description=f"npm package {pkg}",
        spec=url,
        transport="stdio",
        source_kind="npm",
    )


def _resolve_uvx(url: str) -> ResolvedManifest:
    m = _UV_RE.match(url)
    assert m is not None
    pkg = m.group("pkg")
    return ResolvedManifest(
        id=_slug(pkg),
        name=pkg,
        description=f"uvx package {pkg}",
        spec=url,
        transport="stdio",
        source_kind="uvx",
    )


def _resolve_git(url: str) -> ResolvedManifest:
    m = _GIT_RE.match(url)
    assert m is not None
    repo_url = m.group("url")
    # Last path segment as the visible name.
    last = repo_url.rstrip("/").rsplit("/", 1)[-1]
    return ResolvedManifest(
        id=_slug(last),
        name=last or "mcp",
        description=f"git repo {repo_url}",
        spec=url,
        transport="stdio",
        source_kind="git",
    )


async def _resolve_http(url: str, fetcher: HttpFetcher | None) -> ResolvedManifest:
    """Fetch + parse a JSON manifest from an HTTP(s) URL.

    Caller can inject ``fetcher`` for tests; production passes None and
    we use a default httpx client.
    """
    fetch = fetcher or _default_fetcher
    try:
        payload = await fetch(url)
    except httpx.HTTPError as exc:
        raise BadRequest(
            f"could not fetch MCP manifest from {url}",
            code="mcp.manifest_fetch_failed",
            details={"url": url, "reason": str(exc)},
        ) from exc
    if not isinstance(payload, dict):
        # Not a JSON object — treat the URL as a bare spec.
        last = url.rstrip("/").rsplit("/", 1)[-1]
        name = re.sub(r"\.(json|yaml|yml)$", "", last) or "mcp"
        return ResolvedManifest(
            id=_slug(name),
            name=name,
            description=f"manifest at {url}",
            spec=url,
            transport="stdio",
            source_kind="http",
            source_url=url,
        )
    name = str(payload.get("name") or "").strip() or _slug_from_url(url)
    description = str(payload.get("description") or "").strip()
    transport = str(payload.get("transport") or "stdio").strip() or "stdio"
    tools = _coerce_int(payload.get("tools"))
    resources = _coerce_int(payload.get("resources"))
    prompts = _coerce_int(payload.get("prompts"))
    env_required: list[str] = []
    env_block = payload.get("env")
    if isinstance(env_block, dict):
        env_required = [str(k) for k in env_block]
    elif isinstance(env_block, list):
        env_required = [str(x) for x in env_block if isinstance(x, str)]
    return ResolvedManifest(
        id=_slug(name),
        name=name,
        description=description,
        spec=url,
        transport=transport,
        tools=tools,
        resources=resources,
        prompts=prompts,
        env_required=env_required,
        source_kind="manifest",
        source_url=url,
    )


def _slug_from_url(url: str) -> str:
    last = url.rstrip("/").rsplit("/", 1)[-1]
    base = re.sub(r"\.(json|yaml|yml)$", "", last) or "mcp"
    return _slug(base)


def _coerce_int(value: Any) -> int:
    """Best-effort int coercion. Drops non-numeric values to 0.

    Manifests in the wild vary — ``tools`` shows up as ``int``, ``str``,
    or even an array (length-of-tools). Try sensible coercions and
    fall back to 0 rather than 400ing the caller.
    """
    if isinstance(value, bool):  # bool is an int subclass — exclude it
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, str):
        try:
            return max(0, int(value))
        except ValueError:
            return 0
    if isinstance(value, list):
        return len(value)
    return 0


# ── Public entrypoint ───────────────────────────────────────────────────────


async def _default_fetcher(url: str) -> Any:
    """Production fetcher — bounded body size + JSON decode."""
    async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(url, headers={"Accept": "application/json"})
        resp.raise_for_status()
        # Bound body size so a misbehaving server can't stuff us.
        body = resp.content
        if len(body) > _MAX_MANIFEST_BYTES:
            raise httpx.HTTPError(f"manifest body too large ({len(body)} > {_MAX_MANIFEST_BYTES})")
        try:
            return resp.json()
        except ValueError:
            return None


async def resolve(url: str, *, fetcher: HttpFetcher | None = None) -> ResolvedManifest:
    """Resolve a paste-box URL/spec to a :class:`ResolvedManifest`.

    The dashboard's InstallDrawer calls this once on paste to render the
    preview card; it then calls it again on Install click so the
    persisted record has the freshest resolved data.

    Args:
        url: A URL or one of the supported scheme prefixes (oci/npm/uvx/git).
        fetcher: Optional manifest fetcher — defaults to a fresh httpx
            client. Tests inject a fake so they don't hit the network.

    Raises:
        BadRequest: When the URL is empty, exceeds the length cap, or
            doesn't match any supported shape.
    """
    if not isinstance(url, str):
        raise BadRequest("url must be a string", code="mcp.url_invalid")
    url = url.strip()
    if not url:
        raise BadRequest("url is required", code="mcp.url_required")
    if len(url) > 2048:
        raise BadRequest("url too long (max 2048)", code="mcp.url_too_long")

    if _OCI_RE.match(url):
        return _resolve_oci(url)
    if _NPM_RE.match(url):
        return _resolve_npm(url)
    if _UV_RE.match(url):
        return _resolve_uvx(url)
    if _GIT_RE.match(url):
        return _resolve_git(url)
    if _HTTP_RE.match(url):
        return await _resolve_http(url, fetcher)

    raise BadRequest(
        "unsupported MCP spec — expected oci://, npm:, uvx:, git+https://, or an http(s) URL",
        code="mcp.spec_unsupported",
        details={"url": url},
    )


__all__ = [
    "HttpFetcher",
    "ResolvedManifest",
    "resolve",
]
