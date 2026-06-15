"""Hermes kanban REST client — the Operator Board's upstream front door.

hal0-api proxies the Hermes dashboard's kanban plugin
(``{HERMES_DASHBOARD_BASE_URL}/api/plugins/kanban/*`` + the ``/events`` WS) so
the dashboard's Operator Board page talks to ONE boundary (hal0-api :8080),
never to the loopback-only Hermes dashboard (:9119) directly.

This client mirrors :class:`hal0.memory.hindsight_client.HindsightRestClient`:
``from_env()`` reads config, ``_headers()`` injects auth, and
``request_json()`` is the single generic authenticated forward the
table-driven proxy router funnels every read/CRUD row through, so auth +
base-url + error mapping live in exactly one place.

Auth / transport (SPEC §2.G / §7):

* Hermes is loopback-only inside CT105 and gates ``/api/*`` behind an
  **ephemeral per-process bearer** that rotates every restart. The browser
  can never set it; hal0-api resolves it server-side via a pluggable
  ``session_token`` resolver (default: env ``HERMES_SESSION_TOKEN``). It is
  sent both as ``X-Hermes-Session-Token`` and ``Authorization: Bearer`` so
  either of Hermes's two accepted forms works.
* ``X-hal0-Agent`` is attached outbound for audit attribution (resolved from
  ``HAL0_AGENT_ID``; per-request override threaded by the router).
* Browser session credentials (``Authorization`` / ``Cookie`` /
  ``X-Hermes-Session-Token``) coming IN from the browser are stripped by the
  router before the body ever reaches this client — this client only ever
  sends the server-resolved token.

Error mapping (``board.*`` codes, surfaced via :class:`hal0.errors.Hal0Error`):

* upstream 4xx → pass the status through with ``board.upstream_error``
* upstream 5xx → ``502 board.upstream_error``
* transport failure (ConnectError / timeout / other httpx error) →
  ``503 board.unreachable``
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import httpx

from hal0.errors import Hal0Error

# Loopback Hermes dashboard default (SPEC §2.B / §7). Override with
# HERMES_DASHBOARD_BASE_URL — the single env var the whole board surface
# standardises on (matches manifest_proxy.py).
DEFAULT_BASE_URL = "http://127.0.0.1:9119"

# Every kanban REST path hangs off this prefix on the Hermes dashboard.
KANBAN_BASE_PATH = "/api/plugins/kanban"


class BoardUpstreamError(Hal0Error):
    """Hermes answered with an error; status mirrors upstream (4xx) or 502."""

    code = "board.upstream_error"
    status = 502


class BoardUnreachable(Hal0Error):
    """hal0-api could not reach the Hermes dashboard on loopback."""

    code = "board.unreachable"
    status = 503


def _default_session_token() -> str | None:
    """Resolve the Hermes per-process session bearer.

    Default resolver reads ``HERMES_SESSION_TOKEN`` from the environment.
    Returns ``None`` when unset (Hermes will then reject and the upstream
    4xx/401 is surfaced honestly rather than faked). Deployments that run
    Hermes on loopback with a stable injected token set this env var; the
    rotating-token case is handled by pointing the resolver at a reader
    that re-reads the live token (future work — see report).
    """
    tok = os.environ.get("HERMES_SESSION_TOKEN")
    return tok or None


def _default_agent_id() -> str:
    """Outbound ``X-hal0-Agent`` value (matches the rest of the proxy stack)."""
    return os.environ.get("HAL0_AGENT_ID", "hermes") or "hermes"


class HermesKanbanClient:
    """Async REST client for the Hermes kanban plugin surface.

    Args:
        base_url: Hermes dashboard base (no trailing ``/api/plugins/kanban``).
        session_token_resolver: zero-arg callable returning the current
            Hermes session bearer (or ``None``). Defaults to reading
            ``HERMES_SESSION_TOKEN``. A callable (not a static value) so a
            rotating token can be re-read per request without reconstructing
            the client.
        agent_id: value for the outbound ``X-hal0-Agent`` header.
        http_client: shared httpx client (tests inject a MockTransport-backed
            one). When omitted the client owns its own and closes it.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        session_token_resolver: Callable[[], str | None] | None = None,
        agent_id: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._resolve_token = session_token_resolver or _default_session_token
        self._agent_id = agent_id or _default_agent_id()
        self._owns = http_client is None
        self._http = http_client or httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(120.0, connect=3.0),
        )

    @classmethod
    def from_env(cls) -> HermesKanbanClient:
        base = os.environ.get("HERMES_DASHBOARD_BASE_URL", DEFAULT_BASE_URL)
        return cls(base_url=base)

    # ── headers ──────────────────────────────────────────────────────────

    def _headers(self, *, agent_id: str | None = None) -> dict[str, str]:
        """Build the outbound header set.

        Injects the Hermes session bearer in BOTH accepted forms
        (``X-Hermes-Session-Token`` + ``Authorization: Bearer``) plus
        ``X-hal0-Agent`` for audit attribution. ``agent_id`` lets the router
        thread a per-request agent override (e.g. an MCP caller's identity).
        """
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "X-hal0-Agent": agent_id or self._agent_id,
        }
        token = self._resolve_token()
        if token:
            headers["X-Hermes-Session-Token"] = token
            headers["Authorization"] = f"Bearer {token}"
        return headers

    # ── generic forward ──────────────────────────────────────────────────

    async def request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
        agent_id: str | None = None,
    ) -> Any:
        """Authenticated forward to a kanban path, returning the parsed JSON.

        ``path`` is the upstream sub-path WITHOUT the ``/api/plugins/kanban``
        prefix (e.g. ``/board``, ``/tasks/{id}``); the prefix is prepended
        here so callers only think in contract terms.

        Maps errors to the ``board.*`` family:

        * upstream 4xx → :class:`BoardUpstreamError` with the upstream status
        * upstream 5xx → :class:`BoardUpstreamError` (502)
        * transport failure → :class:`BoardUnreachable` (503)
        """
        upstream_path = f"{KANBAN_BASE_PATH}{path}"
        try:
            resp = await self._http.request(
                method,
                upstream_path,
                headers=self._headers(agent_id=agent_id),
                params=params,
                json=json_body,
            )
        except httpx.HTTPError as exc:
            raise BoardUnreachable(
                "hermes kanban dashboard is unreachable on loopback",
                details={"error": str(exc), "target": upstream_path},
            ) from exc

        if resp.status_code >= 400:
            try:
                detail: Any = resp.json()
            except ValueError:
                detail = {"body": resp.text[:500]}
            err = BoardUpstreamError(
                "hermes kanban returned an error",
                details={"upstream_status": resp.status_code, "upstream": detail},
            )
            if 400 <= resp.status_code < 500:
                err.status = resp.status_code
            raise err

        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {"raw": resp.text}

    async def aclose(self) -> None:
        if self._owns:
            await self._http.aclose()


__all__ = [
    "DEFAULT_BASE_URL",
    "KANBAN_BASE_PATH",
    "BoardUnreachable",
    "BoardUpstreamError",
    "HermesKanbanClient",
    "_default_agent_id",
    "_default_session_token",
]
