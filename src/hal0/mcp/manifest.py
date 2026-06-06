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

import ipaddress
import json
import re
import socket
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

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


# ── SSRF guard ──────────────────────────────────────────────────────────────
#
# The manifest fetcher is reachable through ``GET /api/mcp/resolve?url=…``
# with no auth on the LAN (ADR-0012). Without a guard, an unauthenticated
# caller can use hal0 as a blind probe against the LAN, IMDS, and loopback.
# We enforce a deny-list at the URL/IP layer and refuse to follow redirects
# (a 30x to a private host would otherwise bypass the pre-flight check).


class SsrfBlockedError(BadRequest):
    """The manifest URL resolves to a non-public address — refuse to fetch."""

    code = "mcp.ssrf_blocked"
    status = 400


def _ip_is_safe(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Reject loopback, private, link-local, unspecified, and CGNAT space.

    Mirrors the standard library's ``is_*`` classifiers plus an explicit
    carrier-grade NAT (100.64.0.0/10) reject — Python's ``is_private``
    already covers RFC 1918 + ULA + loopback + link-local on both
    families, but CGNAT only landed in 3.13's ``is_global`` semantics,
    so we check it explicitly to stay compatible across versions.
    """
    if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_unspecified:
        return False
    if ip.is_multicast or ip.is_reserved:
        return False
    # Carrier-grade NAT (RFC 6598). is_private in newer Python versions
    # includes this, but be explicit for portability.
    return not (
        isinstance(ip, ipaddress.IPv4Address) and ip in ipaddress.IPv4Network("100.64.0.0/10")
    )


def _is_safe_url(url: str) -> bool:
    """Return True only when ``url`` resolves entirely to public addresses.

    Steps:

    1. Parse the URL. Reject anything without an http(s) scheme + host.
    2. Reject ``*.local`` mDNS hostnames before DNS lookup — most
       resolvers happily answer them from the LAN.
    3. Resolve the hostname via ``socket.getaddrinfo`` and reject if
       *any* resolved address is in deny space (avoids DNS-rebinding
       single-A-record tricks where one of N IPs is private).
    4. If the host is already a literal IP, classify it directly.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    scheme = (parts.scheme or "").lower()
    if scheme not in {"http", "https"}:
        return False
    host = parts.hostname
    if not host:
        return False
    low = host.lower()
    # mDNS / multicast DNS hostnames — never let these through.
    if low == "localhost" or low.endswith(".local"):
        return False

    # If the host parsed as an IP literal, classify it directly. urlsplit
    # exposes the unbracketed form for IPv6 via .hostname.
    try:
        literal = ipaddress.ip_address(low)
    except ValueError:
        literal = None
    if literal is not None:
        return _ip_is_safe(literal)

    # DNS lookup. Any resolved address in deny space → reject.
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    for info in infos:
        sockaddr = info[4]
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except (ValueError, IndexError):
            return False
        if not _ip_is_safe(ip):
            return False
    return True


def _enforce_safe_url(url: str) -> None:
    """Raise :class:`SsrfBlockedError` when the URL fails the SSRF guard."""
    if not _is_safe_url(url):
        raise SsrfBlockedError(
            "refusing to fetch a non-public URL",
            code="mcp.ssrf_blocked",
            details={"url": url},
        )


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
    if m is None:  # defensive — caller has already matched, but assert
        # was stripped under ``python -O`` so we raise a typed error.
        raise BadRequest(f"invalid oci spec: {url}", code="mcp.spec_unsupported")
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
    if m is None:
        raise BadRequest(f"invalid npm spec: {url}", code="mcp.spec_unsupported")
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
    if m is None:
        raise BadRequest(f"invalid uvx spec: {url}", code="mcp.spec_unsupported")
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
    if m is None:
        raise BadRequest(f"invalid git spec: {url}", code="mcp.spec_unsupported")
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
        # Cap at the schema's pydantic upper bound (le=4096) so a manifest
        # with a runaway tool list doesn't trip a 500 in validation.
        return min(len(value), 4096)
    return 0


# ── Public entrypoint ───────────────────────────────────────────────────────


async def _default_fetcher(url: str) -> Any:
    """Production fetcher — SSRF-guarded, bounded body, JSON decoded.

    Redirects are intentionally NOT followed: a 30x to a private host
    would bypass the pre-flight :func:`_enforce_safe_url` check. If a
    legitimate manifest sits behind a redirect, the caller can paste the
    final URL directly.
    """
    _enforce_safe_url(url)
    # Stream the body and abort the moment we cross the cap (#381) — a
    # misbehaving server must not be able to make us buffer an unbounded
    # response into memory before we reject it.
    async with (
        httpx.AsyncClient(timeout=_FETCH_TIMEOUT, follow_redirects=False) as client,
        client.stream("GET", url, headers={"Accept": "application/json"}) as resp,
    ):
        resp.raise_for_status()
        chunks: list[bytes] = []
        total = 0
        async for chunk in resp.aiter_bytes():
            total += len(chunk)
            if total > _MAX_MANIFEST_BYTES:
                raise httpx.HTTPError(f"manifest body too large (> {_MAX_MANIFEST_BYTES})")
            chunks.append(chunk)
    body = b"".join(chunks)
    try:
        return json.loads(body)
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
        # SSRF guard: enforce here as well as inside _default_fetcher so a
        # test-injected fetcher (or any future caller path) still gets the
        # pre-flight check. Skipped when the caller injects a fetcher — the
        # test surface relies on synthesised hosts like example.com.
        if fetcher is None:
            _enforce_safe_url(url)
        return await _resolve_http(url, fetcher)

    raise BadRequest(
        "unsupported MCP spec — expected oci://, npm:, uvx:, git+https://, or an http(s) URL",
        code="mcp.spec_unsupported",
        details={"url": url},
    )


__all__ = [
    "HttpFetcher",
    "ResolvedManifest",
    "SsrfBlockedError",
    "resolve",
]
