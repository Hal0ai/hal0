"""Integration tests for ``/api/memory/graph/*`` (ADR-0023).

Builds a bare FastAPI app + a stub wrapper so we don't have to spin up a full
hal0 lifespan or a real memory engine. Pins the contract the
``hal0 memory graph`` CLI + the dashboard Memory tab depend on:

  - ``GET /graph/status`` returns ``extraction_slot`` (+ deprecated ``route``
    mirror), ``slot_resolves``, and ``available_slots``.
  - ``PUT /graph`` validates ``extraction_slot`` against the live enabled-llm
    slot set, rejecting an unknown slot with 422 and NOT flipping the gate.
  - ``PUT /graph`` propagates a slot change to hindsight-api (drop-in + restart)
    and echoes a ``propagation`` block.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import tomli_w
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api.middleware import error_codes
from hal0.api.routes import memory as memory_routes


class StubWrapper:
    """Minimal duck-type matching the provider ``graph_*`` surface (ADR-0023)."""

    def __init__(self, *, enabled: bool = False, extraction_slot: str = "utility") -> None:
        self.enabled = enabled
        self.extraction_slot = extraction_slot
        self.set_calls: list[tuple[bool, str | None]] = []

    def graph_status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "extraction_slot": self.extraction_slot,
            "route": self.extraction_slot,  # deprecated mirror
            "in_flight": 0,
            "builds_ok": 0,
            "errors": 0,
            "last_built_at": None,
            "last_error": None,
        }

    def set_graph_enabled(self, enabled: bool, extraction_slot: str | None = None) -> None:
        self.set_calls.append((enabled, extraction_slot))
        self.enabled = enabled
        if extraction_slot is not None:
            self.extraction_slot = extraction_slot


def _slot_manager(*names: str) -> MagicMock:
    """A slot_manager whose iter_configs returns the given enabled llm slots."""
    mgr = MagicMock()
    mgr.iter_configs = AsyncMock(
        return_value=[
            {"name": n, "type": "llm", "enabled": True, "model_id": f"model-{n}"} for n in names
        ]
    )
    return mgr


@pytest.fixture
def hal0_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point loader at a tmp HAL0_HOME with a minimal hal0.toml."""
    home = tmp_path / "hal0-home"
    etc = home / "etc"
    etc.mkdir(parents=True)
    (home / "var-lib").mkdir(parents=True)
    (etc / "hal0.toml").write_text(
        tomli_w.dumps(
            {
                "meta": {"schema_version": 1},
            }
        )
    )
    monkeypatch.setenv("HAL0_HOME", str(home))
    return home


@pytest.fixture
def stub_wrapper() -> StubWrapper:
    return StubWrapper()


def _build_app(stub: StubWrapper, *slot_names: str) -> FastAPI:
    app = FastAPI()
    error_codes.install(app)
    app.include_router(memory_routes.router, prefix="/api/memory", tags=["memory"])
    app.state.memory_provider = stub
    app.state.slot_manager = _slot_manager(*slot_names) if slot_names else None
    return app


@pytest.fixture
def client(stub_wrapper: StubWrapper, hal0_home: Path) -> Iterator[TestClient]:
    # Default app advertises agent + utility as enabled llm slots.
    app = _build_app(stub_wrapper, "agent", "utility")
    with TestClient(app) as c:
        yield c


def test_graph_status_default_returns_off(client: TestClient, stub_wrapper: StubWrapper) -> None:
    r = client.get("/api/memory/graph/status")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False
    assert body["extraction_slot"] == "utility"
    # Deprecated mirror still present for the old dashboard.
    assert body["route"] == "utility"
    assert body["builds_ok"] == 0


def test_graph_status_reports_available_slots_and_resolution(
    client: TestClient, stub_wrapper: StubWrapper
) -> None:
    r = client.get("/api/memory/graph/status")
    body = r.json()
    # available_slots is the live enabled-llm-slot alias set; it includes the
    # canonical roles plus any injected back-compat alias (agent-hermes→agent).
    assert {"agent", "utility"} <= set(body["available_slots"])
    # default extraction_slot=utility is among the enabled llm slots
    assert body["slot_resolves"] is True


def test_put_enable_with_valid_slot(
    client: TestClient, stub_wrapper: StubWrapper, hal0_home: Path
) -> None:
    with patch("hal0.memory.extraction_env.apply_extraction_slot") as apply_mock:
        apply_mock.return_value = {"slot": "agent", "written": True, "error": None}
        r = client.put(
            "/api/memory/graph",
            json={"enabled": True, "extraction_slot": "agent"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is True
    assert body["extraction_slot"] == "agent"
    # The wrapper was flipped on with the new slot.
    assert stub_wrapper.set_calls == [(True, "agent")]
    # Slot changed → propagation echoed.
    assert body["propagation"]["slot"] == "agent"


def test_put_enable_with_unknown_slot_rejected(
    client: TestClient, stub_wrapper: StubWrapper, hal0_home: Path
) -> None:
    # ADR-0023 — extraction_slot must name an enabled llm slot. An unknown slot
    # is rejected (422) and the gate stays off.
    r = client.put(
        "/api/memory/graph",
        json={"enabled": True, "extraction_slot": "does-not-exist"},
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "config.memory_graph_slot_invalid"
    assert stub_wrapper.set_calls == []
    assert stub_wrapper.enabled is False


def test_put_enable_without_slot_change_keeps_default(
    client: TestClient, stub_wrapper: StubWrapper, hal0_home: Path
) -> None:
    # Flipping enabled with no extraction_slot keeps the default (utility) and
    # does NOT trigger propagation (slot unchanged).
    r = client.put("/api/memory/graph", json={"enabled": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is True
    assert body["extraction_slot"] == "utility"
    assert "propagation" not in body
    assert stub_wrapper.set_calls == [(True, "utility")]


def test_put_disable_idempotent(client: TestClient, stub_wrapper: StubWrapper) -> None:
    r = client.put("/api/memory/graph", json={"enabled": False})
    assert r.status_code == 200
    assert r.json()["enabled"] is False


def test_put_bad_slot_name_returns_400(client: TestClient, stub_wrapper: StubWrapper) -> None:
    # Grammar failure (uppercase) is a schema error → 400, distinct from the
    # 422 "valid name but not a live slot" case above.
    r = client.put("/api/memory/graph", json={"extraction_slot": "BadName"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "config.memory_graph_invalid"


class _HindsightyWrapper(StubWrapper):
    """Wrapper that also exposes a hindsight_client, so graph/status can
    aggregate real per-bank extraction counters (#1025)."""

    def __init__(self, stats_by_bank: dict[str, Any]) -> None:
        super().__init__()
        self._stats = stats_by_bank
        parent = self

        class _Client:
            async def request_json(self, method: str, path: str) -> Any:
                if path == "/v1/default/banks":
                    return {"banks": [{"bank_id": b} for b in parent._stats]}
                for bank, stats in parent._stats.items():
                    if path == f"/v1/default/banks/{bank}/stats":
                        return stats
                raise AssertionError(f"unexpected path {path}")

        self.hindsight_client = _Client()


def test_graph_status_aggregates_real_counters_from_hindsight(hal0_home: Path) -> None:
    # #1025: graph/status must reflect ACTUAL extraction activity read back from
    # Hindsight per-bank /stats, not the provider's 0/None placeholders.
    wrapper = _HindsightyWrapper(
        {
            "shared": {
                "operations_by_status": {"completed": 3, "processing": 1, "failed": 0},
                "pending_operations": 2,
                "failed_operations": 1,
                "last_consolidated_at": "2026-07-04T01:00:00Z",
            },
            "private__hermes": {
                "operations_by_status": {"completed": 5},
                "last_consolidated_at": "2026-07-04T02:00:00Z",
            },
        }
    )
    app = _build_app(wrapper, "utility")
    with TestClient(app) as c:
        body = c.get("/api/memory/graph/status").json()
    assert body["builds_ok"] == 8  # 3 + 5 completed
    assert body["in_flight"] == 3  # 2 pending + 1 processing
    assert body["errors"] == 1  # failed_operations on shared
    assert body["last_built_at"] == "2026-07-04T02:00:00Z"  # newest across banks


def test_status_unavailable_when_no_wrapper(
    hal0_home: Path,
) -> None:
    app = FastAPI()
    error_codes.install(app)
    app.include_router(memory_routes.router, prefix="/api/memory", tags=["memory"])
    app.state.memory_provider = None
    app.state.slot_manager = None
    with TestClient(app) as c:
        r = c.get("/api/memory/graph/status")
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "memory.unavailable"


# ── POST /api/memory/graph/retry ─────────────────────────────────────────────


class _RetryWrapper(StubWrapper):
    """Wrapper exposing a hindsight_client that serves a (single-page) operations
    ledger and records retry POSTs, so we can drive ``POST /graph/retry``."""

    def __init__(self, ops_by_bank: dict[str, list[dict[str, Any]]]) -> None:
        super().__init__()
        self._ops = ops_by_bank
        self.retried: list[str] = []
        parent = self

        class _Client:
            async def request_json(self, method: str, path: str) -> Any:
                if method == "GET" and path == "/v1/default/banks":
                    return {"banks": [{"bank_id": b} for b in parent._ops]}
                for bank, ops in parent._ops.items():
                    if method == "GET" and path.startswith(
                        f"/v1/default/banks/{bank}/operations?"
                    ):
                        # A page shorter than the limit ends the walk, so return
                        # the whole ledger on offset 0 and nothing after.
                        return {"operations": ops if "offset=0" in path else []}
                    prefix = f"/v1/default/banks/{bank}/operations/"
                    if method == "POST" and path.startswith(prefix) and path.endswith("/retry"):
                        op_id = path[len(prefix) : -len("/retry")]
                        parent.retried.append(op_id)
                        return {"success": True, "operation_id": op_id}
                raise AssertionError(f"unexpected {method} {path}")

        self.hindsight_client = _Client()


def test_retry_failed_requeues_only_failed_ops(hal0_home: Path) -> None:
    # Only ``failed`` ops are requeued; completed/processing are left alone.
    wrapper = _RetryWrapper(
        {
            "shared": [
                {"id": "a", "status": "failed"},
                {"id": "b", "status": "completed"},
                {"id": "c", "status": "failed"},
            ],
            "private__hermes": [
                {"id": "d", "status": "failed"},
                {"id": "e", "status": "processing"},
            ],
        }
    )
    app = _build_app(wrapper, "utility")
    with TestClient(app) as c:
        r = c.post("/api/memory/graph/retry")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["queued"] == 3  # a, c, d
    assert body["skipped"] == 0
    assert set(wrapper.retried) == {"a", "c", "d"}
    assert body["banks"]["shared"]["queued"] == 2
    assert body["banks"]["private__hermes"]["queued"] == 1
    assert body["banks"]["private__hermes"]["failed"] == 1


def test_retry_failed_noop_when_ledger_clean(hal0_home: Path) -> None:
    wrapper = _RetryWrapper({"shared": [{"id": "x", "status": "completed"}]})
    app = _build_app(wrapper, "utility")
    with TestClient(app) as c:
        r = c.post("/api/memory/graph/retry")
    assert r.status_code == 200
    assert r.json()["queued"] == 0
    assert wrapper.retried == []


def test_retry_failed_unavailable_without_hindsight_client(hal0_home: Path) -> None:
    # Provider present but no hindsight_client → 503 (can't reach the ledger).
    app = _build_app(StubWrapper(), "utility")
    with TestClient(app) as c:
        r = c.post("/api/memory/graph/retry")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "memory.unavailable"
