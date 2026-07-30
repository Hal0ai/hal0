"""#1420 — ``/api/status`` must surface retain-pipeline health, not just
daemon reachability.

The wire contract asserted here:

* ``memory_degraded`` keeps its #1301 meaning exactly (daemon reachability).
* ``memory_write_degraded`` is a NEW, independent boolean for the retain path.
* ``memory_write_health`` carries the operator-facing detail the issue asked
  ``hal0 memory status`` to print — the reason plus the engine's own
  failed/pending operation counts, which is the only thing on the whole
  surface that could distinguish lxc105 from a healthy box.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.config.loader import save_hal0_config
from hal0.config.schema import Hal0Config, MemoryConfig
from hal0.memory.hindsight_provider import HindsightProvider


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
    tmp_hal0_home: str,
) -> None:
    """THE regression: every retain fails, ``memory_degraded`` stays false."""
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
