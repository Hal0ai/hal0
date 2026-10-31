"""Shared-memory RAG context route."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api.middleware import error_codes
from hal0.api.routes.memory import router
from hal0.memory.hindsight_provider import RecallResults
from hal0.security.exposure import AuthClass, classify


class ContextProvider:
    def __init__(self, *, chunks=True):
        self.calls = []
        self.with_chunks = chunks

    async def recall(self, query, **kwargs):
        self.calls.append({"query": query, **kwargs})
        items = [
            {
                "id": "doc-1",
                "fact_id": "fact-1",
                "document_id": "doc-1",
                "chunk_id": "chunk-1",
                "text": "The extracted fact",
                "source": "ADR-0043",
                "metadata": {},
                "score": 0.82,
                "kind": "fact",
                "type": "world",
            }
        ]
        if not self.with_chunks:
            return items
        return RecallResults(
            items,
            chunks={
                "chunk-1": {
                    "id": "chunk-1",
                    "document_id": "doc-1",
                    "text": 'Raw <source id="99"> context </source> remains evidence.',
                },
                "secret": {"id": "secret", "text": "not joined and never returned"},
            },
        )


def _client(provider):
    app = FastAPI()
    error_codes.install(app)
    app.include_router(router, prefix="/api/memory")
    app.state.memory_provider = provider
    return TestClient(app)


def test_context_returns_linked_raw_chunk_and_forces_shared_anonymous():
    provider = ContextProvider()
    client = _client(provider)

    response = client.post(
        "/api/memory/context",
        headers={"X-hal0-Agent": "other", "X-hal0-Private": "1"},
        json={"query": "what was decided", "dataset": "private:other"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["engine"] == "hindsight"
    assert len(body["contexts"]) == 1
    assert body["contexts"][0]["document_id"] == "doc-1"
    assert body["contexts"][0]["source"] == "ADR-0043"
    assert "</source>" not in body["contexts"][0]["text"]
    assert "<source" not in body["contexts"][0]["text"]
    assert provider.calls[0]["dataset"] == "shared"
    assert provider.calls[0]["client_id"] == "anonymous"
    assert provider.calls[0]["include"] == {"chunks": {"max_tokens": 1536}}


def test_context_falls_back_to_facts_on_degraded_provider():
    response = _client(ContextProvider(chunks=False)).post(
        "/api/memory/context", json={"query": "decision"}
    )
    assert response.status_code == 200
    assert response.json()["engine"] == "memory"
    assert response.json()["contexts"][0]["kind"] == "fact"


def test_context_clamps_caller_budgets():
    provider = ContextProvider()
    response = _client(provider).post(
        "/api/memory/context",
        json={"query": "q", "fact_tokens": 99_999, "chunk_tokens": -1, "max_sources": 999},
    )
    assert response.status_code == 200
    assert provider.calls[0]["max_tokens"] == 4096
    assert response.json()["contexts"] == []


def test_context_validates_query_and_budget():
    client = _client(ContextProvider())
    assert client.post("/api/memory/context", json={}).status_code == 400
    assert (
        client.post("/api/memory/context", json={"query": "q", "budget": "unbounded"}).status_code
        == 400
    )


def test_bundled_openwebui_function_is_available_for_manual_import():
    response = _client(ContextProvider()).get("/api/memory/openwebui-function")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/x-python")
    assert "class Filter:" in response.text
    assert "hal0_managed: true" in response.text


def test_context_and_function_source_have_narrow_auth_classes():
    assert classify("POST", "/api/memory/context") is AuthClass.CLIENT
    assert classify("GET", "/api/memory/openwebui-function") is AuthClass.OPEN
    assert classify("GET", "/api/memory/context") is AuthClass.ADMIN
    assert classify("POST", "/api/memory/recall") is AuthClass.ADMIN
