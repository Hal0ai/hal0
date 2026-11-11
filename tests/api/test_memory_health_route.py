"""``GET /api/memory/health`` against a mocked engine (ADR-0058, decision 12).

An ``httpx.MockTransport`` stands in for Hindsight behind a real
``HindsightRestClient``, as in test_memory_admin_routes.py. The fake engine
below pages, filters and totals the way engine 1.2.0 does, so a read that
stops at the first page is caught by the figures, not by a request count.

Run targeted:
    .venv/bin/python -m pytest tests/api/test_memory_health_route.py -q
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api.middleware import error_codes
from hal0.api.routes import _memory_health_collect as collect
from hal0.api.routes import memory_health, memory_lanes
from hal0.memory.hindsight_client import HindsightRestClient
from hal0.security.exposure import AuthClass, classify, match_rule

_BGE = "BAAI/bge-small-en-v1.5"
_HS = "hindsight-ai/hs-embed-1"

_BANK_PATH = re.compile(r"^/v1/default/banks/([^/]+)/(stats|operations|mental-models)$")


def _ago(seconds: float) -> str:
    return (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat()


def _op(
    status: str,
    age_s: float,
    *,
    task_type: str = "retain",
    error: str | None = None,
    changed_s: float | None = None,
    retry_in_s: float | None = None,
    parent: bool = False,
) -> dict[str, Any]:
    """An ``OperationResponse`` row created ``age_s`` ago."""
    return {
        "id": f"op-{id(object())}",
        "task_type": task_type,
        "operation_type": task_type,
        "items_count": 1,
        "created_at": _ago(age_s),
        "updated_at": _ago(age_s if changed_s is None else changed_s),
        "status": status,
        "error_message": error,
        "retry_count": 0,
        "next_retry_at": None if retry_in_s is None else _ago(-retry_in_s),
        "_parent": parent,
    }


def _model(
    *, page: bool = False, stale: bool = False, failed: bool = False, refreshed_s: float = 0
) -> dict[str, Any]:
    """A ``MentalModelResponse`` row. ``refreshed_s`` of 0 = never refreshed:
    the engine sets ``last_refreshed_at`` to ``created_at`` on creation."""
    created = _ago(86400.0)
    return {
        "id": f"mm-{id(object())}",
        "name": "n",
        "created_at": created,
        "last_refreshed_at": _ago(refreshed_s) if refreshed_s else created,
        "last_refresh_failed_at": _ago(60.0) if failed else None,
        "is_knowledge_page": page,
        "is_stale": stale,
    }


class _Engine:
    """Just enough of hindsight-api 1.2.0 for the health collector."""

    def __init__(self) -> None:
        self.banks: dict[str, dict[str, Any]] = {}
        self.requests: list[tuple[str, dict[str, str]]] = []
        self.down = False
        #: False = answer the operations list without ``total``.
        self.totals = True
        self.delays: dict[str, float] = {}
        self.overrides: dict[str, tuple[int, Any]] = {}

    def add_bank(
        self,
        bank_id: str,
        *,
        facts: int = 10,
        documents: int = 2,
        ops: list[dict[str, Any]] | None = None,
        models: list[dict[str, Any]] | None = None,
        pending_consolidation: int = 0,
        failed_consolidation: int = 0,
    ) -> None:
        # Newest first, as the engine lists them.
        ordered = sorted(ops or [], key=lambda o: o["created_at"], reverse=True)
        self.banks[bank_id] = {
            "facts": facts,
            "ops": ordered,
            "models": models or [],
            "stats": {
                "bank_id": bank_id,
                "total_nodes": facts,
                "total_documents": documents,
                "pending_consolidation": pending_consolidation,
                "failed_consolidation": failed_consolidation,
            },
        }

    def paths(self, suffix: str = "") -> list[str]:
        return [p for p, _ in self.requests if p.endswith(suffix)]

    async def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = dict(request.url.params)
        self.requests.append((path, params))
        if self.down:
            raise httpx.ConnectError("connection refused", request=request)
        if path in self.delays:
            await asyncio.sleep(self.delays[path])
        if path in self.overrides:
            status, body = self.overrides[path]
            return httpx.Response(status, json=body)
        limit = int(params.get("limit", 20))
        offset = int(params.get("offset", 0))
        if path == "/version":
            return httpx.Response(200, json={"api_version": "1.2.0", "features": {}})
        if path == "/v1/default/banks":
            rows = [{"bank_id": b, "fact_count": v["facts"]} for b, v in self.banks.items()]
            return httpx.Response(
                200,
                json={
                    "banks": rows[offset : offset + limit],
                    "total": len(rows),
                    "limit": limit,
                    "offset": offset,
                },
            )
        match = _BANK_PATH.match(path)
        if not match or match.group(1) not in self.banks:
            return httpx.Response(404, json={"detail": "not found"})
        bank, what = self.banks[match.group(1)], match.group(2)
        if what == "stats":
            return httpx.Response(200, json=bank["stats"])
        if what == "mental-models":
            items = bank["models"]
            return httpx.Response(
                200,
                json={"items": items[offset : offset + limit], "total": len(items)},
            )
        if limit > 100:  # the engine's maximum
            return httpx.Response(422, json={"detail": "limit"})
        rows = [o for o in bank["ops"] if o["status"] == params.get("status")]
        if params.get("exclude_parents") == "true":
            rows = [o for o in rows if not o["_parent"]]
        body = {
            "bank_id": match.group(1),
            "total": len(rows),
            "limit": limit,
            "offset": offset,
            "operations": rows[offset : offset + limit],
        }
        if not self.totals:
            del body["total"]
        return httpx.Response(200, json=body)

    def offsets(self, bank_id: str, status: str) -> list[int]:
        """The offsets one bank's operations list was read at, in request order."""
        return [
            int(params["offset"])
            for path, params in self.requests
            if path == f"/v1/default/banks/{bank_id}/operations" and params["status"] == status
        ]


class _Provider:
    def __init__(self, client: HindsightRestClient | None) -> None:
        if client is not None:
            self.hindsight_client = client

    def graph_status(self) -> dict[str, Any]:
        return {"extraction_slot": "memory"}


def _embedder(recorded: str, configured: str, **extra: Any) -> dict[str, Any]:
    status = {
        "recorded": recorded,
        "recorded_provider": "local",
        "configured": configured,
        "configured_provider": "local",
        "mixed": False,
        "conflict": None,
        "record_error": None,
        "store_unavailable": None,
        **extra,
    }
    status["needs_switch"] = status["mixed"] or recorded != configured
    return status


def _app(provider: Any, *slots: str) -> FastAPI:
    app = FastAPI()
    error_codes.install(app)
    app.include_router(memory_health.router, prefix="/api/memory", tags=["memory"])
    app.include_router(memory_lanes.router, prefix="/api/memory", tags=["memory"])
    app.state.memory_provider = provider
    manager = MagicMock()
    manager.iter_configs = AsyncMock(
        return_value=[
            {"name": n, "type": "llm", "enabled": True, "model_id": f"model-{n}"} for n in slots
        ]
    )
    app.state.slot_manager = manager
    return app


@pytest.fixture
def engine() -> _Engine:
    return _Engine()


@pytest.fixture(autouse=True)
def local_facts(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The two host reads behind seams: the engine's LLM target and the
    embedder record. A test changes the values it cares about."""
    facts: dict[str, Any] = {
        "llm": {"base_url": None, "overridden": False},
        "embedder": _embedder(_HS, _HS),
    }
    monkeypatch.setattr(collect, "_engine_llm", lambda: facts["llm"])
    monkeypatch.setattr(collect, "_embedder_status", lambda _models: facts["embedder"])
    return facts


@pytest.fixture
def client(engine: _Engine) -> Iterator[TestClient]:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(engine.handler), base_url="http://127.0.0.1:9177"
    )
    rest = HindsightRestClient(http_client=http, api_key="hal0-local-noauth")
    with TestClient(_app(_Provider(rest), "memory", "agent")) as c:
        yield c


def _health(client: TestClient) -> dict[str, Any]:
    response = client.get("/api/memory/health")
    assert response.status_code == 200, response.text
    return response.json()


def _steps(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {step["id"]: step for step in body["steps"]}


# ── shape and the clear path ─────────────────────────────────────────────


def test_a_healthy_box_is_clear_and_fills_the_whole_shape(
    client: TestClient, engine: _Engine
) -> None:
    engine.add_bank(
        "shared",
        facts=1335,
        documents=253,
        ops=[_op("pending", 30.0), _op("completed", 900.0)],
        models=[_model(page=True, refreshed_s=600.0), _model(refreshed_s=120.0)],
        pending_consolidation=4,
    )
    engine.add_bank("coding-agent::repo-1.4", facts=20, documents=3)
    body = _health(client)

    assert list(body) == [
        "enabled",
        "verdict",
        "checked_at",
        "partial",
        "engine",
        "extraction",
        "embedder",
        "queue",
        "knowledge",
        "banks",
        "writes",
        "steps",
    ]
    assert (body["enabled"], body["verdict"], body["partial"]) == (True, "clear", [])
    assert datetime.fromisoformat(body["checked_at"]).tzinfo is not None
    assert body["engine"] == {"reachable": True, "version": "1.2.0"}
    assert body["extraction"] == {
        "slot": "memory",
        "mode": "dedicated",
        "slot_resolves": True,
        "llm_reachable": None,
        "concurrency": 1,
        "failures_6h": 0,
        "top_errors": [],
    }
    assert body["embedder"] == {
        "recorded": {"provider": "local", "model": _HS},
        "configured": {"provider": "local", "model": _HS},
        "mixed": False,
        "needs_switch": False,
        "conflict": None,
        "informational": False,
    }
    queue = body["queue"]
    assert (queue["waiting"], queue["failed"], queue["truncated"]) == (1, 0, False)
    assert 29.0 <= queue["oldest_waiting_s"] <= 40.0
    assert (queue["consolidation_pending"], queue["consolidation_failed"]) == (4, 0)
    assert [row["bank_id"] for row in queue["by_bank"]] == ["shared", "coding-agent::repo-1.4"]
    assert queue["by_bank"][1] == {
        "bank_id": "coding-agent::repo-1.4",
        "waiting": 0,
        "failed": 0,
        "oldest_waiting_s": None,
    }
    knowledge = body["knowledge"]
    assert {k: knowledge[k] for k in knowledge if k != "last_success_at"} == {
        "pages": 1,
        "pages_stale": 0,
        "models": 1,
        "models_stale": 0,
        "refresh_failed": 0,
    }
    # The latest of the two refreshes that finished.
    age = datetime.now(UTC) - datetime.fromisoformat(knowledge["last_success_at"])
    assert 115.0 <= age.total_seconds() <= 135.0
    assert body["banks"] == {
        "layout": "legacy_unified",
        "total": 2,
        "facts": 1355,
        "list": [
            {"bank_id": "shared", "facts": 1335, "documents": 253},
            {"bank_id": "coding-agent::repo-1.4", "facts": 20, "documents": 3},
        ],
    }
    assert body["writes"] is None
    assert [(s["id"], s["state"]) for s in body["steps"]] == [
        ("engine", "clear"),
        ("extraction", "clear"),
        ("queue", "clear"),
        ("embedder", "clear"),
        ("knowledge", "clear"),
    ]


def test_a_model_never_refreshed_is_not_a_successful_refresh(
    client: TestClient, engine: _Engine
) -> None:
    engine.add_bank("shared", models=[_model(page=True, stale=True, failed=True)])
    knowledge = _health(client)["knowledge"]
    assert knowledge["last_success_at"] is None
    assert (knowledge["pages_stale"], knowledge["refresh_failed"]) == (1, 1)


def test_steps_follow_the_dependency_chain(client: TestClient, engine: _Engine) -> None:
    engine.add_bank(
        "shared",
        ops=[_op("failed", 7200.0, error="LLM call failed", changed_s=300.0)],
        models=[_model(page=True, stale=True, refreshed_s=9000.0), _model(failed=True)],
    )
    body = _health(client)
    steps = _steps(body)
    assert body["verdict"] == "attention"
    assert (steps["extraction"]["state"], steps["extraction"]["reason_code"]) == (
        "current",
        "extraction_recent_failures",
    )
    assert (steps["queue"]["state"], steps["queue"]["waits_on"]) == ("waiting", "extraction")
    assert steps["queue"]["reason_code"] == "queue_operations_failed"
    assert steps["embedder"]["state"] == "clear"
    assert (steps["knowledge"]["state"], steps["knowledge"]["waits_on"]) == ("waiting", "queue")
    assert steps["knowledge"]["reason_code"] == "knowledge_refresh_failed"
    assert body["knowledge"]["models_stale"] == 0 and body["knowledge"]["pages_stale"] == 1


def test_the_upgraded_box_embedder_is_informational_not_open(
    client: TestClient, engine: _Engine, local_facts: dict[str, Any]
) -> None:
    engine.add_bank("shared")
    local_facts["embedder"] = _embedder(_BGE, _HS)
    body = _health(client)
    assert body["embedder"]["needs_switch"] is True
    assert body["embedder"]["informational"] is True
    assert _steps(body)["embedder"]["state"] == "clear"
    assert body["verdict"] == "clear"


def test_an_embedder_the_operator_set_opens_the_step(
    client: TestClient, engine: _Engine, local_facts: dict[str, Any]
) -> None:
    engine.add_bank("shared")
    local_facts["embedder"] = _embedder(_BGE, "example-org/another-embedder")
    body = _health(client)
    assert body["embedder"]["informational"] is False
    step = _steps(body)["embedder"]
    assert (step["state"], step["reason_code"]) == ("current", "embedder_needs_switch")


# ── extraction facts ─────────────────────────────────────────────────────


def test_a_slot_with_no_model_opens_extraction(engine: _Engine) -> None:
    engine.add_bank("shared")
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(engine.handler), base_url="http://127.0.0.1:9177"
    )
    rest = HindsightRestClient(http_client=http, api_key="k")
    with TestClient(_app(_Provider(rest))) as c:  # no llm slot has a model
        body = _health(c)
    assert body["extraction"]["slot_resolves"] is False
    step = _steps(body)["extraction"]
    assert (step["state"], step["reason_code"]) == ("current", "extraction_slot_unresolved")


def test_the_slot_is_not_judged_when_the_engine_runs_on_another_llm(
    engine: _Engine, local_facts: dict[str, Any]
) -> None:
    """An env-file override points the engine elsewhere: the slot is not in play."""
    engine.add_bank("shared")
    local_facts["llm"] = {"base_url": None, "overridden": True}
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(engine.handler), base_url="http://127.0.0.1:9177"
    )
    rest = HindsightRestClient(http_client=http, api_key="k")
    with TestClient(_app(_Provider(rest))) as c:
        body = _health(c)
    assert body["extraction"]["slot_resolves"] is None
    assert _steps(body)["extraction"]["state"] == "clear"


def test_an_llm_endpoint_that_refuses_connections_opens_extraction(
    client: TestClient,
    engine: _Engine,
    local_facts: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine.add_bank("shared")
    local_facts["llm"] = {"base_url": "https://llm.example.test/v1", "overridden": True}
    probed: list[Any] = []

    async def refuse(base_url: Any) -> bool:
        probed.append(base_url)
        return False

    monkeypatch.setattr(collect, "_llm_reachable", refuse)
    body = _health(client)
    assert probed == ["https://llm.example.test/v1"]
    assert body["extraction"]["llm_reachable"] is False
    assert _steps(body)["extraction"]["reason_code"] == "extraction_llm_unreachable"


def test_llm_probe_is_a_connect_and_reports_refusal() -> None:
    async def run() -> tuple[bool | None, bool | None, bool | None]:
        server = await asyncio.start_server(lambda _r, w: w.close(), "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        up = await collect._llm_reachable(f"http://127.0.0.1:{port}/v1")
        server.close()
        await server.wait_closed()
        down = await collect._llm_reachable(f"http://127.0.0.1:{port}/v1")
        return up, down, await collect._llm_reachable(None)

    assert asyncio.run(run()) == (True, False, None)


def test_only_recent_retain_failures_count_against_extraction(
    client: TestClient, engine: _Engine
) -> None:
    engine.add_bank(
        "shared",
        ops=[
            # Created three days ago, failed five minutes ago: recent.
            _op("failed", 3 * 86400.0, error="boom", changed_s=300.0),
            # Failed seven hours ago: queue work, not a failing extraction.
            _op("failed", 8 * 3600.0, error="older", changed_s=7 * 3600.0),
            # Recent, but not an extraction.
            _op("failed", 600.0, task_type="consolidation", error="other", changed_s=60.0),
        ],
    )
    body = _health(client)
    assert body["extraction"]["failures_6h"] == 1
    assert body["extraction"]["top_errors"] == [{"message": "boom", "count": 1}]
    assert body["queue"]["failed"] == 3


# ── paging ───────────────────────────────────────────────────────────────


def test_the_oldest_waiting_operation_is_found_on_a_later_page(
    client: TestClient, engine: _Engine
) -> None:
    ops = [_op("pending", 10.0 + i) for i in range(249)]
    ops.append(_op("pending", 3 * 3600.0))  # the oldest: the last row of the list
    ops += [_op("failed", 5000.0 + i, error="boom", changed_s=5000.0) for i in range(120)]
    engine.add_bank("shared", ops=ops)
    body = _health(client)

    queue = body["queue"]
    assert (queue["waiting"], queue["failed"], queue["truncated"]) == (250, 120, False)
    assert 3 * 3600.0 <= queue["oldest_waiting_s"] <= 3 * 3600.0 + 30.0
    step = _steps(body)["queue"]
    assert step["figures"]["oldest_waiting_s"] == queue["oldest_waiting_s"]
    # The count is the engine's total; the oldest is at the end, so the read
    # goes there and skips the middle.
    assert engine.offsets("shared", "pending") == [0, 150]
    # A failure's time is its last update, so every failed row is read.
    assert sorted(engine.offsets("shared", "failed")) == [0, 100]
    assert body["extraction"]["failures_6h"] == 120
    assert body["extraction"]["top_errors"] == [{"message": "boom", "count": 120}]


def test_a_deep_queue_costs_two_requests_and_keeps_the_oldest_age(
    client: TestClient, engine: _Engine
) -> None:
    depth = collect.OPERATIONS_CAP_PER_BANK + 150
    ops = [_op("pending", 10.0 + i) for i in range(depth - 1)]
    ops.append(_op("pending", 5 * 86400.0))
    engine.add_bank("deep", ops=ops)
    queue = _health(client)["queue"]
    assert (queue["waiting"], queue["truncated"]) == (depth, False)
    assert queue["oldest_waiting_s"] >= 5 * 86400.0
    assert engine.offsets("deep", "pending") == [0, depth - 100]


def test_the_read_walks_back_past_rows_that_were_put_off(
    client: TestClient, engine: _Engine
) -> None:
    """The last page holds only rows put off on purpose: the oldest due row is before it."""
    ops = [_op("pending", 100.0 + i) for i in range(150)]
    ops += [
        _op("pending", 50000.0 + i, task_type="refresh_mental_model", retry_in_s=600.0)
        for i in range(100)
    ]
    engine.add_bank("shared", ops=ops)
    queue = _health(client)["queue"]
    assert queue["waiting"] == 250
    assert 249.0 <= queue["oldest_waiting_s"] <= 280.0  # the oldest of the 150 that are due
    assert engine.offsets("shared", "pending") == [0, 150, 100]


def test_an_engine_without_totals_is_read_forward_to_the_end(
    client: TestClient, engine: _Engine
) -> None:
    engine.totals = False
    ops = [_op("pending", 10.0 + i) for i in range(249)]
    ops.append(_op("pending", 3 * 3600.0))
    engine.add_bank("shared", ops=ops)
    queue = _health(client)["queue"]
    assert (queue["waiting"], queue["truncated"]) == (250, False)
    assert queue["oldest_waiting_s"] >= 3 * 3600.0
    assert engine.offsets("shared", "pending") == [0, 100, 200]


def test_an_operation_put_off_on_purpose_is_not_the_oldest_waiting(
    client: TestClient, engine: _Engine
) -> None:
    engine.add_bank(
        "shared",
        ops=[
            _op("pending", 7200.0, task_type="refresh_mental_model", retry_in_s=900.0),
            _op("pending", 45.0),
        ],
    )
    body = _health(client)
    assert body["queue"]["waiting"] == 2
    assert 44.0 <= body["queue"]["oldest_waiting_s"] <= 60.0
    assert _steps(body)["queue"]["state"] == "clear"


def test_split_parent_batches_are_not_counted_twice(client: TestClient, engine: _Engine) -> None:
    engine.add_bank(
        "shared",
        ops=[
            _op("pending", 4000.0, task_type="batch_retain", parent=True),
            _op("pending", 50.0),
            _op("failed", 900.0, task_type="batch_retain", error="x", parent=True),
            _op("failed", 900.0, error="x"),
        ],
    )
    queue = _health(client)["queue"]
    assert (queue["waiting"], queue["failed"]) == (1, 1)
    assert queue["oldest_waiting_s"] < 120.0


def test_the_cap_stops_the_walk_and_says_truncated(client: TestClient, engine: _Engine) -> None:
    cap = collect.OPERATIONS_CAP_PER_BANK
    ops = [_op("failed", 600.0 + i, error="boom", changed_s=60.0) for i in range(cap + 150)]
    engine.add_bank("deep", ops=ops)
    engine.add_bank("quiet", ops=[_op("failed", 600.0, error="boom", changed_s=60.0)])
    body = _health(client)

    queue = body["queue"]
    assert queue["truncated"] is True
    # The engine's total is the count even where the rows were not read.
    by_bank = {row["bank_id"]: row for row in queue["by_bank"]}
    assert by_bank["deep"]["failed"] == cap + 150
    assert queue["failed"] == cap + 150 + 1
    assert sorted(engine.offsets("deep", "failed")) == list(range(0, cap, 100))
    # Figures made from the rows are a floor past the cap.
    assert body["extraction"]["failures_6h"] == cap + 1
    assert body["extraction"]["top_errors"] == [{"message": "boom", "count": cap + 1}]
    assert body["partial"] == []  # truncated is not partial


def test_the_cap_holds_without_totals_too(client: TestClient, engine: _Engine) -> None:
    engine.totals = False
    cap = collect.OPERATIONS_CAP_PER_BANK
    engine.add_bank("deep", ops=[_op("pending", 10.0 + i) for i in range(cap + 150)])
    queue = _health(client)["queue"]
    assert queue["truncated"] is True
    assert queue["waiting"] == cap  # the rows read: a floor
    assert engine.offsets("deep", "pending") == list(range(0, cap, 100))


def test_twenty_quiet_banks_cost_four_requests_each(client: TestClient, engine: _Engine) -> None:
    for i in range(20):
        engine.add_bank(f"bank-{i}")
    body = _health(client)
    assert body["banks"]["total"] == 20 and body["partial"] == []
    # /version and the bank list twice (engine status, then the full list),
    # then stats + waiting + failed + mental models per bank.
    assert len(engine.requests) == 3 + 20 * 4


# ── fail-soft ────────────────────────────────────────────────────────────


def test_engine_down_is_unreachable_not_an_error(client: TestClient, engine: _Engine) -> None:
    engine.down = True
    body = _health(client)
    assert (body["enabled"], body["verdict"], body["partial"]) == (True, "unreachable", [])
    assert body["engine"] == {"reachable": False, "version": None}
    for section in ("extraction", "embedder", "queue", "knowledge", "banks", "writes"):
        assert body[section] is None
    assert body["steps"] == [
        {
            "id": "engine",
            "state": "current",
            "waits_on": None,
            "reason_code": "engine_unreachable",
            "figures": {"reachable": False},
        }
    ]


def test_a_hung_engine_is_unreachable_within_the_probe_budget(
    client: TestClient, engine: _Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(collect, "ENGINE_PROBE_TIMEOUT_S", 0.2)
    engine.delays = {"/version": 30.0, "/v1/default/banks": 30.0}
    started = time.monotonic()
    body = _health(client)
    assert time.monotonic() - started < 5.0
    assert body["verdict"] == "unreachable"


def test_the_boot_fallback_provider_reads_as_an_unreachable_engine() -> None:
    """No Hindsight client on a hindsight-configured box: the engine lost the boot race."""
    with TestClient(_app(_Provider(None), "agent")) as c:
        body = _health(c)
    assert body["verdict"] == "unreachable"
    assert [step["id"] for step in body["steps"]] == ["engine"]


def test_a_slow_bank_makes_its_section_partial(
    client: TestClient, engine: _Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(collect, "TOTAL_BUDGET_S", 0.5)
    engine.add_bank("quick", ops=[_op("pending", 40.0), _op("failed", 99999.0, error="x")])
    engine.add_bank("slow", ops=[_op("pending", 9000.0)])
    engine.delays = {"/v1/default/banks/slow/operations": 30.0}
    started = time.monotonic()
    body = _health(client)
    assert time.monotonic() - started < 5.0

    # The failed list feeds extraction.failures_6h, so both are partial.
    assert body["partial"] == ["extraction", "queue"]
    by_bank = {row["bank_id"]: row for row in body["queue"]["by_bank"]}
    assert by_bank["slow"] == {
        "bank_id": "slow",
        "waiting": None,
        "failed": None,
        "oldest_waiting_s": None,
    }
    assert (by_bank["quick"]["waiting"], by_bank["quick"]["failed"]) == (1, 1)
    # What is known still decides the path.
    assert (body["queue"]["waiting"], body["queue"]["failed"]) == (1, 1)
    assert _steps(body)["queue"]["reason_code"] == "queue_operations_failed"
    assert body["banks"]["total"] == 2 and body["knowledge"]["pages"] == 0


def test_a_bank_the_engine_errors_on_is_partial_not_a_failure(
    client: TestClient, engine: _Engine
) -> None:
    engine.add_bank("fine", ops=[_op("pending", 12.0)])
    engine.add_bank("broken")
    engine.overrides = {
        "/v1/default/banks/broken/stats": (500, {"detail": "boom"}),
        "/v1/default/banks/broken/mental-models": (500, {"detail": "boom"}),
    }
    body = _health(client)
    assert body["partial"] == ["queue", "knowledge", "banks"]
    assert body["banks"]["list"][1] == {"bank_id": "broken", "facts": 10, "documents": None}
    assert body["queue"]["waiting"] == 1


@pytest.mark.parametrize(
    ("path", "payload", "partial"),
    [
        ("/v1/default/banks/b/stats", ["not", "an", "object"], ["queue", "banks"]),
        ("/v1/default/banks/b/stats", "text", ["queue", "banks"]),
        (
            "/v1/default/banks/b/operations",
            {"operations": "nope", "total": 3},
            ["extraction", "queue"],
        ),
        ("/v1/default/banks/b/operations", {"total": 3}, ["extraction", "queue"]),
        ("/v1/default/banks/b/operations", None, ["extraction", "queue"]),
        ("/v1/default/banks/b/mental-models", {"items": {"a": 1}}, ["knowledge"]),
        ("/v1/default/banks/b/mental-models", [], ["knowledge"]),
    ],
)
def test_a_malformed_engine_response_is_partial_not_a_5xx(
    client: TestClient, engine: _Engine, path: str, payload: Any, partial: list[str]
) -> None:
    engine.add_bank("b")
    engine.overrides = {path: (200, payload)}
    body = _health(client)
    assert body["partial"] == partial
    assert body["verdict"] in {"clear", "attention"}


def test_malformed_rows_inside_a_well_formed_list_are_skipped(
    client: TestClient, engine: _Engine
) -> None:
    engine.add_bank("b")
    engine.overrides = {
        "/v1/default/banks/b/operations": (
            200,
            {
                "total": "many",
                "operations": [
                    None,
                    7,
                    {"status": "pending", "created_at": "not a date"},
                    {"status": "pending"},
                    {"status": "failed", "task_type": "retain", "error_message": {"x": 1}},
                    {"status": "PENDING", "created_at": _ago(100.0), "next_retry_at": 12},
                ],
            },
        ),
        "/v1/default/banks/b/mental-models": (
            200,
            {"items": [None, "x", {"is_stale": "yes", "last_refreshed_at": 5}], "total": -1},
        ),
        "/v1/default/banks/b/stats": (200, {"total_documents": "12", "pending_consolidation": -4}),
    }
    body = _health(client)
    assert body["partial"] == []
    # With no usable total the rows read are the count, each list by its own status.
    assert (body["queue"]["waiting"], body["queue"]["failed"]) == (3, 1)
    assert 95.0 <= body["queue"]["oldest_waiting_s"] <= 130.0
    assert body["knowledge"]["models"] == 1 and body["knowledge"]["models_stale"] == 0
    assert body["banks"]["list"] == [{"bank_id": "b", "facts": 10, "documents": None}]
    assert body["queue"]["consolidation_pending"] == 0


def test_a_malformed_bank_list_is_partial_everywhere_it_matters(
    client: TestClient, engine: _Engine
) -> None:
    engine.overrides = {"/v1/default/banks": (200, {"banks": "nope"})}
    body = _health(client)
    assert body["verdict"] == "clear"  # the engine answered /version
    assert body["partial"] == ["extraction", "queue", "knowledge", "banks"]
    assert body["banks"] == {"layout": "legacy_unified", "total": None, "facts": None, "list": []}


@pytest.mark.parametrize("layout", ["legacy_unified", "legacy_split", "two_bank"])
def test_the_bank_layout_is_the_providers(engine: _Engine, layout: str) -> None:
    """ADR-0058 decision 7: ``banks.layout`` comes from the layout record,
    which the provider was built from."""
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(engine.handler), base_url="http://127.0.0.1:9177"
    )
    provider = _Provider(HindsightRestClient(http_client=http, api_key="hal0-local-noauth"))
    provider.layout = layout
    with TestClient(_app(provider, "memory", "agent")) as c:
        assert _health(c)["banks"]["layout"] == layout


def test_the_real_provider_reports_its_layout(engine: _Engine) -> None:
    from hal0.memory.hindsight_provider import HindsightProvider

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(engine.handler), base_url="http://127.0.0.1:9177"
    )
    rest = HindsightRestClient(http_client=http, api_key="hal0-local-noauth")
    provider = HindsightProvider(client=rest, two_bank=True, bank="hal0:shared")
    with TestClient(_app(provider, "memory", "agent")) as c:
        assert _health(c)["banks"]["layout"] == "two_bank"


# ── cache ────────────────────────────────────────────────────────────────


def test_a_second_call_inside_the_ttl_is_served_from_the_cache(
    client: TestClient, engine: _Engine
) -> None:
    engine.add_bank("shared", ops=[_op("pending", 5.0)])
    first = _health(client)
    seen = len(engine.requests)
    engine.banks["shared"]["ops"].append(_op("failed", 60.0, error="late"))
    second = _health(client)
    assert len(engine.requests) == seen
    assert second == first


def test_the_cache_expires(client: TestClient, engine: _Engine) -> None:
    engine.add_bank("shared")
    assert _health(client)["queue"]["failed"] == 0
    seen = len(engine.requests)
    engine.banks["shared"]["ops"].append(_op("failed", 60.0, error="late"))
    stamp, result = client.app.state.memory_health_cache
    client.app.state.memory_health_cache = (stamp - collect.CACHE_TTL_S - 1.0, result)
    assert _health(client)["queue"]["failed"] == 1
    assert len(engine.requests) > seen


def test_an_unreachable_result_is_cached_too(client: TestClient, engine: _Engine) -> None:
    engine.down = True
    _health(client)
    seen = len(engine.requests)
    assert _health(client)["verdict"] == "unreachable"
    assert len(engine.requests) == seen


# ── memory off ───────────────────────────────────────────────────────────


def test_memory_disabled_says_so_and_has_no_steps(engine: _Engine) -> None:
    with TestClient(_app(None)) as c:
        body = _health(c)
    assert {k: v for k, v in body.items() if k != "checked_at"} == {
        "enabled": False,
        "reason": "memory_disabled",
        "verdict": None,
        "partial": [],
        "steps": [],
    }
    assert engine.requests == []


def test_the_in_process_engine_has_no_health_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(memory_health, "_engine_is_pgvector", lambda: True)
    with TestClient(_app(_Provider(None))) as c:
        body = _health(c)
    assert (body["enabled"], body["reason"], body["steps"]) == (True, "engine_unsupported", [])


# ── exposure ─────────────────────────────────────────────────────────────


def test_route_is_admin_by_the_memory_prefix_rule() -> None:
    assert classify("GET", "/api/memory/health") is AuthClass.ADMIN
    rule = match_rule("GET", "/api/memory/health")
    assert rule is not None and rule.label == "memory"
    # Not swept up by the OPEN liveness rules, which match exactly.
    assert classify("GET", "/api/health") is AuthClass.OPEN


def test_route_is_mounted_in_the_real_app(client_app: TestClient) -> None:
    body = client_app.get("/api/memory/health")
    assert body.status_code == 200
    assert "steps" in body.json()


@pytest.fixture
def client_app(tmp_hal0_home: str) -> Iterator[TestClient]:
    from hal0.api import create_app

    with TestClient(create_app()) as c:
        yield c


# ── error messages ───────────────────────────────────────────────────────

_WALL = (
    "Task exceeded the {n}s wall-clock limit for 'batch_retain' "
    "(stage=llm.openai.retain_extract_facts+structured.queued) and was cancelled. "
    "Raise HINDSIGHT_API_RETAIN_WALL_TIMEOUT if this is a legitimately long operation, "
    "or set it to 0 to disable the limit."
)


def test_top_errors_group_after_ids_and_numbers_are_normalised(
    client: TestClient, engine: _Engine
) -> None:
    ops = [
        _op("failed", 500.0, error=_WALL.format(n=14400), changed_s=100.0),
        _op("failed", 500.0, error=_WALL.format(n=3600), changed_s=100.0),
        _op(
            "failed",
            400.0,
            error="document 7f3c2a10-9b1e-4c55-8d2a-0a1b2c3d4e5f: chunk 12 of 40 refused",
            changed_s=90.0,
        ),
        _op(
            "failed",
            400.0,
            error="document 11111111-2222-4333-8444-555555555555: chunk 3 of 9 refused",
            changed_s=90.0,
        ),
        _op(
            "failed",
            400.0,
            error="document 99999999-2222-4333-8444-555555555555: chunk 1 of 2 refused",
            changed_s=90.0,
        ),
        _op("failed", 300.0, error="Error code: 402 - upstream said no", changed_s=80.0),
        _op("failed", 300.0, error="Error code: 500 - upstream said no", changed_s=80.0),
        _op("failed", 300.0, error=None, changed_s=80.0),
    ]
    engine.add_bank("shared", ops=ops)
    body = _health(client)
    assert body["extraction"]["failures_6h"] == 8
    top = body["extraction"]["top_errors"]
    assert top[0] == {"message": "document <id>: chunk <n> of <n> refused", "count": 3}
    assert top[1]["count"] == 2
    assert top[1]["message"].startswith("Task exceeded the <n>s wall-clock limit for")
    # An HTTP status is a category: the two do not merge.
    assert {row["message"]: row["count"] for row in top[2:]} == {
        "Error code: 402 - upstream said no": 1,
        "Error code: 500 - upstream said no": 1,
    }
    for row in top:
        assert len(row["message"]) <= collect.ERROR_MESSAGE_MAX_CHARS


def test_top_errors_are_capped_at_five_groups(client: TestClient, engine: _Engine) -> None:
    names = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf"]
    ops = [_op("failed", 60.0, error=f"{name} broke", changed_s=30.0) for name in names]
    ops.append(_op("failed", 60.0, error="golf broke", changed_s=30.0))
    engine.add_bank("shared", ops=ops)
    top = _health(client)["extraction"]["top_errors"]
    assert len(top) == collect.TOP_ERRORS_MAX
    assert top[0] == {"message": "golf broke", "count": 2}


# Assembled here so no credential-shaped literal sits in the tree. It is not a
# key for anything.
_SECRET = "-".join(["sk", "test", "9f8e7d6c5b4a3928" + "1706f5e4d3c2b1a0"])
_BODY_TEXT = "the operator's private note about the migration"


@pytest.mark.parametrize(
    "raw",
    [
        f"LLM call failed: Authorization: Bearer {_SECRET}",
        f"LLM call failed with api_key={_SECRET} at step 3",
        f'LLM call failed: {{"model": "m", "messages": [{{"content": "{_BODY_TEXT}"}}]}}',
        f"LLM call failed, request body: {_BODY_TEXT}",
        f"LLM call failed, prompt={_BODY_TEXT}",
        f"LLM call failed for https://user:{_SECRET}@llm.example.test/v1",
        f"LLM call failed\nTraceback (most recent call last):\n  {_BODY_TEXT}\n  {_SECRET}",
        f"LLM call failed token {_SECRET}",
    ],
)
def test_error_messages_carry_no_secret_and_no_request_body(raw: str) -> None:
    message = collect.normalise_error(raw)
    assert message.startswith("LLM call failed")
    assert _SECRET not in message
    assert _BODY_TEXT not in message


# Too short to look opaque: only a rule about where it stands can catch it.
_SHORT = "".join(["Tr0ub", "4dor"])


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # A provider echoing the key it refused: the line ends at the word.
        (
            f"Error code: 401 - Incorrect API key provided: {_SHORT}. Find yours at the console",
            "Error code: 401 - Incorrect API key",
        ),
        (f"LLM call failed: password={_SHORT} at step 3", "LLM call failed: password"),
        # A secret-named variable: the shared redactor's shape.
        (f"LLM call failed: HF_TOKEN={_SHORT} rejected", None),
    ],
)
def test_error_messages_drop_a_short_secret_by_its_context(raw: str, expected: str | None) -> None:
    message = collect.normalise_error(raw)
    assert _SHORT not in message
    if expected is not None:
        assert message == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, ""),
        ({"detail": "x"}, ""),
        ("", ""),
        ("\n\n  second line is the first with text  \n", "second line is the first with text"),
        ("connect to 192.0.2.10:8080 refused", "connect to <host> refused"),
        ("[Errno 111] Connection refused", "[Errno <n>] Connection refused"),
        ("model mm-31c911ae502648b49e5532e03fdc611e not found", "model <id> not found"),
        ("bge-small-en-v1.5 is not loaded", "bge-small-en-v1.5 is not loaded"),
        ("HTTP 429 after 3 retries in 12.5s", "HTTP 429 after <n> retries in <n>s"),
    ],
)
def test_normalise_error(raw: Any, expected: str) -> None:
    assert collect.normalise_error(raw) == expected


def test_normalise_error_caps_the_length() -> None:
    message = collect.normalise_error("word " * 400)
    assert len(message) == collect.ERROR_MESSAGE_MAX_CHARS
    assert message.endswith("…")


# ── /api/memory/lanes takes the same paged figures ───────────────────────


def test_lanes_rows_are_read_past_the_first_page(client: TestClient, engine: _Engine) -> None:
    ops = [_op("pending", 10.0 + i) for i in range(130)]
    ops.append(_op("pending", 2 * 3600.0))
    ops += [_op("failed", 500.0, error="x") for _ in range(25)]
    ops += [_op("processing", 90.0), _op("pending", 9000.0, task_type="batch_retain", parent=True)]
    engine.add_bank("shared", ops=ops)
    rows = client.get("/api/memory/lanes").json()["banks"]
    assert len(rows) == 1
    row = rows[0]
    assert {k: row[k] for k in ("bank_id", "pending", "processing", "failed")} == {
        "bank_id": "shared",
        "pending": 131,
        "processing": 1,
        "failed": 25,
    }
    assert 2 * 3600.0 <= row["oldest_pending_age_s"] <= 2 * 3600.0 + 30.0
