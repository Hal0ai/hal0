"""POST /api/memory/recall route (P2)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api.middleware import error_codes
from hal0.api.routes.memory import router
from tests.memory.fakes import FakeMemoryProvider


class RecordingProvider(FakeMemoryProvider):
    def __init__(self):
        super().__init__(client_id="anonymous")
        self.recall_calls = []

    async def recall(
        self,
        query,
        *,
        types=None,
        max_tokens=4096,
        dataset="shared",
        tags=None,
        tags_match=None,
        client_id=None,
    ):
        self.recall_calls.append({"query": query, "max_tokens": max_tokens})
        return [
            {
                "id": "d1",
                "text": "recalled",
                "timestamp": "2026-06-06T00:00:00+00:00",
                "dataset": "shared",
                "tags": [],
                "source": None,
                "metadata": {},
                "score": None,
            }
        ]


@pytest.fixture
def client():
    app = FastAPI()
    error_codes.install(app)
    app.include_router(router, prefix="/api/memory")
    app.state.memory_provider = RecordingProvider()
    return TestClient(app)


def test_recall_route_returns_items(client):
    resp = client.post(
        "/api/memory/recall",
        json={"query": "what do I know", "max_tokens": 2048},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"][0]["text"] == "recalled"
    assert client.app.state.memory_provider.recall_calls[0]["max_tokens"] == 2048


def test_recall_requires_query(client):
    resp = client.post("/api/memory/recall", json={})
    assert resp.status_code == 400
