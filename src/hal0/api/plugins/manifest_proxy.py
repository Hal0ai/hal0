"""Plugin manifest + static-asset proxy (v0.3, PR-7).

Reverse-proxies upstream Hermes's dashboard plugin endpoints so the hal0
v3 dashboard can consume them through hal0-api as a single boundary:

* ``GET /api/dashboard/plugins``
    Returns the upstream JSON manifest list verbatim with a
    ``Content-Security-Policy: script-src 'self' 'strict-dynamic'``
    header attached (DA-sec-ops MUST-FIX #4).

* ``GET /dashboard-plugins/{name}/{file_path:path}``
    Streams the upstream plugin static asset (the JS/CSS bundle the
    dashboard loads via ``<script integrity=...>``). When the manifest
    declares ``integrity`` (``sha384-...`` or ``sha256-...``) for that
    asset, we compute the hash of the fetched body and refuse with 502
    on mismatch (DA-sec-ops MUST-FIX #4).

    The ``file_path`` is run through the same path-traversal validator
    upstream uses for its ``api`` field (GHSA-5qr3-c538-wm9j): the path
    must be relative, must not contain ``..`` traversal that escapes
    the plugin's notional dashboard root, and must not be absolute.

Header policy (DA-sec-ops MUST-FIX #2):

* Inbound: strip ``Authorization`` + ``Cookie`` from the browser request
  before forwarding. These are hal0 session credentials; upstream
  Hermes does not need them and must not see them.
* Outbound: inject ``X-hal0-Agent: hermes`` (resolved from the
  ``HAL0_AGENT_ID`` env or a per-request override) per ADR-0012.

Networking: upstream is loopback-only (``HERMES_DASHBOARD_BASE_URL``,
default ``http://127.0.0.1:9119`` to match
``docs/agents/hermes/SERVICE.md``). When upstream is unreachable, both
endpoints return a hal0-shaped error envelope so the dashboard can
render the "Hermes offline — plugins unavailable" banner.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
from pathlib import PurePosixPath
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, Request
from fastapi.responses import Response

log = structlog.get_logger(__name__)

router = APIRouter()


# ── upstream / config ─────────────────────────────────────────────────


_DEFAULT_UPSTREAM = "http://127.0.0.1:9119"

# CSP applied to the manifest endpoint. ``strict-dynamic`` allows the
# manifest-driven <script> tags injected by ``PluginTabHost`` while
# preventing inline scripts and unallowlisted origins from running.
_MANIFEST_CSP = "script-src 'self' 'strict-dynamic'"

# Hop-by-hop headers — must not be forwarded across the proxy boundary
# (standard RFC 9110 §7.6.1 set).
_REQUEST_HOP_BY_HOP = frozenset(
    {
        "host",
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "content-length",
    }
)

_RESPONSE_HOP_BY_HOP = frozenset(
    {
        "connection",
        "content-encoding",
        "content-length",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)

# Browser session credentials. Stripped from every inbound request
# before forwarding so upstream Hermes never sees hal0 cookies / bearer
# tokens (ADR-0012 routes identity through ``X-hal0-Agent`` only; the
# DA-sec-ops review locked this in for plugin proxy traffic too).
_INBOUND_STRIP = frozenset(
    {
        "authorization",
        "cookie",
        "x-hermes-session-token",
    }
)


def _upstream_base_url() -> str:
    """Resolve the upstream Hermes dashboard base URL."""
    return os.environ.get("HERMES_DASHBOARD_BASE_URL", _DEFAULT_UPSTREAM).rstrip("/")


def _agent_id() -> str:
    """Resolve the value of the outbound ``X-hal0-Agent`` header.

    v0.3 ships a single bundled agent (``hermes``); future plugin
    surfaces parameterised by ``agent_id`` will pass the value through
    from the route. The env override matches the rest of the hal0-agent
    stack so contributors can point at a non-default agent in dev.
    """
    return os.environ.get("HAL0_AGENT_ID", "hermes") or "hermes"


def _filter_request_headers(headers: Any) -> dict[str, str]:
    """Strip hop-by-hop + browser session credentials from the request."""
    return {
        k: v
        for k, v in headers.items()
        if k.lower() not in _REQUEST_HOP_BY_HOP and k.lower() not in _INBOUND_STRIP
    }


def _filter_response_headers(headers: Any) -> dict[str, str]:
    """Strip hop-by-hop + length headers from the upstream response."""
    return {k: v for k, v in headers.items() if k.lower() not in _RESPONSE_HOP_BY_HOP}


def _build_client(timeout: httpx.Timeout) -> httpx.AsyncClient:
    """Construct the per-request httpx client.

    Module-level seam so tests can monkeypatch it to inject an
    ``httpx.MockTransport`` without spinning up a real socket.
    """
    return httpx.AsyncClient(timeout=timeout)


# ── path-traversal validator (ported from GHSA-5qr3-c538-wm9j) ────────


# Plugin names are short identifiers — bound to the same alphabet
# upstream uses for the ``name`` field of ``plugin.yaml`` /
# ``dashboard/manifest.json`` (alphanumerics + dash + underscore + dot
# for versioned scopes). The strict regex doubles as a denial gate for
# obvious traversal attempts in the plugin segment.
_PLUGIN_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _safe_plugin_asset_relpath(file_path: str) -> str | None:
    """Validate the ``file_path`` portion of a static-asset request.

    Returns the original ``file_path`` (untouched) when safe; ``None``
    when the path must be rejected.

    The rules are a verbatim port of upstream's
    ``_safe_plugin_api_relpath`` (``hermes_cli/web_server.py:4233``)
    plus the asset-side ``serve_plugin_asset`` resolve / is-relative-to
    check:

    * empty / non-string → reject
    * absolute path → reject (``Path('plugin/dir') / '/etc/passwd'``
      resolves to ``/etc/passwd``)
    * ``..`` traversal that escapes the notional plugin root → reject

    The validator operates on a POSIX path against a synthetic root so
    it does NOT require the upstream plugin directory to exist on the
    hal0 box (we are proxying, not loading from disk).
    """
    if not isinstance(file_path, str) or not file_path.strip():
        return None

    # Absolute POSIX paths are illegal — they would discard the
    # plugin-name prefix when joined.
    if file_path.startswith("/"):
        return None

    # Backslashes are not a path separator in URLs; treat them as
    # literal but disallow because they confuse Windows-style code
    # paths if this ever runs on a non-POSIX host.
    if "\\" in file_path:
        return None

    # Synthesise a root + resolved candidate using PurePosixPath so the
    # check is host-agnostic.
    root = PurePosixPath("/__hal0_plugin_root__")
    candidate = PurePosixPath(file_path)
    if candidate.is_absolute():
        return None

    resolved_parts: list[str] = []
    for part in candidate.parts:
        if part in ("", "."):
            continue
        if part == "..":
            # Refuse if traversal would escape the root.
            if not resolved_parts:
                return None
            resolved_parts.pop()
            continue
        resolved_parts.append(part)

    if not resolved_parts:
        return None

    resolved = root.joinpath(*resolved_parts)
    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    return file_path


def _safe_plugin_name(name: str) -> bool:
    """Reject obviously-malformed plugin name path segments."""
    return bool(_PLUGIN_NAME_RE.match(name))


# ── SRI verification ─────────────────────────────────────────────────


_SRI_ALGS = {
    "sha256": hashlib.sha256,
    "sha384": hashlib.sha384,
    "sha512": hashlib.sha512,
}


def _parse_integrity(integrity: str) -> tuple[str, bytes] | None:
    """Parse a single SRI token (``sha384-<base64>``).

    Returns ``(algorithm, expected_digest_bytes)`` or ``None`` when the
    token is malformed / unsupported. We accept the first whitespace-
    separated token only — the subresource-integrity spec allows
    multiple algorithms but the upstream plugin manifest only ever
    declares one.
    """
    if not isinstance(integrity, str) or "-" not in integrity:
        return None
    token = integrity.strip().split()[0]
    alg, _, b64 = token.partition("-")
    alg = alg.lower()
    if alg not in _SRI_ALGS:
        return None
    try:
        expected = base64.b64decode(b64, validate=True)
    except (ValueError, binascii.Error) as exc:
        log.warning("plugin.sri.malformed_b64", token=token, error=str(exc))
        return None
    if len(expected) != _SRI_ALGS[alg]().digest_size:
        return None
    return alg, expected


def _verify_sri(body: bytes, integrity: str) -> bool:
    """Recompute the SRI digest and compare to the manifest declaration.

    Returns ``True`` when ``integrity`` parses and matches, ``False``
    when it parses but does not match. Returns ``None`` (via the
    caller-side ``_parse_integrity`` already filtering) is not used
    here — caller checks for parse failure separately.
    """
    parsed = _parse_integrity(integrity)
    if parsed is None:
        return False
    alg, expected = parsed
    actual = _SRI_ALGS[alg](body).digest()
    return hmac.compare_digest(actual, expected)


# ── error envelopes ───────────────────────────────────────────────────


def _error_response(
    *,
    code: str,
    message: str,
    status: int,
    target: str,
    extra: dict[str, Any] | None = None,
) -> Response:
    payload: dict[str, Any] = {
        "error": {
            "code": code,
            "message": message,
            "details": {"target": target},
        }
    }
    if extra:
        payload["error"]["details"].update(extra)
    return Response(
        content=json.dumps(payload).encode("utf-8"),
        status_code=status,
        media_type="application/json",
    )


# ── manifest cache (per-request best-effort) ──────────────────────────


# The manifest is cheap to refetch but the asset proxy needs to know
# the declared ``integrity`` for the file the browser asks for. The
# cache is process-scoped and best-effort: when the upstream restarts,
# the manifest is refetched on the next call.
_MANIFEST_CACHE: dict[str, list[dict[str, Any]]] = {}


def _manifest_cache_clear() -> None:
    """Test seam — wipe the module-level manifest cache."""
    _MANIFEST_CACHE.clear()


async def _fetch_manifest(
    *,
    base: str,
    headers: dict[str, str],
    timeout: httpx.Timeout,
) -> list[dict[str, Any]] | None:
    """Fetch and cache the upstream manifest list."""
    url = f"{base}/api/dashboard/plugins"
    client = _build_client(timeout)
    try:
        try:
            resp = await client.get(url, headers=headers)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.HTTPError):
            return None
    finally:
        await client.aclose()
    if resp.status_code != 200:
        return None
    try:
        body = resp.json()
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(body, list):
        return None
    plugins: list[dict[str, Any]] = [m for m in body if isinstance(m, dict)]
    _MANIFEST_CACHE[base] = plugins
    return plugins


def _manifest_entry_for(plugins: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    for entry in plugins:
        if entry.get("name") == name:
            return entry
    return None


def _expected_integrity_for_asset(entry: dict[str, Any], file_path: str) -> str | None:
    """Resolve the manifest's ``integrity`` value for ``file_path``.

    The upstream manifest stores SRI either at the top level
    (``integrity`` applies to the ``entry`` bundle) or per-asset via
    optional ``integrity_map`` (``{relative_path: 'sha384-...'}``). The
    upstream contract today only ships the top-level form for the
    primary JS bundle; we accept both shapes so future per-asset SRI
    drops in without proxy changes.
    """
    integrity_map = entry.get("integrity_map")
    if isinstance(integrity_map, dict):
        candidate = integrity_map.get(file_path)
        if isinstance(candidate, str) and candidate.strip():
            return candidate

    primary_entry = entry.get("entry", "dist/index.js")
    if file_path == primary_entry:
        top = entry.get("integrity")
        if isinstance(top, str) and top.strip():
            return top
    return None


# ── manifest proxy endpoint ───────────────────────────────────────────


@router.get("/api/dashboard/plugins", include_in_schema=False)
async def proxy_dashboard_plugins(request: Request) -> Response:
    """Return the upstream Hermes plugin manifest list."""
    base = _upstream_base_url()
    url = f"{base}/api/dashboard/plugins"
    headers = _filter_request_headers(request.headers)
    headers["X-hal0-Agent"] = _agent_id()

    timeout = httpx.Timeout(connect=2.0, read=10.0, write=5.0, pool=5.0)
    client = _build_client(timeout)
    try:
        try:
            upstream_resp = await client.get(url, headers=headers)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            return _error_response(
                code="plugins.unavailable",
                message="hermes dashboard is not reachable on loopback",
                status=503,
                target=url,
                extra={"reason": str(exc)},
            )
        except httpx.HTTPError as exc:
            return _error_response(
                code="plugins.proxy_error",
                message="error forwarding plugin manifest request",
                status=502,
                target=url,
                extra={"reason": str(exc)},
            )
    finally:
        await client.aclose()

    # Refresh the per-process manifest cache opportunistically so the
    # asset proxy can resolve the SRI expectation without a second
    # round-trip per request.
    if upstream_resp.status_code == 200:
        try:
            parsed = upstream_resp.json()
            if isinstance(parsed, list):
                _MANIFEST_CACHE[base] = [m for m in parsed if isinstance(m, dict)]
        except (ValueError, json.JSONDecodeError):
            pass

    response_headers = _filter_response_headers(upstream_resp.headers)
    # DA-sec-ops MUST-FIX #4: pin the CSP for the manifest endpoint.
    # ``strict-dynamic`` lets any script the manifest declares (with a
    # valid SRI) hydrate the dashboard, but inline scripts and other
    # origins stay blocked.
    response_headers["content-security-policy"] = _MANIFEST_CSP
    # Belt-and-braces: never let a browser cache the manifest — a stale
    # entry could outlive an SRI rotation.
    response_headers["cache-control"] = "no-store, no-cache, must-revalidate"
    media_type = upstream_resp.headers.get("content-type", "application/json")

    return Response(
        content=upstream_resp.content,
        status_code=upstream_resp.status_code,
        headers=response_headers,
        media_type=media_type,
    )


# ── static-asset proxy endpoint ───────────────────────────────────────


@router.get(
    "/dashboard-plugins/{plugin_name}/{file_path:path}",
    include_in_schema=False,
)
async def proxy_plugin_asset(request: Request, plugin_name: str, file_path: str) -> Response:
    """Stream a plugin static asset, with SRI verification + traversal guard."""
    if not _safe_plugin_name(plugin_name):
        return _error_response(
            code="plugins.invalid_name",
            message="plugin name contains illegal characters",
            status=400,
            target=plugin_name,
        )
    safe_path = _safe_plugin_asset_relpath(file_path)
    if safe_path is None:
        return _error_response(
            code="plugins.path_traversal",
            message="asset path rejected (traversal or absolute path)",
            status=400,
            target=file_path,
        )

    base = _upstream_base_url()
    target_url = f"{base}/dashboard-plugins/{plugin_name}/{safe_path}"

    headers = _filter_request_headers(request.headers)
    headers["X-hal0-Agent"] = _agent_id()

    timeout = httpx.Timeout(connect=2.0, read=30.0, write=10.0, pool=5.0)
    client = _build_client(timeout)
    try:
        try:
            upstream_resp = await client.get(target_url, headers=headers)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            return _error_response(
                code="plugins.unavailable",
                message="hermes dashboard is not reachable on loopback",
                status=503,
                target=target_url,
                extra={"reason": str(exc)},
            )
        except httpx.HTTPError as exc:
            return _error_response(
                code="plugins.proxy_error",
                message="error forwarding plugin asset request",
                status=502,
                target=target_url,
                extra={"reason": str(exc)},
            )
    finally:
        await client.aclose()

    # Non-200 → propagate verbatim. The browser uses the status code to
    # surface "plugin missing" / 404 / 410.
    if upstream_resp.status_code != 200:
        response_headers = _filter_response_headers(upstream_resp.headers)
        media_type = upstream_resp.headers.get("content-type")
        return Response(
            content=upstream_resp.content,
            status_code=upstream_resp.status_code,
            headers=response_headers,
            media_type=media_type,
        )

    body = upstream_resp.content

    # SRI verification (DA-sec-ops MUST-FIX #4). Look up the expected
    # digest in the cached manifest. If the manifest is not cached yet,
    # fetch it now so first-call asset requests still verify.
    plugins = _MANIFEST_CACHE.get(base)
    if plugins is None:
        plugins = await _fetch_manifest(
            base=base,
            headers={"X-hal0-Agent": _agent_id()},
            timeout=timeout,
        )
    expected_integrity: str | None = None
    if plugins is not None:
        entry = _manifest_entry_for(plugins, plugin_name)
        if entry is not None:
            expected_integrity = _expected_integrity_for_asset(entry, safe_path)

    if expected_integrity is not None:
        parsed = _parse_integrity(expected_integrity)
        if parsed is None:
            log.warning(
                "plugin.sri.malformed",
                plugin=plugin_name,
                asset=safe_path,
                integrity=expected_integrity,
            )
            return _error_response(
                code="plugins.sri_malformed",
                message="manifest integrity value is malformed",
                status=502,
                target=target_url,
                extra={"plugin": plugin_name, "asset": safe_path},
            )
        if not _verify_sri(body, expected_integrity):
            log.warning(
                "plugin.sri.mismatch",
                plugin=plugin_name,
                asset=safe_path,
                integrity=expected_integrity,
            )
            return _error_response(
                code="plugins.sri_mismatch",
                message="plugin asset SRI mismatch",
                status=502,
                target=target_url,
                extra={"plugin": plugin_name, "asset": safe_path},
            )

    response_headers = _filter_response_headers(upstream_resp.headers)
    # Asset bodies are immutable per SRI digest; let the browser cache
    # them aggressively but tag with the SRI so a manifest rotation
    # invalidates correctly. When SRI is absent we fall back to no-store
    # so a swapped-in malicious upstream cannot persist.
    if expected_integrity is not None:
        response_headers["cache-control"] = "public, max-age=300, immutable"
    else:
        response_headers["cache-control"] = "no-store, no-cache, must-revalidate"
    media_type = upstream_resp.headers.get("content-type")

    return Response(
        content=body,
        status_code=200,
        headers=response_headers,
        media_type=media_type,
    )


__all__ = [
    "_MANIFEST_CSP",
    "_agent_id",
    "_build_client",
    "_expected_integrity_for_asset",
    "_manifest_cache_clear",
    "_parse_integrity",
    "_safe_plugin_asset_relpath",
    "_safe_plugin_name",
    "_upstream_base_url",
    "_verify_sri",
    "router",
]
