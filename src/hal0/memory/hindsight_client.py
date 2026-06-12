"""Async REST client for the shared hindsight-api (brain-redesign P1).

Talks to ``/v1/default/banks/{bank}/...`` (the bank-scoped REST surface the
spike confirmed). Auth is the single server-wide key when enabled; on the LAN
the daemon runs no-auth but Hindsight still requires a NON-EMPTY key, so we
default to a local-noauth placeholder.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:9177"  # dynamic port — pinned by the unit (P1-6)
DEFAULT_API_KEY = "hal0-local-noauth"


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

    async def recall(self, *, bank_id, query, types=None, max_tokens=4096, tags=None):
        body: dict[str, Any] = {"query": query, "max_tokens": max_tokens}
        if types:
            body["types"] = list(types)
        if tags:
            body["tags"] = list(tags)
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
        resp = await self._http.request(
            "DELETE",
            f"/v1/default/banks/{bank_id}/documents/{document_id}",
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
