"""DR-1 regression: idle-EVICTED capability slots must wake on request.

The idle sweeper unloads embed/rerank/tts/img slots to OFFLINE and
DEREGISTERS their container upstream. A follow-up capability request then
404s inside ``resolve_by_capability`` (surfaced as ``NoRouteFound``) long
before it can reach the dispatcher's container readiness gate — so that gate
can never reload the slot (the finding's first proposed fix is insufficient).

The fix is the route-level capability twin of the chat branch's
``_ensure_backend_for_model``: ``_wake_capability_slot`` resolves the slot
from the request PATH and, when it is a known managed slot sitting OFFLINE,
drives ``SlotManager.load(slot)`` — which re-registers the upstream — BEFORE
``dispatcher.dispatch`` runs.

Complements the eviction-only coverage at
``tests/slots/test_pulling_serving_idle.py::test_idle_sweep_unloads_slot_past_ttl``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.requests import Request
from starlette.responses import Response

from hal0.api.routes.v1 import _dispatch_and_forward
from hal0.dispatcher.router import Dispatcher, NoRouteFound
from hal0.slots.manager import SlotManager
from hal0.slots.state import SlotState
from hal0.upstreams.registry import UpstreamRegistry
from tests.slots.conftest import FakeContainerProvider


def _write_min_slot(root: Path, name: str, *, port: int, model: str = "qwen3-4b-q4_k_m") -> None:
    """Write a minimal llama-server slot TOML (mirrors the slots suite helper).

    ``model=""`` writes an INACTIVE slot — since #1369 model-presence is the
    activation signal, so that replaces the old ``enabled=False``.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.toml").write_text(
        "\n".join(
            [
                f'name = "{name}"',
                f"port = {port}",
                'backend = "vulkan"',
                'provider = "llama-server"',
                "[model]",
                f'default = "{model}"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def _make_request(path: str, slot_manager: SlotManager) -> Request:
    """A Starlette Request whose ``app.state.slot_manager`` is wired.

    ``app.state`` is a permissive namespace so the route helpers'
    ``getattr(request.app.state, ..., None)`` probes degrade cleanly for the
    fields this focused test does not populate (upstreams cache, etc.).
    """
    app = SimpleNamespace(state=SimpleNamespace(slot_manager=slot_manager))
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [
            (b"content-type", b"application/json"),
            (b"authorization", b"Bearer test-token"),
        ],
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
        "http_version": "1.1",
        "root_path": "",
        "app": app,
    }
    return Request(scope)


def _rerank_load_count(fake: FakeContainerProvider) -> int:
    return sum(1 for cfg, _mi in fake.load_calls if cfg.get("name") == "rerank")


async def _evict(sm: SlotManager) -> None:
    sm._last_used[sm._key("rerank")] = 0.0  # ancient — well past the 0.01s TTL
    await sm._sweep_idle_once()


@pytest.fixture
def wired(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> tuple[SlotManager, Dispatcher, UpstreamRegistry, FakeContainerProvider]:
    """A SlotManager + Dispatcher sharing ONE upstream registry.

    Mirrors the real ``create_app`` wiring (api/__init__.py:811-818) where the
    manager and dispatcher share the registry — so eviction's deregister and
    ``load``'s re-register are visible to the dispatcher.
    """
    fake = FakeContainerProvider()
    monkeypatch.setattr("hal0.providers.container.container_provider", lambda: fake)

    root = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    _write_min_slot(root, "rerank", port=8090)

    registry = UpstreamRegistry()
    sm = SlotManager(
        idle_after_s=0.0,
        evict_after_s=0.01,
        idle_monitor_interval_s=10.0,
        upstreams_registry=registry,
    )
    dispatcher = Dispatcher(
        upstream_registry=registry,
        model_registry=None,
        cached_models=lambda _name: [],
        slot_manager=sm,
    )
    return sm, dispatcher, registry, fake


async def test_evicted_capability_slot_404s_without_wake(
    wired: tuple[SlotManager, Dispatcher, UpstreamRegistry, FakeContainerProvider],
) -> None:
    """Negative control: after eviction the upstream is deregistered, so a raw
    dispatch (no wake) 404s with ``dispatch.no_route`` — locking the
    born-broken behaviour the fix changes."""
    sm, dispatcher, registry, _fake = wired

    await sm.load("rerank")
    assert (await sm.status("rerank")).state == SlotState.READY
    assert registry.get("rerank") is not None

    await _evict(sm)
    assert (await sm.status("rerank")).state == SlotState.OFFLINE
    assert registry.get("rerank") is None, "eviction must deregister the upstream"

    req = _make_request("/v1/rerankings", sm)
    with pytest.raises(NoRouteFound) as exc:
        await dispatcher.dispatch(
            req, body={"model": "bge-reranker-v2-m3", "query": "q", "documents": ["d"]}
        )
    assert exc.value.code == "dispatch.no_route"


async def test_capability_slot_wakes_on_request_after_eviction(
    wired: tuple[SlotManager, Dispatcher, UpstreamRegistry, FakeContainerProvider],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DR-1: a /v1/rerankings request through the route reloads an evicted
    rerank slot (second container load) and resolves instead of 404ing."""
    sm, dispatcher, registry, fake = wired

    await sm.load("rerank")
    assert _rerank_load_count(fake) == 1

    await _evict(sm)
    assert (await sm.status("rerank")).state == SlotState.OFFLINE
    assert registry.get("rerank") is None

    # Stub forward() so the resolved call is captured without real networking.
    captured: dict[str, Any] = {}

    async def _fake_forward(call: Any) -> Response:
        captured["call"] = call
        return Response(content=b"{}", media_type="application/json")

    monkeypatch.setattr(dispatcher, "forward", _fake_forward)

    req = _make_request("/v1/rerankings", sm)
    resp = await _dispatch_and_forward(
        req,
        dispatcher,
        body={"model": "bge-reranker-v2-m3", "query": "q", "documents": ["d"]},
    )

    # Wake fired: the slot was reloaded (a SECOND container load) and its
    # upstream is re-registered — so dispatch resolved to rerank, not 404.
    assert (await sm.status("rerank")).state == SlotState.READY
    assert registry.get("rerank") is not None
    assert _rerank_load_count(fake) == 2, "wake must drive a second container load"
    assert resp.status_code == 200
    assert captured["call"].upstream_name == "rerank"


async def test_model_less_capability_slot_is_not_woken(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DR-1 ↔ SC-1 interaction: the wake path must NOT revive a CLEARED slot.

    When an operator disables a capability, SC-1 clears ``[model].default``
    (#1369) and the reconciler unloads it to OFFLINE / deregisters its upstream.
    A follow-up capability request must still 404 — waking the slot would start
    its container and re-register the upstream, silently undoing the disable
    (and there is no model to launch anyway). The wake gate mirrors the routing
    layer's model-presence drop rule.
    """
    fake = FakeContainerProvider()
    monkeypatch.setattr("hal0.providers.container.container_provider", lambda: fake)

    root = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    _write_min_slot(root, "rerank", port=8090, model="")

    registry = UpstreamRegistry()
    sm = SlotManager(
        idle_after_s=0.0,
        evict_after_s=0.01,
        idle_monitor_interval_s=10.0,
        upstreams_registry=registry,
    )
    dispatcher = Dispatcher(
        upstream_registry=registry,
        model_registry=None,
        cached_models=lambda _name: [],
        slot_manager=sm,
    )

    # A model-less slot starts OFFLINE with no upstream — the post-disable state.
    assert (await sm.status("rerank")).state == SlotState.OFFLINE
    assert registry.get("rerank") is None

    req = _make_request("/v1/rerankings", sm)
    with pytest.raises(NoRouteFound) as exc:
        await _dispatch_and_forward(
            req,
            dispatcher,
            body={"model": "bge-reranker-v2-m3", "query": "q", "documents": ["d"]},
        )
    assert exc.value.code == "dispatch.no_route"
    # The wake gate skipped it: no container load, no re-registered upstream.
    assert _rerank_load_count(fake) == 0, "a model-less slot must never be woken"
    assert registry.get("rerank") is None
