"""Shared helpers for hal0 CLI modules."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx
from rich.console import Console

_console = Console(stderr=True)

NOT_IMPLEMENTED = "not implemented yet — see PLAN.md §13"


def _api_base() -> str:
    """Return the hal0 API base URL, honouring HAL0_API_URL env override."""
    return os.environ.get("HAL0_API_URL", "http://127.0.0.1:8080").rstrip("/")


def _key_from_api_env() -> str | None:
    """Best-effort read of an API key from /etc/hal0/api.env (admin preferred).

    Thin back-compat wrapper over :func:`hal0.service_identity.keys_from_api_env`
    — the box-key discovery now lives in one place shared with the brain
    steward's internal self-calls so the two surfaces can't drift.
    """
    from hal0.service_identity import keys_from_api_env

    found = keys_from_api_env()
    return found.get("HAL0_ADMIN_KEY") or found.get("HAL0_CLIENT_KEY")


def _auth_headers() -> dict[str, str]:
    """Bearer header for CLI→API calls on an auth-enabled box (halo150 O2).

    Precedence: HAL0_ADMIN_KEY env → HAL0_CLIENT_KEY env → api.env on disk.
    Empty when no key is discoverable — loopback dev boxes stay keyless and
    the API's development-open posture handles them. Delegates to the shared
    :mod:`hal0.service_identity` seam (admin-preferred, matching the CLI's
    historical behaviour).
    """
    from hal0.service_identity import service_auth_headers

    return service_auth_headers(prefer="admin")


def auth_client(**kwargs: Any) -> httpx.Client:
    """Build an ``httpx.Client`` with ``_auth_headers()`` baked into its
    default headers, so every request issued through it — one-shot or
    long-lived streaming — carries the CLI's discovered bearer token on
    auth-enabled boxes. An explicit ``headers=`` kwarg is merged on top
    (a caller-set ``Authorization`` wins over the discovered one).

    Use this directly (rather than :func:`api_stream`) when a call site
    needs a *persistent* client across several requests — e.g. ``hal0
    chat``'s REPL loop, which reuses one client for every turn.
    """
    headers = dict(kwargs.pop("headers", None) or {})
    if "Authorization" not in headers:
        headers.update(_auth_headers())
    return httpx.Client(headers=headers, **kwargs)


@contextmanager
def api_stream(
    method: str,
    path: str,
    *,
    base: str | None = None,
    timeout: float | None = None,
    params: dict[str, Any] | None = None,
) -> Iterator[httpx.Response]:
    """Open an authenticated streaming request and yield the response.

    Thin wrapper over :func:`auth_client` for the CLI's one-shot SSE tails
    (``slot logs --follow``, ``doctor logs --follow``): opens a client with
    ``_auth_headers()`` applied, issues a streaming request, and tears both
    down when the caller's ``with`` block exits. Mirrors ``httpx.stream()``'s
    call shape so existing call sites barely change — swap
    ``httpx.stream(method, url, ...)`` for ``api_stream(method, path, ...)``.

    Before this helper, these SSE tails called the module-level
    ``httpx.stream()`` directly, bypassing ``_auth_headers()`` entirely —
    they 401'd (or silently printed the JSON error envelope as if it were
    a log line) on auth-enabled boxes.
    """
    url = (base or _api_base()).rstrip("/") + (path if path.startswith("/") else "/" + path)
    with auth_client(timeout=timeout) as client, client.stream(method, url, params=params) as resp:
        yield resp


def _iter_sse_payloads(resp: httpx.Response) -> Iterator[Any]:
    """Parse an SSE response's ``data: ...`` lines into JSON (or raw text)."""
    for raw in resp.iter_lines():
        if not raw or not raw.startswith("data:"):
            continue
        payload = raw[len("data:") :].strip()
        try:
            yield json.loads(payload)
        except ValueError:
            yield payload


def follow_sse_logs(
    path: str, *, console: Console, params: dict[str, object] | None = None
) -> None:
    """Stream an SSE log tail via :func:`api_stream` and print each line.

    Shared by ``hal0 slot logs --follow`` and ``hal0 doctor logs --follow``
    — both are line-buffered passthroughs over the same ``data: ...`` SSE
    framing, just against different endpoints/params. Runs until the
    connection drops or the operator hits Ctrl-C. On 401/403 (missing or
    stale credentials on an auth-enabled box) prints one actionable line via
    :func:`die` instead of silently echoing the JSON error envelope as if it
    were log output.
    """
    try:
        with api_stream("GET", path, timeout=None, params=params) as r:
            if r.status_code in (401, 403):
                die(f"not authorized ({r.status_code}) — check HAL0_ADMIN_KEY/HAL0_CLIENT_KEY.")
                return
            for item in _iter_sse_payloads(r):
                console.print(item)
    except (httpx.HTTPError, KeyboardInterrupt):
        return


def _api_unreachable(url: str) -> bool:
    """Return True (and print an error) if the API is not reachable on ``url``.

    Performs a quick HEAD on /api/status so CLI commands don't hang for the
    full HTTP timeout when the daemon is down.
    """
    try:
        with httpx.Client(timeout=1.0) as client:
            r = client.head(url + "/api/status")
        if r.status_code >= 500:
            api_unreachable_print(url)
            return True
        return False
    except (httpx.HTTPError, OSError):
        api_unreachable_print(url)
        return True


def api_unreachable_print(url: str) -> None:
    _console.print(
        f"[bold red]hal0 API not running on {url}.[/bold red]"
        "  Start it with: [bold]hal0 serve[/bold]"
    )


def api_unreachable_exit(url: str) -> None:
    """Print an error and exit 1 when the API cannot be reached."""
    api_unreachable_print(url)
    sys.exit(1)


def api_get(path: str, *, base: str | None = None, timeout: float = 10.0, **kwargs: Any) -> Any:
    """GET ``path`` and return parsed JSON; raises ``CliApiError`` on non-2xx."""
    return _api_request("GET", path, base=base, timeout=timeout, **kwargs)


def api_post(
    path: str, *, base: str | None = None, json: Any = None, timeout: float = 10.0, **kwargs: Any
) -> Any:
    return _api_request("POST", path, base=base, json=json, timeout=timeout, **kwargs)


def api_put(
    path: str, *, base: str | None = None, json: Any = None, timeout: float = 10.0, **kwargs: Any
) -> Any:
    return _api_request("PUT", path, base=base, json=json, timeout=timeout, **kwargs)


def api_patch(
    path: str, *, base: str | None = None, json: Any = None, timeout: float = 10.0, **kwargs: Any
) -> Any:
    return _api_request("PATCH", path, base=base, json=json, timeout=timeout, **kwargs)


def api_delete(path: str, *, base: str | None = None, timeout: float = 10.0, **kwargs: Any) -> Any:
    return _api_request("DELETE", path, base=base, timeout=timeout, **kwargs)


def api_get_bytes(
    path: str, *, base: str | None = None, timeout: float = 60.0, **kwargs: Any
) -> tuple[bytes, str]:
    """GET ``path`` and return ``(raw_bytes, content_type)`` — for binary payloads
    (e.g. the ``document-transfer`` export ZIP) that ``api_get``'s JSON decode
    would otherwise mangle. Raises ``CliApiError`` on non-2xx, same as the
    other ``api_*`` helpers.
    """
    url = (base or _api_base()).rstrip("/") + (path if path.startswith("/") else "/" + path)
    headers = dict(kwargs.pop("headers", None) or {})
    if "Authorization" not in headers:
        headers.update(_auth_headers())
    if headers:
        kwargs["headers"] = headers
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url, **kwargs)
    except httpx.HTTPError as exc:
        raise CliApiError(f"GET {url} failed: {type(exc).__name__}: {exc}") from exc
    if resp.status_code >= 400:
        try:
            body = resp.json()
            msg = body.get("error", {}).get("message") or body
        except ValueError:
            msg = resp.text[:300]
        raise CliApiError(f"GET {url} → HTTP {resp.status_code}: {msg}")
    return resp.content, resp.headers.get("content-type", "application/octet-stream")


def _api_request(
    method: str, path: str, *, base: str | None, timeout: float = 10.0, **kwargs: Any
) -> Any:
    """Issue a single HTTP request and decode JSON or raise CliApiError."""
    url = (base or _api_base()).rstrip("/") + (path if path.startswith("/") else "/" + path)
    # Attach discovered credentials unless the caller set their own
    # Authorization header. Fixes anonymous CLI probes on auth-on boxes
    # (halo150 O2: doctor reported every gated endpoint "unreachable").
    headers = dict(kwargs.pop("headers", None) or {})
    if "Authorization" not in headers:
        headers.update(_auth_headers())
    if headers:
        kwargs["headers"] = headers
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise CliApiError(f"{method} {url} failed: {type(exc).__name__}: {exc}") from exc
    if resp.status_code >= 400:
        try:
            body = resp.json()
            msg = body.get("error", {}).get("message") or body
        except ValueError:
            msg = resp.text[:300]
        raise CliApiError(f"{method} {url} → HTTP {resp.status_code}: {msg}")
    if not resp.content:
        return None
    try:
        return resp.json()
    except ValueError:
        return resp.text


class CliApiError(RuntimeError):
    """Raised by the api_* helpers when the API returns an error."""


def die(msg: str, code: int = 1) -> None:
    """Print an error to stderr and exit."""
    _console.print(f"[bold red]Error:[/bold red] {msg}")
    sys.exit(code)
