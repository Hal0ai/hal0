"""Shared helpers for hal0 CLI modules."""

from __future__ import annotations

import os
import sys
from typing import Any

import httpx
from rich.console import Console

_console = Console(stderr=True)

NOT_IMPLEMENTED = "not implemented yet — see PLAN.md §13"


def _api_base() -> str:
    """Return the hal0 API base URL, honouring HAL0_API_URL env override."""
    return os.environ.get("HAL0_API_URL", "http://127.0.0.1:8080").rstrip("/")


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


def api_get(path: str, *, base: str | None = None, **kwargs: Any) -> Any:
    """GET ``path`` and return parsed JSON; raises ``CliApiError`` on non-2xx."""
    return _api_request("GET", path, base=base, **kwargs)


def api_post(path: str, *, base: str | None = None, json: Any = None, **kwargs: Any) -> Any:
    return _api_request("POST", path, base=base, json=json, **kwargs)


def api_put(path: str, *, base: str | None = None, json: Any = None, **kwargs: Any) -> Any:
    return _api_request("PUT", path, base=base, json=json, **kwargs)


def api_patch(path: str, *, base: str | None = None, json: Any = None, **kwargs: Any) -> Any:
    return _api_request("PATCH", path, base=base, json=json, **kwargs)


def api_delete(path: str, *, base: str | None = None, **kwargs: Any) -> Any:
    return _api_request("DELETE", path, base=base, **kwargs)


def _api_request(method: str, path: str, *, base: str | None, **kwargs: Any) -> Any:
    """Issue a single HTTP request and decode JSON or raise CliApiError."""
    url = (base or _api_base()).rstrip("/") + (path if path.startswith("/") else "/" + path)
    try:
        with httpx.Client(timeout=10.0) as client:
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
