"""Integration tests for the ``/api/agent/approvals`` REST surface.

The orchestrator hasn't wired the router into ``create_app()`` yet
(other team's responsibility), so we mount it on a bare FastAPI app
with an in-memory :class:`ApprovalQueue` on ``app.state``. That keeps
the test focused on the route ⇄ queue contract without dragging the
full hal0 lifespan in.

End-to-end flow exercised:

  1. Seed the queue with a gated tool call (executor mocked).
  2. ``GET /api/agent/approvals`` lists the pending entry.
  3. ``POST /api/agent/approvals/{id}/approve`` runs the executor and
     returns ``executed`` state.
  4. ``POST /api/agent/approvals/{id}/deny`` on a fresh entry skips
     the executor and returns ``denied`` state.
  5. Double-resolve returns a typed 409, unknown id returns 404.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api.middleware import error_codes
from hal0.api.routes import approvals as approvals_routes
from hal0.mcp.approval_queue import ApprovalQueue


def _build_app() -> FastAPI:
    app = FastAPI()
    error_codes.install(app)
    app.include_router(approvals_routes.router, prefix="/api/agent/approvals", tags=["approvals"])
    app.state.approval_queue = ApprovalQueue()
    return app


@pytest.fixture
def app() -> FastAPI:
    return _build_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def queue(app: FastAPI) -> ApprovalQueue:
    return app.state.approval_queue  # type: ignore[no-any-return]


async def _noop_executor(args: dict[str, Any]) -> dict[str, Any]:
    return {"ran": True, "args": args}


def test_list_pending_empty_returns_empty_list(client: TestClient) -> None:
    response = client.get("/api/agent/approvals")
    assert response.status_code == 200
    assert response.json() == {"approvals": []}


def test_list_pending_returns_enqueued_entries(client: TestClient, queue: ApprovalQueue) -> None:
    import asyncio

    aid = asyncio.run(
        queue.enqueue(
            tool="model_pull",
            args={"model_id": "qwen3:0.6b"},
            client_id="pi",
            executor=_noop_executor,
        )
    )
    response = client.get("/api/agent/approvals")
    assert response.status_code == 200
    body = response.json()
    assert len(body["approvals"]) == 1
    assert body["approvals"][0]["id"] == aid
    assert body["approvals"][0]["tool"] == "model_pull"


def test_approve_runs_executor(client: TestClient, queue: ApprovalQueue) -> None:
    import asyncio

    captured: dict[str, Any] = {}

    async def _exec(args: dict[str, Any]) -> dict[str, Any]:
        captured.update(args)
        return {"ok": True}

    aid = asyncio.run(
        queue.enqueue(
            tool="slot_delete",
            args={"name": "scratch"},
            client_id="pi",
            executor=_exec,
        )
    )
    response = client.post(f"/api/agent/approvals/{aid}/approve")
    assert response.status_code == 200
    body = response.json()["approval"]
    assert body["state"] == "executed"
    assert body["result"] == {"ok": True}
    assert captured == {"name": "scratch"}
    # After approval the queue no longer lists it pending.
    assert client.get("/api/agent/approvals").json() == {"approvals": []}


def test_deny_does_not_run_executor(client: TestClient, queue: ApprovalQueue) -> None:
    import asyncio

    calls: list[dict[str, Any]] = []

    async def _exec(args: dict[str, Any]) -> dict[str, Any]:
        calls.append(args)
        return {"ok": True}

    aid = asyncio.run(
        queue.enqueue(
            tool="model_delete",
            args={"model_id": "x"},
            client_id="pi",
            executor=_exec,
        )
    )
    response = client.post(f"/api/agent/approvals/{aid}/deny")
    assert response.status_code == 200
    body = response.json()["approval"]
    assert body["state"] == "denied"
    assert calls == []


def test_approve_unknown_id_returns_404(client: TestClient) -> None:
    response = client.post("/api/agent/approvals/nope/approve")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "approvals.not_found"


def test_double_approve_returns_409(client: TestClient, queue: ApprovalQueue) -> None:
    import asyncio

    aid = asyncio.run(
        queue.enqueue(
            tool="slot_restart",
            args={"name": "primary"},
            client_id="pi",
            executor=_noop_executor,
        )
    )
    first = client.post(f"/api/agent/approvals/{aid}/approve")
    assert first.status_code == 200
    second = client.post(f"/api/agent/approvals/{aid}/approve")
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "approvals.already_resolved"


def test_unavailable_queue_returns_503() -> None:
    """When app.state.approval_queue is absent, the dependency 503s."""
    app = FastAPI()
    error_codes.install(app)
    app.include_router(approvals_routes.router, prefix="/api/agent/approvals", tags=["approvals"])
    # Deliberately do NOT set app.state.approval_queue.
    with TestClient(app) as client:
        response = client.get("/api/agent/approvals")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "approvals.unavailable"
