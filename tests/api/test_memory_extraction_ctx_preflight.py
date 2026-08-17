"""``POST /api/memory/add`` preflights the extraction slot's context window (#1903).

Hindsight builds its graph via its own extraction LLM call, dispatched to
``hal0/<extraction_slot>`` regardless of ``[memory.graph].enabled``
(reporting-only on this engine — ``HindsightProvider.graph_status``). Nothing
ever checked that dispatch's effective window against the extraction prompt's
own footprint before landing a write: an undersized resolved slot used to
answer ``/add`` with HTTP 200 + a document id, and the retain was then either
silently dropped by the engine (``retain_extract_facts`` 500 "Context size has
been exceeded") or answered by persisting the extraction prompt's own
scaffolding as a "fact" with no grounding check.

These pin the route-level contract: a below-floor window fails fast (503,
before ``wrapper.add`` is ever called — no document id minted for a write
that was always going to be dropped); an ``ok``/``unknown`` window still
reaches the wrapper unchanged, so a healthy box (or one the preflight simply
can't prove anything about) is never blocked.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.agents.anchor_window import AnchorWindow
from hal0.api.middleware import error_codes
from hal0.api.routes import memory as memory_routes


class StubHindsightWrapper:
    """Duck-typed stand-in exposing the two attributes the route probes for
    ("this provider does slot-dispatched extraction") — ``hindsight_client``
    and ``extraction_slot`` — mirroring :class:`HindsightProvider`."""

    def __init__(self, *, extraction_slot: str = "utility") -> None:
        self.hindsight_client = object()
        self.extraction_slot = extraction_slot
        self.add_calls: list[dict[str, Any]] = []

    async def add(self, **kwargs: Any) -> dict[str, Any]:
        self.add_calls.append(kwargs)
        return {"id": kwargs.get("document_id") or "generated-id", "timestamp": "now"}


@pytest.fixture
def stub_wrapper() -> StubHindsightWrapper:
    return StubHindsightWrapper()


def _build_app(stub: StubHindsightWrapper) -> FastAPI:
    app = FastAPI()
    error_codes.install(app)
    app.include_router(memory_routes.router, prefix="/api/memory", tags=["memory"])
    app.state.memory_provider = stub
    return app


@pytest.fixture
def client(stub_wrapper: StubHindsightWrapper) -> Iterator[TestClient]:
    app = _build_app(stub_wrapper)
    with TestClient(app) as c:
        yield c


def _window(
    verdict_effective: int | None, *, floor: int = 8192, slot: str = "utility"
) -> AnchorWindow:
    return AnchorWindow(
        model=f"hal0/{slot}",
        slot=slot,
        effective=verdict_effective,
        ceiling=verdict_effective,
        floor=floor,
        floor_source="hal0:extraction-prompt-floor",
        slots_dir=Path("/etc/hal0/slots"),
        endpoint="/v1/models",
    )


def test_add_blocked_when_extraction_slot_is_below_the_ctx_floor(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, stub_wrapper: StubHindsightWrapper
) -> None:
    """The reported repro shape: the resolved extraction slot's effective
    window (4096) sits under the extraction prompt's own floor. The route
    must fail fast — 503, never a 200 for a write the engine can't extract —
    and MUST NOT call ``wrapper.add`` at all (no document id minted for a
    write that was always going to be dropped)."""

    async def _fake_window(request, wrapper):
        assert wrapper is stub_wrapper
        return _window(4096)

    monkeypatch.setattr(memory_routes, "_extraction_window", _fake_window)

    r = client.post("/api/memory/add", json={"text": "hello"})

    assert r.status_code == 503, r.text
    body = r.json()
    assert body["error"]["code"] == "memory.extraction_ctx_too_small"
    assert stub_wrapper.add_calls == []


def test_add_proceeds_when_extraction_slot_clears_the_floor(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, stub_wrapper: StubHindsightWrapper
) -> None:
    async def _fake_window(request, wrapper):
        return _window(32768)

    monkeypatch.setattr(memory_routes, "_extraction_window", _fake_window)

    r = client.post("/api/memory/add", json={"text": "hello"})

    assert r.status_code == 200, r.text
    assert len(stub_wrapper.add_calls) == 1


def test_add_proceeds_when_the_window_cannot_be_proven(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, stub_wrapper: StubHindsightWrapper
) -> None:
    """``unknown`` (no evidence either way) must not block writes on a
    healthy box just because the catalog lookup came back thin — the same
    "unknown is not a pass, but it is also not a refusal" rule the route
    docstring cites from #1877's own resolver."""

    async def _fake_window(request, wrapper):
        return _window(None)

    monkeypatch.setattr(memory_routes, "_extraction_window", _fake_window)

    r = client.post("/api/memory/add", json={"text": "hello"})

    assert r.status_code == 200, r.text
    assert len(stub_wrapper.add_calls) == 1


def test_add_skips_the_preflight_for_providers_without_slot_dispatched_extraction() -> None:
    """A provider with no ``hindsight_client`` (e.g. the in-memory
    ``PgVectorProvider`` fallback) never dispatches extraction to a slot at
    all — the REAL ``_extraction_window`` (not a stub) must resolve to
    ``None`` for it without attempting any catalog lookup, and ``/add`` must
    proceed normally."""

    class PlainWrapper:
        async def add(self, **kwargs: Any) -> dict[str, Any]:
            return {"id": "x", "timestamp": "now"}

    stub = PlainWrapper()
    app = _build_app(stub)  # type: ignore[arg-type]

    with TestClient(app) as c:
        r = c.post("/api/memory/add", json={"text": "hello"})

    assert r.status_code == 200, r.text


async def test_extraction_window_helper_no_ops_without_hindsight_client() -> None:
    """Direct unit check of the real guard: no ``hindsight_client`` attribute
    (or a ``None`` one) short-circuits before any catalog call is made."""

    class NoHindsight:
        pass

    class WithNoneClient:
        hindsight_client = None
        extraction_slot = "utility"

    request = object()  # never touched if the guard fires correctly
    assert await memory_routes._extraction_window(request, NoHindsight()) is None
    assert await memory_routes._extraction_window(request, WithNoneClient()) is None
