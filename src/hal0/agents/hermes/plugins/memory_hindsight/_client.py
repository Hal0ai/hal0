"""Thin synchronous REST client for the hal0-memory REST surface.

Design notes: (1) two-bank model via ``X-hal0-Private``, (2) agent-id
defaults to ``hermes``, (3) **synchronous** httpx — the Hermes memory hooks
are all sync, so an async+``asyncio.run`` wrapping buys nothing and breaks
on the 2nd call (one ``AsyncClient`` reused across per-call event loops →
"Event loop is closed"). A sync ``httpx.Client`` has no loop affinity and
is reused safely for the session.

Talks to hal0-api's ``/api/memory/*`` routes, NOT the ``/mcp/memory``
JSON-RPC transport. Identity is carried via ``X-hal0-Agent``;
there is no Bearer auth on the hal0 LAN.

Two banks, selected per write by ``X-hal0-Private``:
  * ``X-hal0-Agent: hermes`` + ``X-hal0-Private: 1`` → ``private:hermes``
  * ``X-hal0-Agent: hermes`` + ``X-hal0-Private: 0`` → ``shared``
Reads are a server-side UNION of both banks regardless of the flag.

#317 contract: this client NEVER sends a ``dataset`` field — the server
resolves the write target from the headers (PR #366).
"""

from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8080"
# hal0's registry calls this agent ``hermes``; the server derives the bank
# name from it (``private:<id>``), so this IS the private bank name.
DEFAULT_AGENT_ID = "hermes"
DEFAULT_CONNECT_TIMEOUT = 3.0
DEFAULT_READ_TIMEOUT = 30.0  # recall/extraction can take a while on the iGPU
# Reflect is an LLM loop inside the engine and runs for minutes on local
# hardware. hal0-api gives the engine ``HAL0_MEMORY_REFLECT_TIMEOUT_S`` seconds
# (default 900, ``hal0.memory.hindsight_client.REFLECT_TIMEOUT_S``); this client
# waits that long plus a margin, so hal0-api's own answer arrives first. Setting
# the same variable in the Hermes environment keeps the two in step.
DEFAULT_REFLECT_TIMEOUT = 900.0
REFLECT_TIMEOUT_MARGIN = 30.0


class Hal0MemoryClientError(RuntimeError):
    """Raised when a hal0-memory REST call fails.

    Wraps the upstream status code + response body. We intentionally do NOT
    import ``agent.memory_provider`` here so this module stays importable
    inside hal0's own venv for unit testing.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _loopback_client_kwargs(url: str) -> dict[str, Any]:
    """httpx kwargs that keep a loopback ``url`` off HTTP_PROXY/ALL_PROXY (#2526).

    Defence in depth: this plugin runs inside Hermes and cannot import hal0,
    so the host check is stdlib-only (a copy of ``hal0.service_identity.
    is_loopback_url``). A loopback target gets ``trust_env=False`` and a CA
    context built with the environment honoured, so ``SSL_CERT_FILE`` still
    applies. Any other target keeps httpx's default environment handling.
    """
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return {}
    if parts.username is not None or host not in _LOOPBACK_HOSTS:
        return {}
    return {"trust_env": False, "verify": httpx.create_ssl_context(trust_env=True)}


def _resolve_base_url(override: str | None) -> str:
    if override:
        return override.rstrip("/")
    return os.environ.get("HAL0_MEMORY_BASE", DEFAULT_BASE_URL).rstrip("/")


def _resolve_agent_id(override: str | None) -> str:
    if override:
        return override
    return os.environ.get("HAL0_AGENT_ID", DEFAULT_AGENT_ID)


def _reflect_read_timeout() -> float:
    """Seconds to wait for a reflect answer; a bad or non-positive value means the default."""
    try:
        budget = float(os.environ.get("HAL0_MEMORY_REFLECT_TIMEOUT_S") or DEFAULT_REFLECT_TIMEOUT)
    except ValueError:
        budget = DEFAULT_REFLECT_TIMEOUT
    if budget <= 0:
        budget = DEFAULT_REFLECT_TIMEOUT
    return budget + REFLECT_TIMEOUT_MARGIN


class Hal0MemoryClient:
    """Synchronous REST client for hal0-memory.

    Instantiated once per ``initialize()`` and reused for the session.
    ``close()`` is idempotent.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        agent_id: str | None = None,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        read_timeout: float = DEFAULT_READ_TIMEOUT,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._base_url = _resolve_base_url(base_url)
        self._agent_id = _resolve_agent_id(agent_id)
        self._connect_timeout = connect_timeout
        self._owns_client = http_client is None
        if http_client is None:
            timeout = httpx.Timeout(read_timeout, connect=connect_timeout)
            self._client = httpx.Client(
                base_url=self._base_url,
                timeout=timeout,
                **_loopback_client_kwargs(self._base_url),
            )
        else:
            self._client = http_client

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def agent_id(self) -> str:
        return self._agent_id

    def _headers(self, *, private: bool) -> dict[str, str]:
        return {
            "X-hal0-Agent": self._agent_id,
            "X-hal0-Private": "1" if private else "0",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    # ── REST verbs ─────────────────────────────────────────────────────

    def add(
        self,
        text: str,
        *,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        private: bool = True,
        document_id: str | None = None,
        append_document_id: str | None = None,
        observation_scopes: Any = None,
    ) -> dict[str, Any]:
        """POST /api/memory/add. ``private=True`` → hermes-private, else shared.

        ``document_id`` is a plain upsert: the engine REPLACES the document it
        already holds under that id. Right for one fact revised in place, and
        never to be used for an id that many writes share.

        ``append_document_id`` is for that case: the text is added to the
        document. It travels as its own field, and this method refuses to send
        both, because a hal0-api from before append mode reads ``document_id``
        and replaces, while it ignores a field it does not know and stores the
        text under a fresh id. The response carries ``update_mode: "append"``
        when the write really was appended.
        """
        if document_id is not None and append_document_id is not None:
            raise ValueError("document_id and append_document_id are mutually exclusive")
        payload: dict[str, Any] = {"text": text}
        if tags is not None:
            payload["tags"] = list(tags)
        if metadata is not None:
            payload["metadata"] = dict(metadata)
        if document_id is not None:
            payload["document_id"] = document_id
        if append_document_id is not None:
            payload["append_document_id"] = append_document_id
        if observation_scopes is not None:
            payload["observation_scopes"] = observation_scopes
        return self._request("POST", "/api/memory/add", json=payload, private=private)

    def search(self, query: str, *, limit: int = 10) -> dict[str, Any]:
        """POST /api/memory/search — semantic retrieval (union of both banks)."""
        return self._request(
            "POST", "/api/memory/search", json={"query": query, "limit": int(limit)}, private=True
        )

    def recall(
        self, query: str, *, types: list[str] | None = None, max_tokens: int = 4096
    ) -> dict[str, Any]:
        """POST /api/memory/recall — token-budgeted consolidated recall (union)."""
        payload: dict[str, Any] = {"query": query, "max_tokens": int(max_tokens)}
        if types is not None:
            payload["types"] = list(types)
        return self._request("POST", "/api/memory/recall", json=payload, private=True)

    def reflect(
        self,
        query: str,
        *,
        budget: str = "low",
        max_tokens: int = 4096,
        tags: list[str] | None = None,
        tags_match: str | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """POST /api/memory/reflect — the engine's LLM synthesis over memory.

        Slow: the engine runs an agentic loop, so this waits minutes, not the
        30 s the other verbs get. Sent without the private toggle, so it reads
        the shared namespace: under hal0's unified bank that includes this
        agent's own private notes (other agents' are filtered out by hal0-api);
        with split banks it is the shared bank alone.
        """
        payload: dict[str, Any] = {
            "query": query,
            "budget": budget,
            "max_tokens": int(max_tokens),
        }
        if tags:
            payload["tags"] = list(tags)
        if tags_match is not None:
            payload["tags_match"] = tags_match
        if response_schema is not None:
            payload["response_schema"] = dict(response_schema)
        return self._request(
            "POST",
            "/api/memory/reflect",
            json=payload,
            private=False,
            read_timeout=_reflect_read_timeout(),
        )

    def list_items(self, *, limit: int = 50) -> dict[str, Any]:
        """GET /api/memory/list — page through stored items."""
        return self._request("GET", "/api/memory/list", params={"limit": int(limit)}, private=True)

    def delete(self, item_id: str) -> dict[str, Any]:
        """POST /api/memory/delete — remove memory items by id."""
        return self._request("POST", "/api/memory/delete", json={"ids": [item_id]}, private=True)

    # ── transport core ─────────────────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        private: bool = True,
        read_timeout: float | None = None,
    ) -> dict[str, Any]:
        # A per-call timeout is passed only when a verb asks for one, so the
        # client-wide default (and an injected client's) is otherwise untouched.
        extra: dict[str, Any] = {}
        if read_timeout is not None:
            extra["timeout"] = httpx.Timeout(read_timeout, connect=self._connect_timeout)
        try:
            response = self._client.request(
                method,
                path,
                headers=self._headers(private=private),
                json=json,
                params=params,
                **extra,
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
        return data if isinstance(data, dict) else {"status": "ok", "raw": data}
