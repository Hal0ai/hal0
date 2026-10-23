"""``/v1/audio/speech`` routes by slot TYPE, not by the slot name ``tts`` (#2784).

A second tts slot (``voice2``) bound to ``kokoro-v1`` must serve a request for
``kokoro-v1``. The seeded ``tts`` slot stays the tie-breaker, and the
single-slot behaviour is unchanged.
"""

from __future__ import annotations

from typing import Any

import pytest
from starlette.requests import Request

from hal0.dispatcher.router import Dispatcher, UpstreamCall
from hal0.slots.state import SlotState
from hal0.upstreams.registry import Upstream, UpstreamRegistry


class FakeUpstreamRegistry(UpstreamRegistry):
    def __init__(self, upstreams: list[Upstream]) -> None:
        super().__init__()
        self._store = {u.name: u for u in upstreams}

    def list(self) -> list[Upstream]:  # type: ignore[override]
        return list(self._store.values())

    def get(self, name: str) -> Upstream | None:  # type: ignore[override]
        return self._store.get(name)

    def __getitem__(self, name: str) -> Upstream:
        return self._store[name]


class FakeModelRegistry:
    def route_for(self, model_id: str) -> str | None:
        return None


class FakeSlotManager:
    """The three seams the dispatcher reads: configs, state, in-flight."""

    def __init__(self, cfgs: list[dict[str, Any]], states: dict[str, SlotState]) -> None:
        self._cfgs = cfgs
        self._states = states

    async def iter_configs(self) -> list[dict[str, Any]]:
        return self._cfgs

    def state(self, name: str) -> SlotState:
        return self._states.get(name, SlotState.OFFLINE)

    def is_ready_for_dispatch(self, name: str) -> bool:
        return self.state(name) in {SlotState.READY, SlotState.IDLE, SlotState.SERVING}


def tts_cfg(name: str, model: str) -> dict[str, Any]:
    return {"name": name, "type": "tts", "model": {"default": model}}


def tts_upstream(name: str, port: int) -> Upstream:
    return Upstream(
        name=name,
        kind="slot",
        url=f"http://127.0.0.1:{port}/v1",
        auth_style="none",
        slot_name=name,
    )


def speech_request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/audio/speech",
            "raw_path": b"/v1/audio/speech",
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 1),
            "http_version": "1.1",
            "root_path": "",
        }
    )


def make_dispatcher(
    cfgs: list[dict[str, Any]],
    states: dict[str, SlotState],
    upstreams: list[Upstream],
) -> Dispatcher:
    return Dispatcher(
        upstream_registry=FakeUpstreamRegistry(upstreams),
        model_registry=FakeModelRegistry(),  # type: ignore[arg-type]
        slot_manager=FakeSlotManager(cfgs, states),  # type: ignore[arg-type]
        # Containers advertise "kokoro", never the registry id "kokoro-v1".
        cached_models=lambda name: ["kokoro"],
    )


async def route(dispatcher: Dispatcher, model: str | None) -> UpstreamCall:
    body: dict[str, Any] = {"input": "hi", "voice": "af_bella"}
    if model is not None:
        body["model"] = model
    return await dispatcher.dispatch(speech_request(), body=body)


@pytest.mark.asyncio
async def test_second_tts_slot_serves_its_bound_model() -> None:
    d = make_dispatcher(
        [tts_cfg("voice2", "kokoro-v1")],
        {"voice2": SlotState.READY},
        [tts_upstream("voice2", 8091)],
    )
    call = await route(d, "kokoro-v1")
    assert call.upstream_name == "voice2"
    assert call.target_url == "http://127.0.0.1:8091/v1/audio/speech"


@pytest.mark.asyncio
async def test_seeded_tts_wins_the_tie() -> None:
    d = make_dispatcher(
        [tts_cfg("voice2", "kokoro-v1"), tts_cfg("tts", "kokoro-v1")],
        {"voice2": SlotState.READY, "tts": SlotState.READY},
        [tts_upstream("voice2", 8091), tts_upstream("tts", 8084)],
    )
    call = await route(d, "kokoro-v1")
    assert call.upstream_name == "tts"


@pytest.mark.asyncio
async def test_non_serving_match_is_skipped() -> None:
    d = make_dispatcher(
        [tts_cfg("voice2", "kokoro-v1"), tts_cfg("tts", "kokoro-v1")],
        {"voice2": SlotState.READY, "tts": SlotState.ERROR},
        [tts_upstream("voice2", 8091), tts_upstream("tts", 8084)],
    )
    call = await route(d, "kokoro-v1")
    assert call.upstream_name == "voice2"


@pytest.mark.asyncio
async def test_model_match_beats_name() -> None:
    """``tts`` is bound to a different model: the request goes to the match."""
    d = make_dispatcher(
        [tts_cfg("tts", "qwen3-tts"), tts_cfg("voice2", "kokoro-v1")],
        {"tts": SlotState.READY, "voice2": SlotState.READY},
        [tts_upstream("tts", 8084), tts_upstream("voice2", 8091)],
    )
    call = await route(d, "kokoro-v1")
    assert call.upstream_name == "voice2"


@pytest.mark.asyncio
async def test_no_model_goes_to_tts() -> None:
    d = make_dispatcher(
        [tts_cfg("voice2", "kokoro-v1"), tts_cfg("tts", "kokoro-v1")],
        {"voice2": SlotState.READY, "tts": SlotState.READY},
        [tts_upstream("voice2", 8091), tts_upstream("tts", 8084)],
    )
    call = await route(d, None)
    assert call.upstream_name == "tts"


@pytest.mark.asyncio
async def test_single_seeded_slot_unchanged() -> None:
    d = make_dispatcher(
        [tts_cfg("tts", "kokoro-v1")],
        {"tts": SlotState.READY},
        [tts_upstream("tts", 8084)],
    )
    call = await route(d, "kokoro-v1")
    assert call.upstream_name == "tts"
    assert call.resolution_path == "legacy_slot:tts"


@pytest.mark.asyncio
async def test_unknown_model_still_pins_to_tts() -> None:
    d = make_dispatcher(
        [tts_cfg("tts", "kokoro-v1"), tts_cfg("voice2", "other")],
        {"tts": SlotState.READY, "voice2": SlotState.READY},
        [tts_upstream("tts", 8084), tts_upstream("voice2", 8091)],
    )
    call = await route(d, "never-heard-of-it")
    assert call.upstream_name == "tts"
