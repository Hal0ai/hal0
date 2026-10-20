"""Two slots, one model id — selection must prefer a healthy slot (#1418).

On lxc105 both ``brain`` (id 2) and ``nano`` (id 11) bind
``hal0-brain-sft-fpx8``. ``nano`` sat in ERROR (its restart failed) while
``brain`` was loaded, healthy and generating — yet every path that maps that
model id to a slot picked ``nano``:

  * the route layer's backend-aware load (#430) reversed the alias map and took
    the FIRST slot binding the id, so it drove ``load("nano")``;
  * the dispatcher committed to the registry binding ``model → nano`` and its
    readiness gate then raised ``slot.load_failed`` — a hard 502.

Net effect: ``POST /api/brain/chat`` (whose ``BRAIN_SLOT_MODEL`` is
``hal0/brain``) and the model over ``/v1`` were both unreachable.

The contract pinned here: when several slots advertise one model id, selection
ranks candidates by health and never picks an ERROR slot while a healthy one
exists — and a ``hal0/<slot>`` lane keeps its own slot when the lane resolver
matched a live one.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Callable
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from starlette.requests import Request

import hal0.api as hal0_api
from hal0.api.routes.v1 import _ensure_backend_for_model
from hal0.dispatcher.router import Dispatcher, UpstreamUnavailable
from hal0.slots.manager import SlotManager
from hal0.slots.state import DISPATCHABLE_STATES, SlotState, slot_selection_rank
from hal0.upstreams.registry import Upstream, UpstreamRegistry

_MODEL = "hal0-brain-sft-fpx8"

# ── doubles ──────────────────────────────────────────────────────────────────


class _SlotManager:
    """Records ``load`` targets; reports a per-slot state."""

    def __init__(
        self, states: dict[str, SlotState], in_flight: dict[str, int] | None = None
    ) -> None:
        self._states = states
        self._in_flight = in_flight or {}
        self.loaded: list[str] = []

    def state(self, name: str) -> SlotState:
        return self._states.get(name, SlotState.OFFLINE)

    def in_flight_count(self, name: str) -> int:
        return self._in_flight.get(name, 0)

    async def load(self, slot_name: str, model_id: str | None = None) -> None:
        self.loaded.append(slot_name)

    async def iter_configs(self) -> list[dict[str, Any]]:
        return []


class _FakeUpstreams(UpstreamRegistry):
    def __init__(self, upstreams: list[Upstream]) -> None:
        super().__init__()
        self._store = {u.name: u for u in upstreams}

    def list(self) -> list[Upstream]:  # type: ignore[override]
        return list(self._store.values())

    def get(self, name: str) -> Upstream | None:  # type: ignore[override]
        return self._store.get(name)


class _FakeModels:
    def __init__(self, routes: dict[str, str]) -> None:
        self._routes = routes

    def route_for(self, model_id: str) -> str | None:
        return self._routes.get(model_id)


def _container_upstream(name: str, port: int) -> Upstream:
    """A container slot upstream — ``kind="remote"`` + ``slot_name`` (#656)."""
    return Upstream(
        name=name,
        kind="remote",
        url=f"http://127.0.0.1:{port}/v1",
        auth_style="none",
        advertise_models=True,
        slot_name=name,
    )


def _request(lane_slot: str | None = None) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/chat/completions",
        "raw_path": b"/v1/chat/completions",
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
        "http_version": "1.1",
        "root_path": "",
    }
    request = Request(scope)
    if lane_slot:
        request.state.hal0_lane_slot = lane_slot
    return request


def _route_request(sm: _SlotManager, lane_slot: str | None = None) -> Any:
    """Minimal request stand-in for the route-layer helper."""
    request = _request(lane_slot)
    request.scope["app"] = SimpleNamespace(state=SimpleNamespace(slot_manager=sm))
    return request


def _patch_alias_map(monkeypatch: pytest.MonkeyPatch, order: tuple[str, ...]) -> None:
    """Alias map in TOML declaration order — ``nano`` first, as on lxc105."""

    async def _fake(_sm: Any) -> dict[str, str]:
        return {name: _MODEL for name in order}

    monkeypatch.setattr(hal0_api, "hal0_chat_slot_alias_map", _fake)


def _dispatcher(
    upstreams: list[Upstream],
    *,
    states: dict[str, SlotState],
    advertised: dict[str, list[str]] | None = None,
    routes: dict[str, str] | None = None,
    in_flight: dict[str, int] | None = None,
) -> Dispatcher:
    cache = advertised or {}

    async def _online(_u: Upstream) -> bool:
        return True

    return Dispatcher(
        upstream_registry=_FakeUpstreams(upstreams),
        model_registry=_FakeModels(routes or {}),
        cached_models=lambda name: list(cache.get(name, [])),
        is_online=_online,
        slot_manager=_SlotManager(states, in_flight),  # type: ignore[arg-type]
    )


# ── the ranking primitive ────────────────────────────────────────────────────


class TestSlotSelectionRank:
    def test_dispatchable_beats_everything(self) -> None:
        for live in (SlotState.SERVING, SlotState.READY, SlotState.IDLE):
            assert slot_selection_rank(live) < slot_selection_rank(SlotState.WARMING)
            assert slot_selection_rank(live) < slot_selection_rank(SlotState.OFFLINE)
            assert slot_selection_rank(live) < slot_selection_rank(SlotState.ERROR)

    def test_error_is_the_last_resort(self) -> None:
        worst = slot_selection_rank(SlotState.ERROR)
        for state in SlotState:
            if state is not SlotState.ERROR:
                assert slot_selection_rank(state) < worst, state

    def test_loading_states_outrank_offline_and_error(self) -> None:
        for loading in (SlotState.PULLING, SlotState.STARTING, SlotState.WARMING):
            assert slot_selection_rank(loading) < slot_selection_rank(SlotState.OFFLINE)
            assert slot_selection_rank(loading) < slot_selection_rank(SlotState.ERROR)

    def test_accepts_wire_strings(self) -> None:
        assert slot_selection_rank("ready") == slot_selection_rank(SlotState.READY)
        assert slot_selection_rank("error") == slot_selection_rank(SlotState.ERROR)
        # An unknown value must never outrank a real dispatchable state.
        assert slot_selection_rank("banana") > slot_selection_rank(SlotState.READY)


# ── route layer: the backend-aware load target ───────────────────────────────


class TestBackendAwareLoadPicksAHealthySlot:
    async def test_error_slot_is_skipped_for_the_healthy_sibling(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The lxc105 shape: nano (ERROR) declared first, brain (READY) second."""
        _patch_alias_map(monkeypatch, ("nano", "brain"))
        sm = _SlotManager({"nano": SlotState.ERROR, "brain": SlotState.READY})

        await _ensure_backend_for_model(_route_request(sm), {"model": _MODEL})

        assert sm.loaded == ["brain"], sm.loaded

    async def test_lane_pin_wins_when_both_siblings_are_healthy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``hal0/brain`` must land on the slot NAMED brain, not on whichever
        sibling happens to share its model id."""
        _patch_alias_map(monkeypatch, ("nano", "brain"))
        sm = _SlotManager({"nano": SlotState.READY, "brain": SlotState.READY})

        await _ensure_backend_for_model(_route_request(sm, lane_slot="brain"), {"model": _MODEL})

        assert sm.loaded == ["brain"], sm.loaded

    async def test_single_error_slot_is_still_loaded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No healthy candidate → unchanged behaviour (the recovery load runs)."""
        _patch_alias_map(monkeypatch, ("nano",))
        sm = _SlotManager({"nano": SlotState.ERROR})

        await _ensure_backend_for_model(_route_request(sm), {"model": _MODEL})

        assert sm.loaded == ["nano"], sm.loaded

    async def test_declaration_order_breaks_health_ties(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Equally healthy, no lane pin → deterministic declaration order."""
        _patch_alias_map(monkeypatch, ("nano", "brain"))
        sm = _SlotManager({"nano": SlotState.READY, "brain": SlotState.READY})

        await _ensure_backend_for_model(_route_request(sm), {"model": _MODEL})

        assert sm.loaded == ["nano"], sm.loaded


# ── dispatcher: upstream selection ───────────────────────────────────────────


class TestDispatchPrefersAHealthySlotUpstream:
    async def test_container_preemption_skips_the_error_slot(self) -> None:
        """Step 0 walked upstreams in registration order — nano (ERROR) won."""
        dispatcher = _dispatcher(
            [_container_upstream("nano", 8086), _container_upstream("brain", 8087)],
            states={"nano": SlotState.ERROR, "brain": SlotState.READY},
            advertised={"nano": [_MODEL], "brain": [_MODEL]},
        )

        call = await dispatcher.dispatch(_request(), body={"model": _MODEL})

        assert call.upstream_name == "brain", call.resolution_path
        assert call.container_slot_name == "brain"

    async def test_registry_binding_to_an_error_slot_falls_through(self) -> None:
        """The binding ``model → nano`` must not commit while brain is live."""
        dispatcher = _dispatcher(
            [_container_upstream("nano", 8086), _container_upstream("brain", 8087)],
            states={"nano": SlotState.ERROR, "brain": SlotState.READY},
            advertised={"nano": [_MODEL], "brain": [_MODEL]},
            routes={_MODEL: "nano"},
        )

        call = await dispatcher.dispatch(_request(), body={"model": _MODEL})

        assert call.upstream_name == "brain", call.resolution_path

    async def test_lane_pin_selects_its_own_slot_among_healthy_siblings(self) -> None:
        dispatcher = _dispatcher(
            [_container_upstream("nano", 8086), _container_upstream("brain", 8087)],
            states={"nano": SlotState.READY, "brain": SlotState.READY},
            advertised={"nano": [_MODEL], "brain": [_MODEL]},
        )

        call = await dispatcher.dispatch(_request(lane_slot="brain"), body={"model": _MODEL})

        assert call.upstream_name == "brain", call.resolution_path

    async def test_only_candidate_is_still_selected_when_it_is_in_error(self) -> None:
        """One ERROR slot and nothing else → resolve to it exactly as before, so
        the readiness gate keeps owning the retry/recovery envelope."""
        dispatcher = _dispatcher(
            [_container_upstream("nano", 8086)],
            states={"nano": SlotState.ERROR},
            advertised={"nano": [_MODEL]},
            routes={_MODEL: "nano"},
        )

        call = await dispatcher.dispatch(_request(), body={"model": _MODEL})

        assert call.upstream_name == "nano", call.resolution_path

    async def test_healthy_registry_binding_is_untouched(self) -> None:
        """A single healthy bound slot still resolves through the registry."""
        dispatcher = _dispatcher(
            [_container_upstream("brain", 8087)],
            states={"brain": SlotState.READY},
            advertised={"brain": [_MODEL]},
            routes={_MODEL: "brain"},
        )

        call = await dispatcher.dispatch(_request(), body={"model": _MODEL})

        assert call.upstream_name == "brain"
        assert json.loads(call.body)["model"] == _MODEL


# ── #2716: an idle non-utility sibling wins over utility ─────────────────────
#
# Operator decision (2026-10-19): when several ready slots serve the requested
# model, pick the one that is not ``utility`` and has no request in flight;
# fall back to the current order (health, then declaration) when none
# qualifies. ``utility`` is declared FIRST here so "current order" means
# utility — the ct151 shape, where 43 of 59 decisions went to utility.

_UTILITY_FIRST = (_container_upstream("utility", 8086), _container_upstream("brain", 8087))
_BOTH_READY = {"utility": SlotState.READY, "brain": SlotState.READY}
_BOTH_ADVERTISE = {"utility": [_MODEL], "brain": [_MODEL]}


class TestDispatchPrefersAnIdleNonUtilitySibling:
    async def test_brain_idle_lands_on_brain(self) -> None:
        """Acceptance 1: utility and brain both ready on the model, brain idle."""
        dispatcher = _dispatcher(
            list(_UTILITY_FIRST), states=_BOTH_READY, advertised=_BOTH_ADVERTISE
        )

        call = await dispatcher.dispatch(_request(), body={"model": _MODEL})

        assert call.upstream_name == "brain", call.resolution_path
        # The chosen upstream is in the resolution path dispatch.decision logs.
        assert call.resolution_path.endswith(":brain")

    async def test_brain_busy_and_utility_idle_keeps_the_current_order(self) -> None:
        """Acceptance 2: no slot qualifies, so the old order (utility) stands."""
        dispatcher = _dispatcher(
            list(_UTILITY_FIRST),
            states=_BOTH_READY,
            advertised=_BOTH_ADVERTISE,
            in_flight={"brain": 1},
        )

        call = await dispatcher.dispatch(_request(), body={"model": _MODEL})

        assert call.upstream_name == "utility", call.resolution_path

    async def test_idle_sibling_beats_a_busy_non_utility_one(self) -> None:
        dispatcher = _dispatcher(
            [*_UTILITY_FIRST[:1], _container_upstream("nano", 8088), _UTILITY_FIRST[1]],
            states={**_BOTH_READY, "nano": SlotState.READY},
            advertised={**_BOTH_ADVERTISE, "nano": [_MODEL]},
            in_flight={"nano": 2},
        )

        call = await dispatcher.dispatch(_request(), body={"model": _MODEL})

        assert call.upstream_name == "brain", call.resolution_path

    async def test_registry_binding_to_utility_yields_to_an_idle_sibling(self) -> None:
        dispatcher = _dispatcher(
            list(_UTILITY_FIRST),
            states=_BOTH_READY,
            advertised=_BOTH_ADVERTISE,
            routes={_MODEL: "utility"},
        )

        call = await dispatcher.dispatch(_request(), body={"model": _MODEL})

        assert call.upstream_name == "brain", call.resolution_path

    async def test_health_still_outranks_idleness(self) -> None:
        """An idle brain that is still WARMING never beats a READY utility."""
        dispatcher = _dispatcher(
            list(_UTILITY_FIRST),
            states={"utility": SlotState.READY, "brain": SlotState.WARMING},
            advertised=_BOTH_ADVERTISE,
        )

        call = await dispatcher.dispatch(_request(), body={"model": _MODEL})

        assert call.upstream_name == "utility", call.resolution_path

    async def test_utility_lane_pin_still_wins(self) -> None:
        dispatcher = _dispatcher(
            list(_UTILITY_FIRST), states=_BOTH_READY, advertised=_BOTH_ADVERTISE
        )

        call = await dispatcher.dispatch(_request(lane_slot="utility"), body={"model": _MODEL})

        assert call.upstream_name == "utility", call.resolution_path

    async def test_utility_serves_when_it_is_the_only_ready_slot(self) -> None:
        dispatcher = _dispatcher(
            list(_UTILITY_FIRST),
            states={"utility": SlotState.READY, "brain": SlotState.OFFLINE},
            advertised=_BOTH_ADVERTISE,
        )

        call = await dispatcher.dispatch(_request(), body={"model": _MODEL})

        assert call.upstream_name == "utility", call.resolution_path


class TestColdLoadPrefersTheNonUtilitySibling:
    """``_ensure_backend_for_model`` shares the ranking (v1.py), so the slot it
    cold-loads for a model both slots bind is the non-utility one."""

    async def test_both_offline_loads_brain_not_utility(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_alias_map(monkeypatch, ("utility", "brain"))
        sm = _SlotManager({"utility": SlotState.OFFLINE, "brain": SlotState.OFFLINE})

        await _ensure_backend_for_model(_route_request(sm), {"model": _MODEL})

        assert sm.loaded == ["brain"], sm.loaded

    async def test_ready_utility_is_not_bypassed_for_an_offline_brain(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Health first: no cold load of brain while utility already serves."""
        _patch_alias_map(monkeypatch, ("utility", "brain"))
        sm = _SlotManager({"utility": SlotState.READY, "brain": SlotState.OFFLINE})

        await _ensure_backend_for_model(_route_request(sm), {"model": _MODEL})

        assert sm.loaded == ["utility"], sm.loaded


# ── the in-flight signal is exact and never leaks ────────────────────────────
#
# The preference reads ``SlotManager.in_flight_count`` — the real one here, so
# the counter under test is the production counter that ``Dispatcher.forward``
# drives (dispatch ticket + ``serving()``). Only state and readiness probes are
# stubbed.


class _LiveCountSlotManager(SlotManager):
    def __init__(self, states: dict[str, SlotState]) -> None:
        super().__init__()
        self._fixed_states = states

    def state(self, name: str) -> SlotState:  # type: ignore[override]
        return self._fixed_states.get(name, SlotState.OFFLINE)

    def is_ready_for_dispatch(self, name: str) -> bool:  # type: ignore[override]
        return self.state(name) in DISPATCHABLE_STATES

    async def container_readiness_check(self, slot_name: str) -> tuple[bool, str]:  # type: ignore[override]
        return True, "ready"


class _HangingStream(httpx.AsyncByteStream):
    """One SSE chunk, then a generation that never finishes."""

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b'data: {"choices":[]}\n\n'
        await asyncio.Event().wait()


class _BrokenStream(httpx.AsyncByteStream):
    """One SSE chunk, then the upstream connection drops."""

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b'data: {"choices":[]}\n\n'
        raise httpx.ReadError("upstream reset")


def _live_dispatcher(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[Dispatcher, _LiveCountSlotManager]:
    sm = _LiveCountSlotManager(dict(_BOTH_READY))

    async def _online(_u: Upstream) -> bool:
        return True

    dispatcher = Dispatcher(
        upstream_registry=_FakeUpstreams(list(_UTILITY_FIRST)),
        model_registry=_FakeModels({}),  # type: ignore[arg-type]
        cached_models=lambda name: list(_BOTH_ADVERTISE.get(name, [])),
        is_online=_online,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        slot_manager=sm,
    )
    return dispatcher, sm


async def _pick(dispatcher: Dispatcher) -> str:
    call = await dispatcher.dispatch(_request(), body={"model": _MODEL})
    return call.upstream_name


class TestInFlightSignalReturnsToZero:
    async def test_streaming_cancel_releases_brain(self) -> None:
        dispatcher, sm = _live_dispatcher(
            lambda _req: httpx.Response(
                200, stream=_HangingStream(), headers={"content-type": "text/event-stream"}
            )
        )
        try:
            call = await dispatcher.dispatch(_request(), body={"model": _MODEL, "stream": True})
            assert call.upstream_name == "brain"
            resp = await dispatcher.forward(call)
            assert sm.in_flight_count("brain") == 1
            # Brain is mid-generation: the next turn takes the current order.
            assert await _pick(dispatcher) == "utility"

            first_chunk = asyncio.Event()

            async def _consume() -> None:
                async for _chunk in resp.body_iterator:
                    first_chunk.set()

            # Starlette cancels the body task when the client disconnects.
            task = asyncio.create_task(_consume())
            await asyncio.wait_for(first_chunk.wait(), timeout=5)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

            assert sm.in_flight_count("brain") == 0
            assert await _pick(dispatcher) == "brain"
        finally:
            await dispatcher.aclose()

    @pytest.mark.parametrize("stream", [False, True])
    async def test_upstream_connect_error_releases_brain(self, stream: bool) -> None:
        def _refuse(req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=req)

        dispatcher, sm = _live_dispatcher(_refuse)
        try:
            call = await dispatcher.dispatch(_request(), body={"model": _MODEL, "stream": stream})
            assert call.upstream_name == "brain"
            with pytest.raises(UpstreamUnavailable):
                await dispatcher.forward(call)

            assert sm.in_flight_count("brain") == 0
            assert await _pick(dispatcher) == "brain"
        finally:
            await dispatcher.aclose()

    async def test_upstream_error_mid_stream_releases_brain(self) -> None:
        dispatcher, sm = _live_dispatcher(
            lambda _req: httpx.Response(
                200, stream=_BrokenStream(), headers={"content-type": "text/event-stream"}
            )
        )
        try:
            call = await dispatcher.dispatch(_request(), body={"model": _MODEL, "stream": True})
            resp = await dispatcher.forward(call)
            with contextlib.suppress(httpx.HTTPError):
                async for _chunk in resp.body_iterator:
                    pass

            assert sm.in_flight_count("brain") == 0
            assert await _pick(dispatcher) == "brain"
        finally:
            await dispatcher.aclose()

    async def test_upstream_500_releases_brain(self) -> None:
        dispatcher, sm = _live_dispatcher(lambda _req: httpx.Response(500, json={"error": "boom"}))
        try:
            call = await dispatcher.dispatch(_request(), body={"model": _MODEL})
            resp = await dispatcher.forward(call)
            assert resp.status_code == 500

            assert sm.in_flight_count("brain") == 0
            assert await _pick(dispatcher) == "brain"
        finally:
            await dispatcher.aclose()
