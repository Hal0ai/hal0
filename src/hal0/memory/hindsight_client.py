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
        entities=None,
        observation_scopes=None,
        strategy=None,
        update_mode=None,
        sync=False,
    ):
        """POST one item to ``/memories`` (RetainRequest, single-item shape).

        ``sync=True`` sends ``async: false`` — Hindsight waits for extraction
        to finish before responding (no ``operation_id``, but a ``usage``
        block for the extraction LLM call). ``sync=False`` (default) is the
        existing fire-and-forget background-ingest path.
        """
        item: dict[str, Any] = {"content": content, "document_id": document_id}
        if context is not None:
            item["context"] = context
        if metadata:
            item["metadata"] = metadata
        if tags:
            item["tags"] = list(tags)
        if timestamp is not None:
            item["timestamp"] = timestamp
        if entities is not None:
            item["entities"] = list(entities)
        if observation_scopes is not None:
            item["observation_scopes"] = observation_scopes
        if strategy is not None:
            item["strategy"] = strategy
        if update_mode is not None:
            item["update_mode"] = update_mode
        body: dict[str, Any] = {"items": [item], "async": not sync}
        resp = await self._http.post(
            f"/v1/default/banks/{bank_id}/memories", headers=self._headers(), json=body
        )
        resp.raise_for_status()
        return resp.json()

    async def recall(
        self,
        *,
        bank_id,
        query,
        types=None,
        max_tokens=4096,
        tags=None,
        tags_match=None,
        tag_groups=None,
        budget=None,
        prefer_observations=None,
        include=None,
        query_timestamp=None,
        min_scores=None,
    ):
        body: dict[str, Any] = {"query": query, "max_tokens": max_tokens}
        if types:
            body["types"] = list(types)
        if tags:
            body["tags"] = list(tags)
        if tags_match is not None:
            body["tags_match"] = tags_match
        if tag_groups is not None:
            body["tag_groups"] = tag_groups
        if budget is not None:
            body["budget"] = budget
        if prefer_observations is not None:
            body["prefer_observations"] = bool(prefer_observations)
        if include is not None:
            body["include"] = include
        if query_timestamp is not None:
            body["query_timestamp"] = query_timestamp
        if min_scores is not None:
            body["min_scores"] = min_scores
        resp = await self._http.post(
            f"/v1/default/banks/{bank_id}/memories/recall", headers=self._headers(), json=body
        )
        resp.raise_for_status()
        return resp.json()

    async def reflect(
        self,
        *,
        bank_id,
        query,
        budget=None,
        max_tokens=4096,
        include=None,
        response_schema=None,
        tags=None,
        tags_match=None,
        tag_groups=None,
        fact_types=None,
        exclude_mental_models=None,
        exclude_mental_model_ids=None,
    ):
        """POST ``/reflect`` — LLM-backed synthesis over the bank's memory."""
        body: dict[str, Any] = {"query": query, "max_tokens": max_tokens}
        if budget is not None:
            body["budget"] = budget
        if include is not None:
            body["include"] = include
        if response_schema is not None:
            body["response_schema"] = response_schema
        if tags:
            body["tags"] = list(tags)
        if tags_match is not None:
            body["tags_match"] = tags_match
        if tag_groups is not None:
            body["tag_groups"] = tag_groups
        if fact_types is not None:
            body["fact_types"] = list(fact_types)
        if exclude_mental_models is not None:
            body["exclude_mental_models"] = bool(exclude_mental_models)
        if exclude_mental_model_ids is not None:
            body["exclude_mental_model_ids"] = list(exclude_mental_model_ids)
        return await self.request_json(
            "POST", f"/v1/default/banks/{bank_id}/reflect", json_body=body
        )

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

    # ── single-memory curation (PATCH /memories/{memory_id}) ────────────────

    async def get_memory(self, *, bank_id, memory_id):
        mem = quote(str(memory_id), safe="")
        return await self.request_json("GET", f"/v1/default/banks/{bank_id}/memories/{mem}")

    async def update_memory(
        self,
        *,
        bank_id,
        memory_id,
        text=None,
        context=None,
        occurred_start=None,
        occurred_end=None,
        fact_type=None,
        entities=None,
        state=None,
        reason=None,
    ):
        """PATCH one memory unit — edit its text/context/occurred-range/
        fact_type/entities, or curate it via ``state`` (``"invalidated"`` to
        soft-retire, ``"valid"`` to revert — reversible either way).

        Every field is "omit to leave unchanged" per UpdateMemoryRequest, so
        only explicitly-set (non-``None``) fields are sent."""
        body: dict[str, Any] = {}
        if text is not None:
            body["text"] = text
        if context is not None:
            body["context"] = context
        if occurred_start is not None:
            body["occurred_start"] = occurred_start
        if occurred_end is not None:
            body["occurred_end"] = occurred_end
        if fact_type is not None:
            body["fact_type"] = fact_type
        if entities is not None:
            body["entities"] = list(entities)
        if state is not None:
            body["state"] = state
        if reason is not None:
            body["reason"] = reason
        mem = quote(str(memory_id), safe="")
        return await self.request_json(
            "PATCH", f"/v1/default/banks/{bank_id}/memories/{mem}", json_body=body
        )

    async def memory_history(self, *, bank_id, memory_id):
        mem = quote(str(memory_id), safe="")
        return await self.request_json("GET", f"/v1/default/banks/{bank_id}/memories/{mem}/history")

    # ── directives ────────────────────────────────────────────────────────

    async def list_directives(
        self, *, bank_id, tags=None, tags_match=None, active_only=None, limit=None, offset=None
    ):
        params: dict[str, Any] = {}
        if tags:
            params["tags"] = ",".join(tags)
        if tags_match is not None:
            params["tags_match"] = tags_match
        if active_only is not None:
            params["active_only"] = "true" if active_only else "false"
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        return await self.request_json(
            "GET", f"/v1/default/banks/{bank_id}/directives", params=params or None
        )

    async def get_directive(self, *, bank_id, directive_id):
        did = quote(str(directive_id), safe="")
        return await self.request_json("GET", f"/v1/default/banks/{bank_id}/directives/{did}")

    async def create_directive(
        self, *, bank_id, name, content, priority=0, is_active=True, tags=None
    ):
        body = {
            "name": name,
            "content": content,
            "priority": priority,
            "is_active": is_active,
            "tags": list(tags or []),
        }
        return await self.request_json(
            "POST", f"/v1/default/banks/{bank_id}/directives", json_body=body
        )

    async def update_directive(
        self,
        *,
        bank_id,
        directive_id,
        name=None,
        content=None,
        priority=None,
        is_active=None,
        tags=None,
    ):
        body: dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if content is not None:
            body["content"] = content
        if priority is not None:
            body["priority"] = priority
        if is_active is not None:
            body["is_active"] = is_active
        if tags is not None:
            body["tags"] = list(tags)
        did = quote(str(directive_id), safe="")
        return await self.request_json(
            "PATCH", f"/v1/default/banks/{bank_id}/directives/{did}", json_body=body
        )

    async def delete_directive(self, *, bank_id, directive_id):
        did = quote(str(directive_id), safe="")
        return await self.request_json("DELETE", f"/v1/default/banks/{bank_id}/directives/{did}")

    # ── mental models ─────────────────────────────────────────────────────

    async def list_mental_models(
        self, *, bank_id, tags=None, tags_match=None, detail=None, limit=None, offset=None
    ):
        params: dict[str, Any] = {}
        if tags:
            params["tags"] = ",".join(tags)
        if tags_match is not None:
            params["tags_match"] = tags_match
        if detail is not None:
            params["detail"] = detail
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        return await self.request_json(
            "GET", f"/v1/default/banks/{bank_id}/mental-models", params=params or None
        )

    async def get_mental_model(self, *, bank_id, mental_model_id):
        mid = quote(str(mental_model_id), safe="")
        return await self.request_json("GET", f"/v1/default/banks/{bank_id}/mental-models/{mid}")

    async def create_mental_model(
        self, *, bank_id, name, source_query, id=None, tags=None, max_tokens=2048, trigger=None
    ):
        body: dict[str, Any] = {
            "name": name,
            "source_query": source_query,
            "tags": list(tags or []),
            "max_tokens": max_tokens,
        }
        if id is not None:
            body["id"] = id
        if trigger is not None:
            body["trigger"] = trigger
        return await self.request_json(
            "POST", f"/v1/default/banks/{bank_id}/mental-models", json_body=body
        )

    async def update_mental_model(
        self,
        *,
        bank_id,
        mental_model_id,
        name=None,
        source_query=None,
        max_tokens=None,
        tags=None,
        trigger=None,
    ):
        body: dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if source_query is not None:
            body["source_query"] = source_query
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if tags is not None:
            body["tags"] = list(tags)
        if trigger is not None:
            body["trigger"] = trigger
        mid = quote(str(mental_model_id), safe="")
        return await self.request_json(
            "PATCH", f"/v1/default/banks/{bank_id}/mental-models/{mid}", json_body=body
        )

    async def delete_mental_model(self, *, bank_id, mental_model_id):
        mid = quote(str(mental_model_id), safe="")
        return await self.request_json("DELETE", f"/v1/default/banks/{bank_id}/mental-models/{mid}")

    async def refresh_mental_model(self, *, bank_id, mental_model_id):
        mid = quote(str(mental_model_id), safe="")
        return await self.request_json(
            "POST", f"/v1/default/banks/{bank_id}/mental-models/{mid}/refresh"
        )

    # ── async operations ─────────────────────────────────────────────────

    async def list_operations(
        self, *, bank_id, status=None, type=None, limit=None, offset=None, exclude_parents=None
    ):
        params: dict[str, Any] = {}
        if status is not None:
            params["status"] = status
        if type is not None:
            params["type"] = type
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        if exclude_parents is not None:
            params["exclude_parents"] = "true" if exclude_parents else "false"
        return await self.request_json(
            "GET", f"/v1/default/banks/{bank_id}/operations", params=params or None
        )

    async def get_operation(self, *, bank_id, operation_id, include_payload=None):
        params = {"include_payload": "true"} if include_payload else None
        oid = quote(str(operation_id), safe="")
        return await self.request_json(
            "GET", f"/v1/default/banks/{bank_id}/operations/{oid}", params=params
        )

    async def cancel_operation(self, *, bank_id, operation_id):
        oid = quote(str(operation_id), safe="")
        return await self.request_json("DELETE", f"/v1/default/banks/{bank_id}/operations/{oid}")

    async def retry_operation(self, *, bank_id, operation_id):
        oid = quote(str(operation_id), safe="")
        return await self.request_json(
            "POST", f"/v1/default/banks/{bank_id}/operations/{oid}/retry"
        )

    # ── tags / stats / consolidation ─────────────────────────────────────

    async def list_tags(self, *, bank_id, q=None, source=None, limit=None, offset=None):
        params: dict[str, Any] = {}
        if q is not None:
            params["q"] = q
        if source is not None:
            params["source"] = source
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        return await self.request_json(
            "GET", f"/v1/default/banks/{bank_id}/tags", params=params or None
        )

    async def bank_stats(self, *, bank_id, refresh=None):
        params = {"refresh": "true"} if refresh else None
        return await self.request_json("GET", f"/v1/default/banks/{bank_id}/stats", params=params)

    async def consolidate(self, *, bank_id):
        return await self.request_json("POST", f"/v1/default/banks/{bank_id}/consolidate")

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
