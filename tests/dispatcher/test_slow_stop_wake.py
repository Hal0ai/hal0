"""#2738 — a stop that outlived the 30 s budget must not strand the slot.

After the teardown finished, the slot sat in a sticky ERROR; once hal0-api
restarted (upstream registry empty, ``state.json`` still ERROR)
``_wake_capability_slot`` skipped it and every capability request 404'd
``dispatch.no_route``. The convergence now runs from the wake path too.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from starlette.responses import Response

from hal0.api.routes.v1 import _dispatch_and_forward
from hal0.dispatcher.router import Dispatcher
from hal0.slots.manager import SlotManager
from hal0.slots.state import SlotState, SlotTerminateTimeout
from hal0.upstreams.registry import UpstreamRegistry
from tests.dispatcher.test_capability_wake_on_evict import _make_request, _write_min_slot
from tests.slots.conftest import FakeContainerProvider


async def test_capability_request_wakes_slot_after_slow_stop_and_api_restart(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeContainerProvider()
    monkeypatch.setattr("hal0.providers.container.container_provider", lambda: fake)
    _write_min_slot(Path(tmp_hal0_home) / "etc" / "hal0" / "slots", "rerank", port=8090)

    sm = SlotManager(upstreams_registry=UpstreamRegistry())
    await sm.load("rerank")
    # The stop budget elapsed (state stamped ERROR + slow_stop), then the
    # teardown finished: unit removed, nothing running.
    sm._terminate_timeout_s = 0.0
    fake.active.discard("rerank")
    await sm._transition(
        "rerank",
        SlotState.ERROR,
        message=str(SlotTerminateTimeout("stopping slot 'rerank' did not return within 30.0s")),
        extra={"slow_stop": True},
        force=True,
    )
    fake.removed_cleanly.add("rerank")

    # hal0-api restart: fresh manager + empty registry, state.json persisted.
    registry = UpstreamRegistry()
    fresh = SlotManager(upstreams_registry=registry)
    assert fresh.state("rerank") is SlotState.ERROR
    dispatcher = Dispatcher(
        upstream_registry=registry,
        model_registry=None,
        cached_models=lambda _n: [],
        slot_manager=fresh,
    )

    async def _fake_forward(call: Any) -> Response:
        return Response(content=b"{}", media_type="application/json")

    monkeypatch.setattr(dispatcher, "forward", _fake_forward)
    fake.load_calls.clear()

    resp = await _dispatch_and_forward(
        _make_request("/v1/rerankings", fresh),
        dispatcher,
        body={"model": "bge-reranker-v2-m3", "query": "q", "documents": ["d"]},
    )

    assert resp.status_code == 200
    assert len(fake.load_calls) == 1, "wake must have loaded the converged slot"
    assert (await fresh.status("rerank")).state == SlotState.READY
