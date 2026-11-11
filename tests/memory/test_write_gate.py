"""The memory write gate ``hal0 memory embedder switch`` holds while it runs (#3518).

One flag file, one check, one typed refusal: ``503 memory.writes_paused`` on
REST and the same code in an MCP tool error. Reads stay open.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api.middleware import error_codes
from hal0.api.routes import memory as memory_routes
from hal0.api.routes import memory_admin, memory_lanes
from hal0.mcp.memory import make_dispatcher
from hal0.memory import write_gate
from hal0.memory.hindsight_client import HindsightRestClient
from hal0.memory.hindsight_provider import HindsightProvider
from tests.memory.fakes import FakeMemoryProvider


class _Provider(FakeMemoryProvider):
    """The in-memory provider, taking the keywords the real front doors pass."""

    async def add(  # type: ignore[override]
        self, text, dataset="shared", tags=None, source=None, metadata=None, client_id=None, **_kw
    ):
        return await super().add(text, dataset, tags, source, metadata, client_id)


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))


def _assert_paused(body: dict[str, Any]) -> None:
    error = body["error"]
    assert error["code"] == "memory.writes_paused"
    assert "embedder" in error["message"] and "recall still works" in error["message"]
    assert error["details"]["reason"] == "embedder_switch"
    assert error["details"]["retry_after_s"] == write_gate.RETRY_AFTER_S
    assert error["details"]["since"]


# ── the flag file ────────────────────────────────────────────────────────────


def test_the_gate_is_a_file_that_any_process_on_the_box_can_see() -> None:
    assert write_gate.read_gate() is None
    write_gate.require_writes_allowed()

    written = write_gate.engage_gate(run_id="3f9a1c2e")

    assert write_gate.gate_path().name == "memory-writes-paused.json"
    assert oct(write_gate.gate_path().stat().st_mode & 0o777) == "0o644"
    assert write_gate.read_gate() == written
    assert written["reason"] == "embedder_switch" and written["run_id"] == "3f9a1c2e"
    with pytest.raises(write_gate.MemoryWritesPaused) as err:
        write_gate.require_writes_allowed()
    assert (err.value.status, err.value.code) == (503, "memory.writes_paused")

    assert write_gate.lift_gate() is True
    assert write_gate.lift_gate() is False
    write_gate.require_writes_allowed()


def test_a_gate_file_that_cannot_be_parsed_still_pauses_writes() -> None:
    write_gate.gate_path().parent.mkdir(parents=True)
    write_gate.gate_path().write_text("{half a")
    with pytest.raises(write_gate.MemoryWritesPaused):
        write_gate.require_writes_allowed()


@pytest.mark.parametrize(
    ("method", "path", "writes"),
    [
        ("GET", "/v1/default/banks/shared/stats", False),
        ("POST", "/v1/default/banks/shared/memories/recall", False),
        ("POST", "/v1/default/banks/{bank_id}/reflect", False),
        ("POST", "/v1/default/banks/shared/memories/dry-run-extract", False),
        ("POST", "/v1/default/banks/shared/mental-models/mm-1/dry-run-refresh", False),
        ("POST", "/v1/default/banks/shared/memories", True),
        ("POST", "/v1/default/banks/shared/consolidate", True),
        ("POST", "/v1/default/banks/shared/clone", True),
        ("POST", "/v1/default/banks/shared/mental-models/mm-1/refresh", True),
        ("PATCH", "/v1/default/banks/{bank_id}/documents/{document_id}", True),
        ("DELETE", "/v1/default/banks/shared/directives/d1", True),
        ("PUT", "/v1/default/banks/shared", True),
    ],
)
def test_an_engine_call_is_a_write_unless_it_is_a_read(
    method: str, path: str, writes: bool
) -> None:
    assert write_gate.engine_call_writes(method, path) is writes


# ── REST ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def rest() -> TestClient:
    app = FastAPI()
    error_codes.install(app)
    app.include_router(memory_routes.router, prefix="/api/memory")
    app.state.memory_provider = _Provider(client_id="anonymous")
    return TestClient(app)


def test_rest_add_returns_the_typed_503_while_the_gate_is_set(rest: TestClient) -> None:
    write_gate.engage_gate(run_id="r1")

    resp = rest.post("/api/memory/add", json={"text": "remember this"})

    assert resp.status_code == 503
    _assert_paused(resp.json())
    assert resp.headers["Retry-After"] == str(write_gate.RETRY_AFTER_S)
    assert rest.app.state.memory_provider._rows == [], "nothing was written"
    # Reads are not gated.
    assert rest.post("/api/memory/search", json={"query": "remember"}).status_code == 200

    write_gate.lift_gate()
    assert rest.post("/api/memory/add", json={"text": "remember this"}).status_code == 200
    assert len(rest.app.state.memory_provider._rows) == 1


def test_rest_graph_retry_is_refused_while_the_gate_is_set(rest: TestClient) -> None:
    write_gate.engage_gate()
    resp = rest.post("/api/memory/graph/retry")
    assert resp.status_code == 503
    _assert_paused(resp.json())


# ── MCP ──────────────────────────────────────────────────────────────────────


async def test_mcp_memory_add_returns_the_same_code_while_the_gate_is_set() -> None:
    provider = _Provider(client_id="alice")
    preflight_ran: list[bool] = []

    async def preflight() -> None:
        preflight_ran.append(True)

    dispatch = make_dispatcher(provider, add_preflight=preflight)
    write_gate.engage_gate(run_id="r1")

    out = await dispatch("memory_add", {"text": "remember this"})

    assert out["status"] == "error"
    assert out["error"]["code"] == "memory.writes_paused"
    assert out["error"]["details"]["retry_after_s"] == write_gate.RETRY_AFTER_S
    assert "recall still works" in out["error"]["detail"]
    assert provider._rows == [] and preflight_ran == [], "refused before any other preflight"
    # A read tool still answers.
    assert (await dispatch("memory_search", {"query": "x"}))["status"] == "ok"

    write_gate.lift_gate()
    assert (await dispatch("memory_add", {"text": "remember this"}))["status"] == "ok"


# ── the provider: every hal0 writer ──────────────────────────────────────────


class _Client:
    """Records which engine methods were reached."""

    def __init__(self) -> None:
        self.called: list[str] = []

    def __getattr__(self, name: str) -> Any:
        async def call(**_kw: Any) -> Any:
            self.called.append(name)
            return {"results": [], "items": [], "operations": [], "total": 0}

        return call


WRITES: list[tuple[str, dict[str, Any]]] = [
    ("add", {"text": "x"}),
    ("delete", {"ids": ["d1"]}),
    ("curate", {"memory_id": "m1", "text": "y"}),
    ("create_mental_model", {"name": "n", "source_query": "q"}),
    ("update_mental_model", {"mental_model_id": "mm", "name": "n"}),
    ("delete_mental_model", {"mental_model_id": "mm"}),
    ("refresh_mental_model", {"mental_model_id": "mm"}),
    ("create_directive", {"name": "n", "content": "c"}),
    ("update_directive", {"directive_id": "d", "content": "c"}),
    ("delete_directive", {"directive_id": "d"}),
    ("cancel_operation", {"operation_id": "op"}),
    ("retry_operation", {"operation_id": "op"}),
    ("consolidate", {}),
]


@pytest.mark.parametrize(("method", "kwargs"), WRITES, ids=[w[0] for w in WRITES])
async def test_every_mutating_provider_method_is_refused_before_the_engine_is_called(
    method: str, kwargs: dict[str, Any]
) -> None:
    client = _Client()
    provider = HindsightProvider(client=client, client_id="hermes", unified_bank=True)
    write_gate.engage_gate(run_id="r1")

    with pytest.raises(write_gate.MemoryWritesPaused):
        await getattr(provider, method)(**kwargs)

    assert client.called == []
    # A refusal is not a failed write: the write-health flag stays clean.
    assert provider.write_degraded is False


async def test_recall_through_the_provider_is_not_gated() -> None:
    client = _Client()
    provider = HindsightProvider(client=client, client_id="hermes", unified_bank=True)
    write_gate.engage_gate()

    await provider.recall("what do I know")

    assert "recall" in client.called


async def test_the_dead_letter_auto_retry_stands_down_while_the_gate_is_set() -> None:
    client = _Client()
    provider = HindsightProvider(client=client, client_id="hermes", unified_bank=True)
    write_gate.engage_gate()

    assert await provider.maybe_auto_retry_dead_letters() is None
    assert client.called == []


# ── the admin routes, which reach the engine without the provider ────────────


class _Stub:
    def __init__(self, client: HindsightRestClient) -> None:
        self.hindsight_client = client


@pytest.fixture
def admin() -> tuple[TestClient, list[tuple[str, str]]]:
    seen: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        return httpx.Response(200, json={"results": [], "text": "", "banks": []})

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:9177"
    )
    app = FastAPI()
    error_codes.install(app)
    app.include_router(memory_admin.router, prefix="/api/memory")
    app.include_router(memory_lanes.router, prefix="/api/memory")
    app.state.memory_provider = _Stub(HindsightRestClient(http_client=http, api_key="k"))
    return TestClient(app), seen


ADMIN_WRITES = [
    ("post", "/api/memory/banks/shared/consolidate"),
    ("post", "/api/memory/banks/shared/clone?target_bank_id=copy"),
    ("post", "/api/memory/banks/shared/import"),
    ("post", "/api/memory/banks/shared/mental-models"),
    ("patch", "/api/memory/banks/shared/documents/d1"),
    ("delete", "/api/memory/banks/shared/documents/d1"),
    ("delete", "/api/memory/banks/shared?confirm=shared"),
    ("delete", "/api/memory/banks/shared/memories?confirm=shared"),
    ("put", "/api/memory/banks/shared/profile"),
    ("post", "/api/memory/banks/shared/document-transfer"),
    ("post", "/api/memory/engine/v1/default/banks/shared/memories"),
    ("delete", "/api/memory/engine/v1/default/banks/shared/directives/d1"),
]


@pytest.mark.parametrize(("verb", "path"), ADMIN_WRITES)
def test_admin_writes_are_refused_and_never_forwarded(
    admin: tuple[TestClient, list[tuple[str, str]]], verb: str, path: str
) -> None:
    client, seen = admin
    write_gate.engage_gate(run_id="r1")

    resp = getattr(client, verb)(path)

    assert resp.status_code == 503
    _assert_paused(resp.json())
    assert seen == [], "the engine was never called"


@pytest.mark.parametrize(
    ("verb", "path"),
    [
        ("get", "/api/memory/banks"),
        ("post", "/api/memory/banks/shared/recall"),
        ("post", "/api/memory/engine/v1/default/banks/shared/memories/recall"),
        ("get", "/api/memory/engine/v1/default/banks"),
    ],
)
def test_admin_reads_stay_open(
    admin: tuple[TestClient, list[tuple[str, str]]], verb: str, path: str
) -> None:
    client, seen = admin
    write_gate.engage_gate()

    kwargs = {"json": {"query": "x"}} if verb == "post" else {}
    resp = getattr(client, verb)(path, **kwargs)

    assert resp.status_code == 200
    assert len(seen) == 1
