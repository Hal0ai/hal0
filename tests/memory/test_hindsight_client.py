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
