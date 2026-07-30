"""The operator surface for a degraded-at-boot box.

Automatic re-promotion (``hal0.memory.degrade``) is the normal path, but two
situations need a human-driven one: an operator who has just started
hindsight-api and does not want to wait out the re-probe interval, and a box
with automatic promotion disabled, where this route is the ONLY way back to
durable storage short of restarting hal0-api.

``GET /api/status`` also has to say more than "degraded". That bit alone tells
an operator something is wrong but not whether it is self-healing or how much
data is sitting in volatile storage while they decide — which is the whole
question when the answer is "it will be gone if you reboot".
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api.middleware import error_codes
from hal0.api.routes import health as health_routes
from hal0.api.routes import memory as memory_routes
from hal0.memory.degrade import DegradedMemoryProvider
from hal0.memory.pgvector_provider import PgVectorProvider


class _FakeHindsight:
    degraded = False

    async def add(self, text: str, **kwargs: Any) -> dict[str, str]:
        return {"id": kwargs.get("document_id") or "hs-1", "timestamp": "2026-07-29T00:00:00Z"}

    async def search(self, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    async def list_items(self, **kwargs: Any) -> dict[str, Any]:
        return {"items": [], "next_cursor": None}

    async def delete(self, ids: list[str], **kwargs: Any) -> dict[str, int]:
        return {"deleted": 0}

    def graph_status(self) -> dict[str, Any]:
        return {"enabled": False, "extraction_slot": "utility"}

    def set_graph_enabled(self, enabled: bool, extraction_slot: str | None = None) -> None:
        return None

    def set_rerank_enabled(self, enabled: bool) -> None:
        return None


def _app(provider: Any) -> TestClient:
    app = FastAPI()
    error_codes.install(app)
    app.include_router(memory_routes.router, prefix="/api/memory")
    app.state.memory_provider = provider
    return TestClient(app)


def _status(provider: Any) -> dict[str, Any]:
    """Drive /api/status's memory fields without standing up the whole app.

    ``get_status`` reads slot managers, upstreams and the event bus; none of
    that is what this file is about, and stubbing it would test the stub.
    """
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    request.app.state.memory_provider = provider
    return {
        "memory_degraded": health_routes._memory_degraded(request),  # type: ignore[arg-type]
        "memory_degrade": health_routes._memory_degrade_detail(request),  # type: ignore[arg-type]
    }


def _degraded(promote: Any, *, interval_s: float = 0) -> DegradedMemoryProvider:
    return DegradedMemoryProvider(
        promote=promote, fallback=PgVectorProvider(), interval_s=interval_s
    )


def _dead() -> Any:
    raise ConnectionRefusedError("hindsight-api is not up yet")


def test_promote_returns_false_while_the_daemon_is_down() -> None:
    client = _app(_degraded(_dead))
    body = client.post("/api/memory/promote").json()
    assert body["promoted"] is False
    assert body["changed"] is False
    assert "still unreachable" in body["detail"]


def test_promote_succeeds_once_the_daemon_answers() -> None:
    engine = _FakeHindsight()
    provider = _degraded(lambda: engine)
    client = _app(provider)

    body = client.post("/api/memory/promote").json()
    assert body["promoted"] is True
    assert body["changed"] is True
    assert body["state"]["degraded"] is False
    # Idempotent: pressing the button twice is not an error and does not
    # re-report a change that already happened.
    again = client.post("/api/memory/promote").json()
    assert again["promoted"] is True
    assert again["changed"] is False


def test_promote_works_when_auto_promotion_is_disabled() -> None:
    """interval_s <= 0 turns the timer off; the explicit path must still work
    or the operator has no route back at all."""
    engine = _FakeHindsight()
    provider = _degraded(lambda: engine, interval_s=0)
    assert provider.degrade_state()["auto_promote"] is False
    body = _app(provider).post("/api/memory/promote").json()
    assert body["promoted"] is True


def test_promote_on_a_healthy_provider_is_a_no_op_not_an_error() -> None:
    """Safe for a dashboard button to press regardless of current state."""
    body = _app(_FakeHindsight()).post("/api/memory/promote").json()
    assert body["changed"] is False
    assert body["promoted"] is True


def test_promote_without_memory_enabled_reports_unavailable() -> None:
    resp = _app(None).post("/api/memory/promote")
    assert resp.status_code >= 400


@pytest.mark.asyncio
async def test_status_exposes_what_is_at_risk_while_degraded() -> None:
    provider = _degraded(_dead)
    await provider.add("this row dies on the next restart")

    body = _status(provider)
    assert body["memory_degraded"] is True
    detail = body["memory_degrade"]
    assert detail["promoted"] is False
    assert detail["volatile_rows"] == 1
    assert detail["auto_promote"] is False


def test_status_omits_the_detail_when_not_boot_degraded() -> None:
    """Presence of the field is itself the signal, so a healthy box must not
    carry an empty one."""
    body = _status(_FakeHindsight())
    assert body["memory_degraded"] is False
    assert body["memory_degrade"] is None


def test_status_payload_actually_carries_the_field() -> None:
    """Guard the wiring the helper above bypasses."""
    import inspect

    source = inspect.getsource(health_routes.get_status)
    assert '"memory_degrade"' in source


def test_promote_route_is_admin_classed() -> None:
    """It changes the storage engine under a running process — ADMIN, via the
    /api/memory prefix rule."""
    from hal0.security.exposure import AuthClass, classify

    assert classify("POST", "/api/memory/promote") is AuthClass.ADMIN


@pytest.mark.asyncio
async def test_promote_route_replays_volatile_rows() -> None:
    """The route must go through the same drained promotion as the timer —
    not a shortcut that orphans rows."""
    engine = _FakeHindsight()
    seen: list[str] = []
    original = engine.add

    async def spy(text: str, **kwargs: Any) -> dict[str, str]:
        seen.append(text)
        return await original(text, **kwargs)

    engine.add = spy  # type: ignore[method-assign]
    provider = _degraded(lambda: engine)
    await provider.add("written while degraded")

    client = _app(provider)
    body = client.post("/api/memory/promote").json()
    assert body["promoted"] is True
    assert seen == ["written while degraded"]
