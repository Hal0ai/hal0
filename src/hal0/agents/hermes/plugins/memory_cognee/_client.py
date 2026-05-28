"""Thin async REST client for the hal0-memory REST surface.

Talks to hal0-api's ``/api/memory/*`` routes, NOT the ``/mcp/memory``
JSON-RPC transport. Identity is carried via ``X-hal0-Agent`` per
ADR-0012 + PR #268 — there is no Bearer auth on the hal0 LAN.

#317 contract: this client NEVER sends a ``dataset`` field. The server
resolves the write target from ``X-hal0-Agent`` (see PR #366
``resolve_write_dataset``). Sending an explicit ``private:<id>`` here
re-trips the ``_AGENT_ID_PATTERN`` reject that the old
``installer/agents/hermes/plugins/hal0-memory/__init__.py:117`` stub
caused.

The client is intentionally tiny — no retries, no circuit breaker. The
Hermes agent loop already wraps memory hooks in best-effort try/except;
re-implementing that here would just double the failure modes.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8080"
DEFAULT_AGENT_ID = "hermes-agent"
DEFAULT_CONNECT_TIMEOUT = 3.0
DEFAULT_READ_TIMEOUT = 10.0


class Hal0MemoryClientError(RuntimeError):
    """Raised when a hal0-memory REST call fails.

    Wraps the upstream status code + response body so callers can either
    swallow the error (best-effort memory hooks) or surface it via the
    upstream ``MemoryProviderError`` taxonomy. We intentionally do NOT
    import ``agent.memory_provider`` here so this module stays
    importable inside hal0's own venv for unit testing.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _resolve_base_url(override: str | None) -> str:
    if override:
        return override.rstrip("/")
    raw = os.environ.get("HAL0_MEMORY_BASE", DEFAULT_BASE_URL)
    return raw.rstrip("/")


def _resolve_agent_id(override: str | None) -> str:
    if override:
        return override
    return os.environ.get("HAL0_AGENT_ID", DEFAULT_AGENT_ID)


class Hal0MemoryClient:
    """Async REST client for hal0-memory.

    Designed to be instantiated once per ``initialize()`` call and reused
    for the lifetime of the agent session. ``aclose()`` is idempotent.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        agent_id: str | None = None,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        read_timeout: float = DEFAULT_READ_TIMEOUT,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = _resolve_base_url(base_url)
        self._agent_id = _resolve_agent_id(agent_id)
        self._owns_client = http_client is None
        if http_client is None:
            timeout = httpx.Timeout(read_timeout, connect=connect_timeout)
            self._client = httpx.AsyncClient(base_url=self._base_url, timeout=timeout)
        else:
            self._client = http_client

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def agent_id(self) -> str:
        return self._agent_id

    def _headers(self) -> dict[str, str]:
        return {
            "X-hal0-Agent": self._agent_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ── REST verbs ─────────────────────────────────────────────────────

    async def add(
        self,
        text: str,
        *,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """POST /api/memory/add — record a memory item.

        Omits the ``dataset`` key intentionally; the server resolves it
        from ``X-hal0-Agent``. See module docstring for the #317 contract.
        """
        payload: dict[str, Any] = {"text": text}
        if tags is not None:
            payload["tags"] = list(tags)
        if metadata is not None:
            payload["metadata"] = dict(metadata)
        return await self._request("POST", "/api/memory/add", json=payload)

    async def search(self, query: str, *, limit: int = 5) -> dict[str, Any]:
        """POST /api/memory/search — semantic retrieval.

        Omits ``dataset`` by design (see ``add``).
        """
        payload = {"query": query, "limit": int(limit)}
        return await self._request("POST", "/api/memory/search", json=payload)

    async def list_items(self, *, limit: int = 50) -> dict[str, Any]:
        """GET /api/memory — page through stored items.

        ``limit`` is forwarded as a query parameter so the server can
        cap the response; pagination cursors are an upstream concern.
        """
        return await self._request("GET", "/api/memory", params={"limit": int(limit)})

    async def delete(self, item_id: str) -> dict[str, Any]:
        """DELETE /api/memory/{id} — remove a single memory item."""
        return await self._request("DELETE", f"/api/memory/{item_id}")

    # ── transport core ─────────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = await self._client.request(
                method,
                path,
                headers=self._headers(),
                json=json,
                params=params,
            )
        except httpx.HTTPError as exc:
            raise Hal0MemoryClientError(
                f"hal0-memory transport failure on {method} {path}: {exc}",
            ) from exc

        if response.status_code >= 400:
            raise Hal0MemoryClientError(
                f"hal0-memory {method} {path} returned {response.status_code}: {response.text[:200]}",
                status_code=response.status_code,
            )

        try:
            data = response.json()
        except ValueError:
            return {"status": "ok", "raw": response.text}
        if isinstance(data, dict):
            return data
        return {"status": "ok", "raw": data}
