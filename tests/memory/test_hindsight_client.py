"""HindsightRestClient REST-path tests against a MockTransport (P1)."""

from __future__ import annotations

import httpx
import pytest

from hal0.memory.hindsight_client import HindsightRestClient


@pytest.mark.asyncio
async def test_retain_recall_delete_hit_v1_bank_paths():
    seen: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path.endswith("/recall"):
            return httpx.Response(200, json={"results": []})
        if request.url.path.endswith("/memories"):
            return httpx.Response(
                200, json={"success": True, "bank_id": "shared", "items_count": 1}
            )
        return httpx.Response(200, json={"memory_units_deleted": 1})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9177") as http:
        client = HindsightRestClient(http_client=http, api_key="hal0-local-noauth")
        await client.retain(bank_id="shared", content="x", document_id="d1")
        await client.recall(bank_id="shared", query="x")
        await client.delete_document(bank_id="shared", document_id="d1")

    assert ("POST", "/v1/default/banks/shared/memories") in seen
    assert ("POST", "/v1/default/banks/shared/memories/recall") in seen
    # Delete is the documented delete_document path.
    assert any(m == "DELETE" and "/documents/d1" in p for m, p in seen)


@pytest.mark.asyncio
async def test_delete_document_percent_encodes_id_in_path():
    """A deterministic ``<agent>:<session_id>`` document id (colon) must be
    percent-encoded in the DELETE URL path so reserved chars can't mangle it."""
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.raw_path.decode())
        return httpx.Response(200, json={"memory_units_deleted": 1})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9177") as http:
        client = HindsightRestClient(http_client=http, api_key="hal0-local-noauth")
        await client.delete_document(bank_id="shared", document_id="hermes:s7")

    assert seen == ["/v1/default/banks/shared/documents/hermes%3As7"]


@pytest.mark.asyncio
async def test_list_memories_hits_list_endpoint_and_returns_json():
    seen: list[tuple[str, str]] = []
    payload = {
        "items": [
            {
                "id": "fact-1",
                "text": "Alice works at Google",
                "fact_type": "observation",
                "mentioned_at": "2026-06-06T00:00:00+00:00",
                "tags": ["work"],
            }
        ],
        "total": 1,
        "limit": 50,
        "offset": 0,
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.method == "GET" and request.url.path.endswith("/memories/list"):
            return httpx.Response(200, json=payload)
        return httpx.Response(404, json={"error": "not found"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9177") as http:
        client = HindsightRestClient(http_client=http, api_key="hal0-local-noauth")
        result = await client.list_memories(bank_id="shared", limit=50, offset=0)

    assert ("GET", "/v1/default/banks/shared/memories/list") in seen
    assert result == payload
    assert result["items"][0]["id"] == "fact-1"


@pytest.mark.asyncio
async def test_request_json_generic_forward_carries_auth_params_and_body():
    seen: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = request.content.decode() if request.content else ""
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9177") as http:
        client = HindsightRestClient(http_client=http, api_key="hal0-local-noauth")
        out = await client.request_json(
            "POST",
            "/v1/default/banks/shared/memories/recall",
            params={"limit": 5},
            json_body={"query": "q"},
        )

    assert out == {"ok": True}
    assert seen["method"] == "POST"
    assert seen["path"] == "/v1/default/banks/shared/memories/recall"
    assert seen["params"] == {"limit": "5"}
    assert seen["auth"] == "Bearer hal0-local-noauth"
    assert '"query"' in seen["body"]


# ── retain/recall modernization ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_retain_full_item_shape_and_sync_flag():
    seen: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={"success": True, "bank_id": "shared", "items_count": 1})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9177") as http:
        client = HindsightRestClient(http_client=http, api_key="hal0-local-noauth")
        await client.retain(
            bank_id="shared",
            content="x",
            document_id="d1",
            entities=[{"text": "Alice"}],
            observation_scopes="shared",
            strategy="fast",
            update_mode="append",
            sync=True,
        )

    body = seen["body"]
    assert body["async"] is False  # sync=True -> async: false
    item = body["items"][0]
    assert item["entities"] == [{"text": "Alice"}]
    assert item["observation_scopes"] == "shared"
    assert item["strategy"] == "fast"
    assert item["update_mode"] == "append"


@pytest.mark.asyncio
async def test_retain_defaults_stay_async_and_omit_unset_fields():
    seen: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={"success": True, "bank_id": "shared", "items_count": 1})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9177") as http:
        client = HindsightRestClient(http_client=http, api_key="hal0-local-noauth")
        await client.retain(bank_id="shared", content="x", document_id="d1")

    body = seen["body"]
    assert body["async"] is True  # unchanged default behaviour
    item = body["items"][0]
    assert "entities" not in item
    assert "observation_scopes" not in item
    assert "strategy" not in item
    assert "update_mode" not in item


@pytest.mark.asyncio
async def test_recall_forwards_extended_knobs():
    seen: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={"results": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9177") as http:
        client = HindsightRestClient(http_client=http, api_key="hal0-local-noauth")
        await client.recall(
            bank_id="shared",
            query="q",
            tag_groups=[{"tags": ["a"], "match": "any"}],
            budget="high",
            prefer_observations=True,
            include={"chunks": {}},
            query_timestamp="2026-01-01T00:00:00",
            min_scores={"final": 0.2},
        )

    body = seen["body"]
    assert body["tag_groups"] == [{"tags": ["a"], "match": "any"}]
    assert body["budget"] == "high"
    assert body["prefer_observations"] is True
    assert body["include"] == {"chunks": {}}
    assert body["query_timestamp"] == "2026-01-01T00:00:00"
    assert body["min_scores"] == {"final": 0.2}


@pytest.mark.asyncio
async def test_recall_omits_unset_extended_knobs():
    seen: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={"results": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9177") as http:
        client = HindsightRestClient(http_client=http, api_key="hal0-local-noauth")
        await client.recall(bank_id="shared", query="q")

    body = seen["body"]
    for key in (
        "tag_groups",
        "budget",
        "prefer_observations",
        "include",
        "query_timestamp",
        "min_scores",
    ):
        assert key not in body


# ── reflect ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reflect_posts_to_reflect_endpoint():
    seen: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={"text": "an answer"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9177") as http:
        client = HindsightRestClient(http_client=http, api_key="hal0-local-noauth")
        out = await client.reflect(bank_id="shared", query="what do you know?", budget="low")

    assert seen["path"] == "/v1/default/banks/shared/reflect"
    assert seen["body"]["query"] == "what do you know?"
    assert seen["body"]["budget"] == "low"
    assert out == {"text": "an answer"}


# ── single-memory curation ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_update_and_history_hit_memory_id_paths():
    seen: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path.endswith("/history"):
            return httpx.Response(200, json={"items": []})
        return httpx.Response(200, json={"id": "fact-1"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9177") as http:
        client = HindsightRestClient(http_client=http, api_key="hal0-local-noauth")
        await client.get_memory(bank_id="shared", memory_id="fact-1")
        await client.update_memory(bank_id="shared", memory_id="fact-1", state="invalidated")
        await client.memory_history(bank_id="shared", memory_id="fact-1")

    assert ("GET", "/v1/default/banks/shared/memories/fact-1") in seen
    assert ("PATCH", "/v1/default/banks/shared/memories/fact-1") in seen
    assert ("GET", "/v1/default/banks/shared/memories/fact-1/history") in seen


@pytest.mark.asyncio
async def test_update_memory_only_sends_explicitly_set_fields():
    seen: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9177") as http:
        client = HindsightRestClient(http_client=http, api_key="hal0-local-noauth")
        await client.update_memory(bank_id="shared", memory_id="fact-1", reason="typo")

    assert seen["body"] == {"reason": "typo"}


# ── directives ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_directive_crud_hits_expected_paths():
    seen: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        return httpx.Response(200, json={"items": []} if request.method == "GET" else {"id": "d1"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9177") as http:
        client = HindsightRestClient(http_client=http, api_key="hal0-local-noauth")
        await client.list_directives(bank_id="shared")
        await client.get_directive(bank_id="shared", directive_id="d1")
        await client.create_directive(bank_id="shared", name="n", content="c")
        await client.update_directive(bank_id="shared", directive_id="d1", content="c2")
        await client.delete_directive(bank_id="shared", directive_id="d1")

    assert ("GET", "/v1/default/banks/shared/directives") in seen
    assert ("GET", "/v1/default/banks/shared/directives/d1") in seen
    assert ("POST", "/v1/default/banks/shared/directives") in seen
    assert ("PATCH", "/v1/default/banks/shared/directives/d1") in seen
    assert ("DELETE", "/v1/default/banks/shared/directives/d1") in seen


# ── mental models ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mental_model_crud_and_refresh_hits_expected_paths():
    seen: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        return httpx.Response(
            200, json={"items": []} if request.method == "GET" else {"operation_id": "op1"}
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9177") as http:
        client = HindsightRestClient(http_client=http, api_key="hal0-local-noauth")
        await client.list_mental_models(bank_id="shared")
        await client.get_mental_model(bank_id="shared", mental_model_id="mm1")
        await client.create_mental_model(bank_id="shared", name="n", source_query="q")
        await client.update_mental_model(bank_id="shared", mental_model_id="mm1", name="n2")
        await client.delete_mental_model(bank_id="shared", mental_model_id="mm1")
        await client.refresh_mental_model(bank_id="shared", mental_model_id="mm1")

    assert ("GET", "/v1/default/banks/shared/mental-models") in seen
    assert ("GET", "/v1/default/banks/shared/mental-models/mm1") in seen
    assert ("POST", "/v1/default/banks/shared/mental-models") in seen
    assert ("PATCH", "/v1/default/banks/shared/mental-models/mm1") in seen
    assert ("DELETE", "/v1/default/banks/shared/mental-models/mm1") in seen
    assert ("POST", "/v1/default/banks/shared/mental-models/mm1/refresh") in seen


# ── async operations ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_operation_crud_hits_expected_paths():
    seen: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        return httpx.Response(
            200, json={"operations": []} if request.method == "GET" else {"operation_id": "op1"}
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9177") as http:
        client = HindsightRestClient(http_client=http, api_key="hal0-local-noauth")
        await client.list_operations(bank_id="shared")
        await client.get_operation(bank_id="shared", operation_id="op1")
        await client.cancel_operation(bank_id="shared", operation_id="op1")
        await client.retry_operation(bank_id="shared", operation_id="op1")

    assert ("GET", "/v1/default/banks/shared/operations") in seen
    assert ("GET", "/v1/default/banks/shared/operations/op1") in seen
    assert ("DELETE", "/v1/default/banks/shared/operations/op1") in seen
    assert ("POST", "/v1/default/banks/shared/operations/op1/retry") in seen


# ── tags / bank stats / consolidation ────────────────────────────────────


@pytest.mark.asyncio
async def test_tags_stats_and_consolidate_hit_expected_paths():
    seen: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        return httpx.Response(
            200,
            json={"items": [], "total": 0} if "tags" in request.url.path else {"bank_id": "shared"},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9177") as http:
        client = HindsightRestClient(http_client=http, api_key="hal0-local-noauth")
        await client.list_tags(bank_id="shared")
        await client.bank_stats(bank_id="shared")
        await client.consolidate(bank_id="shared")

    assert ("GET", "/v1/default/banks/shared/tags") in seen
    assert ("GET", "/v1/default/banks/shared/stats") in seen
    assert ("POST", "/v1/default/banks/shared/consolidate") in seen
