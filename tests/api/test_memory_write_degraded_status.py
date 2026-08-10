"""#1420 — ``/api/status`` must surface retain-pipeline health, not just
daemon reachability.

The wire contract asserted here:

* ``memory_degraded`` keeps its #1301 meaning exactly (daemon reachability).
* ``memory_write_degraded`` is a NEW, independent boolean for the retain path.
* ``memory_write_health`` carries the operator-facing detail the issue asked
  ``hal0 memory status`` to print — the reason plus the engine's own
  failed/pending operation counts, which is the only thing on the whole
  surface that could distinguish lxc105 from a healthy box.

#1792 adds a second layer on top: on a fresh install every llm slot ships
model-less, so a growing failed-operation count can ALSO mean "no chat model
has ever been loaded yet" rather than a genuine outage. The tests below that
exercise generic failure detection wire a routable extraction slot
(``_route_extraction_slot``) so they keep testing that shape specifically;
the ``TestNoChatModelWindow`` class covers the model-less case and the
auto-retry it triggers once a model appears.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.config.loader import save_hal0_config
from hal0.config.schema import Hal0Config, MemoryConfig
from hal0.memory.hindsight_provider import HindsightProvider


def _route_extraction_slot(monkeypatch: pytest.MonkeyPatch, *slots: str) -> None:
    """Make ``_extraction_target_resolves`` see ``slots`` as model-bound.

    Patches the same ``_enabled_llm_slots`` helper the route layer calls, so
    tests don't need to stand up a real SlotManager + model-bound config just
    to prove a slot resolves.
    """
    import hal0.api.routes.memory as memory_routes

    async def _fake_enabled_llm_slots(_request: Any) -> list[str]:
        return list(slots)

    monkeypatch.setattr(memory_routes, "_enabled_llm_slots", _fake_enabled_llm_slots)


class _AcceptingClient:
    """A daemon that accepts every retain and serves recalls — while its
    extraction pipeline fails behind the queue. The lxc105 shape."""

    def __init__(self) -> None:
        self.failed = 170

    async def retain(self, **_kwargs: Any) -> dict[str, str]:
        return {"operation_id": "op-1"}

    async def recall(self, **_kwargs: Any) -> dict[str, list[Any]]:
        return {"results": []}

    async def request_json(
        self, method: str, path: str, *, params: dict[str, Any] | None = None, **_kw: Any
    ) -> Any:
        status = (params or {}).get("status")
        totals = {"failed": self.failed, "pending": 2, "processing": 0}
        return {"total": totals.get(str(status), 0)}


def _build(enabled: bool) -> tuple[Any, TestClient]:
    save_hal0_config(Hal0Config(memory=MemoryConfig(enabled=enabled)))
    app = create_app()
    return app, TestClient(app)


def test_status_exposes_the_write_health_fields(client: TestClient) -> None:
    body = client.get("/api/status").json()
    assert "memory_write_degraded" in body
    assert "memory_write_health" in body


def test_write_fields_are_none_when_memory_is_disabled(tmp_hal0_home: str) -> None:
    _app, c = _build(False)
    with c:
        body = c.get("/api/status").json()
    assert body["memory_enabled"] is False
    assert body["memory_degraded"] is None
    assert body["memory_write_degraded"] is None


@pytest.mark.asyncio
async def test_failing_retain_shows_up_on_status_without_touching_memory_degraded(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE regression: every retain fails, ``memory_degraded`` stays false.

    Extraction slot routed as resolvable (#1792) — this test is about a
    genuine failure with a model already bound, not the no-chat-model window
    (that's ``TestNoChatModelWindow`` below).
    """
    _route_extraction_slot(monkeypatch, "utility")
    app, c = _build(True)
    engine = _AcceptingClient()
    provider = HindsightProvider(client=engine, client_id="hermes", unified_bank=True)
    app.state.memory_provider = provider

    # Two samples of the engine's operation counters, the second worse. The
    # verdict is TTL-cached, so force the second rather than sleeping — the
    # route reads whatever the provider last concluded.
    await provider.write_health()
    engine.failed += 3
    await provider.write_health(max_age_s=0)

    with c:
        body = c.get("/api/status").json()

    assert body["memory_enabled"] is True
    # #1301's flag is about the DAEMON, which is answering fine. Unchanged.
    assert body["memory_degraded"] is False
    # The new one tells the truth about writes.
    assert body["memory_write_degraded"] is True
    health = body["memory_write_health"]
    assert health["reason"] == "retain_operations_failing"
    assert health["operations"]["failed"] == 173
    assert health["operations"]["pending"] == 2


def test_healthy_engine_reports_writes_healthy(tmp_hal0_home: str) -> None:
    app, c = _build(True)
    engine = _AcceptingClient()
    engine.failed = 0
    provider = HindsightProvider(client=engine, client_id="hermes", unified_bank=True)
    app.state.memory_provider = provider

    with c:
        body = c.get("/api/status").json()

    assert body["memory_degraded"] is False
    assert body["memory_write_degraded"] is False


def test_provider_without_write_health_reports_none(tmp_hal0_home: str) -> None:
    """The PgVector fallback and any third-party provider have no retain
    pipeline to report on — the field must be ``None``, never a false green."""
    from hal0.memory.pgvector_provider import PgVectorProvider

    app, c = _build(True)
    app.state.memory_provider = PgVectorProvider()

    with c:
        body = c.get("/api/status").json()

    assert body["memory_degraded"] is True
    assert body["memory_write_degraded"] is None


# ── #1792: the no-chat-model window ─────────────────────────────────────────


class _NoChatModelClient:
    """Simulates the #1792 shape: every retain is accepted, but its
    extraction call 404s ``dispatch.no_route`` because no llm slot has a
    model bound yet — so a set of ops sit permanently ``failed`` until
    something retries them. Retrying an id here "succeeds", same as the real
    engine once a route exists.
    """

    def __init__(self, failed_ids: list[str]) -> None:
        self._failed_ids = list(failed_ids)
        self.retried: list[str] = []

    async def retain(self, **_kwargs: Any) -> dict[str, str]:
        return {"operation_id": "op-new"}

    async def recall(self, **_kwargs: Any) -> dict[str, list[Any]]:
        return {"results": []}

    async def request_json(
        self, method: str, path: str, *, params: dict[str, Any] | None = None, **_kw: Any
    ) -> Any:
        params = params or {}
        if method == "GET" and path.endswith("/operations"):
            if params.get("limit") == 1:
                # The count-only sample write_health() sums per status.
                counts = {"failed": len(self._failed_ids), "pending": 0, "processing": 0}
                return {"total": counts.get(str(params.get("status")), 0)}
            # The full listing the auto-retry sweep walks.
            return {"operations": [{"id": op_id} for op_id in self._failed_ids]}
        if method == "POST" and path.endswith("/retry"):
            op_id = path.rsplit("/", 2)[1]
            if op_id in self._failed_ids:
                self._failed_ids.remove(op_id)
                self.retried.append(op_id)
            return {"success": True}
        raise AssertionError(f"unexpected call: {method} {path} {params}")


@pytest.mark.asyncio
async def test_no_chat_model_downgrades_failing_to_a_waiting_state(
    tmp_hal0_home: str,
) -> None:
    """No monkeypatch here: a fresh test app's SlotManager has no model-bound
    llm slots by default, matching a real fresh install — exactly the shape
    #1792 needs ``hal0 memory status`` to stop calling FAILING."""
    app, c = _build(True)
    engine = _NoChatModelClient(["op-1", "op-2"])
    provider = HindsightProvider(client=engine, client_id="hermes", unified_bank=True)
    app.state.memory_provider = provider

    await provider.write_health()
    engine._failed_ids.append("op-3")
    await provider.write_health(max_age_s=0)
    assert provider.write_degraded is True, "sanity: the engine-level signal is degraded"

    with c:
        body = c.get("/api/status").json()

    # The honest status (#1792): not FAILING, and the reason says why.
    assert body["memory_write_degraded"] is False
    health = body["memory_write_health"]
    assert health["waiting_on"] == "chat_model"
    assert health["reason"] == "no_chat_model"


@pytest.mark.asyncio
async def test_auto_retry_fires_once_a_chat_model_resolves(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, c = _build(True)
    engine = _NoChatModelClient(["op-1", "op-2"])
    provider = HindsightProvider(client=engine, client_id="hermes", unified_bank=True)
    app.state.memory_provider = provider

    await provider.write_health()
    engine._failed_ids.append("op-3")
    await provider.write_health(max_age_s=0)

    # A chat model just became routable — the extraction slot now resolves.
    _route_extraction_slot(monkeypatch, "utility")

    with c:
        body = c.get("/api/status").json()

    # The auto-retry swept every dead-lettered op on the bank it tracks.
    assert sorted(engine.retried) == ["op-1", "op-2", "op-3"]
    assert body["memory_write_health"]["operations"]["failed"] == 0


@pytest.mark.asyncio
async def test_auto_retry_is_bounded_by_a_cooldown(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second poll inside the cooldown window must not sweep again — the
    dashboard/status poll happens every few seconds and must never turn into
    a retry-storm against the engine."""
    app, c = _build(True)
    engine = _NoChatModelClient(["op-1"])
    provider = HindsightProvider(client=engine, client_id="hermes", unified_bank=True)
    app.state.memory_provider = provider

    await provider.write_health()
    engine._failed_ids = ["op-1", "op-2"]
    await provider.write_health(max_age_s=0)

    _route_extraction_slot(monkeypatch, "utility")

    with c:
        c.get("/api/status")
        first_sweep_count = provider._auto_retry_sweeps_done
        # Re-dead-letter an op to prove a second sweep, if it ran, would be
        # observable — then poll again immediately.
        engine._failed_ids.append("op-3")
        provider._write_health = None
        provider._write_health_at = None
        c.get("/api/status")

    assert provider._auto_retry_sweeps_done == first_sweep_count == 1
    assert "op-3" not in engine.retried
