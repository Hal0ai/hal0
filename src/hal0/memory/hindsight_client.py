"""Async REST client for the shared hindsight-api (brain-redesign P1).

Talks to ``/v1/default/banks/{bank}/...`` (the bank-scoped REST surface the
spike confirmed). Auth is the single server-wide key when enabled; on the LAN
the daemon runs no-auth but Hindsight still requires a NON-EMPTY key, so we
default to a local-noauth placeholder.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:9177"  # dynamic port — pinned by the unit (P1-6)
DEFAULT_API_KEY = "hal0-local-noauth"

#: Boot-probe budget. This runs inside ``create_app`` on the synchronous
#: boot path, so it is a latency floor for every hal0-api start — keep it
#: well under a second of typical cost and bounded on the worst case. A
#: refused connection to loopback returns in microseconds; this ceiling
#: only matters for a hung or firewalled daemon.
DEFAULT_PROBE_TIMEOUT_S = 2.0
PROBE_TIMEOUT_ENV = "HAL0_HINDSIGHT_PROBE_TIMEOUT_S"


class HindsightUnreachable(RuntimeError):
    """The Hindsight daemon did not answer a health probe.

    Raised by :func:`probe_health` and caught by
    ``hal0.memory.provider_from_config``, which degrades to the in-memory
    ``PgVectorProvider`` rather than handing back a live-but-broken
    Hindsight client (#1301).
    """


def _probe_timeout() -> float:
    raw = os.environ.get(PROBE_TIMEOUT_ENV)
    if not raw:
        return DEFAULT_PROBE_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_PROBE_TIMEOUT_S
    return value if value > 0 else DEFAULT_PROBE_TIMEOUT_S


def probe_health(
    *,
    base_url: str | None = None,
    api_key: str = DEFAULT_API_KEY,
    timeout_s: float | None = None,
    transport: httpx.BaseTransport | None = None,
) -> None:
    """Synchronously confirm the daemon answers, or raise.

    Deliberately synchronous: the one caller is ``provider_from_config``,
    which runs on the sync boot path before an event loop exists. Hits
    ``/health`` — the same endpoint ``installer/install.sh`` waits on, so
    install-time and boot-time agree on what "up" means.

    Reachability and authorization are separate questions. A ``401``/``403``
    proves the daemon is up and answering, so it PASSES the probe; a wrong
    API key is a config error to surface at first use, not a reason to
    silently drop the durable engine. ``5xx`` fails — a daemon that is
    still starting cannot serve recalls either.

    Raises:
        HindsightUnreachable: on connect error, timeout, or a 5xx response.
    """
    url = (base_url or DEFAULT_BASE_URL).rstrip("/")
    budget = _probe_timeout() if timeout_s is None else timeout_s
    try:
        with httpx.Client(
            base_url=url,
            timeout=httpx.Timeout(budget, connect=budget),
            transport=transport,
        ) as http:
            resp = http.get("/health", headers={"Authorization": f"Bearer {api_key}"})
    except httpx.HTTPError as exc:
        raise HindsightUnreachable(f"{url}/health: {type(exc).__name__}: {exc}") from exc
    if resp.status_code >= 500:
        raise HindsightUnreachable(f"{url}/health returned HTTP {resp.status_code}")


class HindsightRestClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str = DEFAULT_API_KEY,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._api_key = api_key
        self._owns = http_client is None
        self._http = http_client or httpx.AsyncClient(
            base_url=self._base_url, timeout=httpx.Timeout(120.0, connect=3.0)
        )

    @classmethod
    def from_env(cls) -> HindsightRestClient:
        base = os.environ.get("HAL0_HINDSIGHT_URL", DEFAULT_BASE_URL)
        key = os.environ.get("HINDSIGHT_API_TENANT_API_KEY", DEFAULT_API_KEY) or DEFAULT_API_KEY
        return cls(base_url=base, api_key=key)

    @property
    def base_url(self) -> str:
        """The resolved daemon root — read-only; callers must not retarget
        a live client. Exposed so the boot probe reaches the same daemon
        this client will actually talk to."""
        return self._base_url

    @property
    def api_key(self) -> str:
        return self._api_key

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}

    async def retain(
        self,
        *,
        bank_id,
        content,
        document_id,
        context=None,
        metadata=None,
        tags=None,
        timestamp=None,
    ):
        item: dict[str, Any] = {"content": content, "document_id": document_id}
        if context is not None:
            item["context"] = context
        if metadata:
            item["metadata"] = metadata
        if tags:
            item["tags"] = list(tags)
        if timestamp is not None:
            item["timestamp"] = timestamp
        body: dict[str, Any] = {"items": [item], "async": True}
        resp = await self._http.post(
            f"/v1/default/banks/{bank_id}/memories", headers=self._headers(), json=body
        )
        resp.raise_for_status()
        return resp.json()

    async def recall(
        self, *, bank_id, query, types=None, max_tokens=4096, tags=None, tags_match=None
    ):
        body: dict[str, Any] = {"query": query, "max_tokens": max_tokens}
        if types:
            body["types"] = list(types)
        if tags:
            body["tags"] = list(tags)
        if tags_match is not None:
            body["tags_match"] = tags_match
        resp = await self._http.post(
            f"/v1/default/banks/{bank_id}/memories/recall", headers=self._headers(), json=body
        )
        resp.raise_for_status()
        return resp.json()

    async def list_memories(self, *, bank_id, limit=50, offset=0, types=None, query=None):
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if types:
            params["type"] = types if isinstance(types, str) else ",".join(types)
        if query:
            params["q"] = query
        resp = await self._http.get(
            f"/v1/default/banks/{bank_id}/memories/list",
            headers=self._headers(),
            params=params,
        )
        resp.raise_for_status()
        return resp.json()

    async def delete_document(self, *, bank_id, document_id):
        # document_id is the only user-influenced value that lands in a URL path
        # segment (e.g. the deterministic ``<agent>:<session_id>`` id). Percent-
        # encode it so a colon / other reserved char can't mangle the path;
        # Hindsight's ``{document_id:path}`` route + Starlette unquote it back.
        doc = quote(str(document_id), safe="")
        resp = await self._http.request(
            "DELETE",
            f"/v1/default/banks/{bank_id}/documents/{doc}",
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()

    async def request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
    ) -> Any:
        """Generic authenticated forward to any Hindsight REST path.

        The admin surface (/api/memory/banks/*) funnels its allowlisted
        passthrough through here so auth + base-url live in one place.
        Raises ``httpx.HTTPStatusError`` on non-2xx (callers map status).
        """
        resp = await self._http.request(
            method,
            path,
            headers=self._headers(),
            params=params,
            json=json_body,
        )
        resp.raise_for_status()
        if not resp.content:
            return {}
        return resp.json()

    async def aclose(self) -> None:
        if self._owns:
            await self._http.aclose()
