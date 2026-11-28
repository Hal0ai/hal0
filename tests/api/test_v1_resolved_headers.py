"""``X-hal0-Resolved-Model`` and ``X-hal0-Slot`` name what answered (#4017).

The rule the tests hold: for a slot, the model header is what the slot has
loaded, read from the slot's own state. It is never the name the caller sent,
which is what the dispatcher's fallback path records as ``resolved_model``
when it sends a request for an unknown model to a default slot.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.responses import Response, StreamingResponse

from hal0.api.routes import v1


class _Headers(dict):
    def get(self, key: str, default: Any = None) -> Any:  # case-insensitive enough here
        return super().get(key.lower(), default)


class _SlotManager:
    """What ``/api/slots`` would say each slot has loaded."""

    def __init__(self, loaded: dict[str, str]) -> None:
        self._loaded = loaded

    def loaded_model_id(self, name: str) -> str:
        return self._loaded.get(name, "")


def _request(loaded: dict[str, str] | None = None, path: str = "/v1/embeddings") -> Any:
    state = SimpleNamespace(
        slot_manager=_SlotManager(loaded) if loaded is not None else None,
        last_used_model={},
        tps_events=None,
        ttft_events=None,
    )

    async def body() -> bytes:
        return b""

    return SimpleNamespace(
        headers=_Headers(),
        url=SimpleNamespace(path=path),
        app=SimpleNamespace(state=state),
        state=SimpleNamespace(),
        scope={"type": "http"},
        body=body,
    )


def _call(**kw: Any) -> Any:
    base = {
        "upstream_name": "embed",
        "requested_model": "",
        "resolved_model": "",
        "slot_name": "",
        "container_slot_name": "",
        "streaming": False,
        "resolution_path": "",
    }
    return SimpleNamespace(**{**base, **kw})


class _Dispatcher:
    def __init__(self, call: Any, response: Any) -> None:
        self._call, self._response = call, response

    async def dispatch(self, request: Any, body: Any = None) -> Any:
        return self._call

    async def forward(self, call: Any) -> Any:
        return self._response


# ── which model: the slot's own, never the requested name ────────────────────


def test_fallback_to_a_default_slot_reports_the_slots_model_not_the_requested_name() -> None:
    """The case #4017 is about. A request for a model no slot is bound to is
    sent to the default embed slot; the routing decision carries the caller's
    name as ``resolved_model``. The header must carry what the slot serves."""
    call = _call(
        requested_model="text-embedding-3-small",
        resolved_model="text-embedding-3-small",  # as router.py's legacy fallback records it
        slot_name="embed",
        resolution_path="legacy_slot:embed",
    )
    model, slot = v1._resolved_identity(_request({"embed": "qwen3-embedding-0-6b-q8-0"}), call)
    assert slot == "embed"
    assert model == "qwen3-embedding-0-6b-q8-0"
    assert model != call.requested_model


def test_a_slot_whose_model_is_unknown_gets_no_model_header() -> None:
    """Absent is honest; the requested name is not a fallback."""
    call = _call(requested_model="misspelt", resolved_model="misspelt", slot_name="agent")
    for request in (_request({}), _request(None)):
        model, slot = v1._resolved_identity(request, call)
        assert (model, slot) == ("", "agent")

    response = Response(content=b"")
    v1._stamp_resolved(response, model="", slot="agent")
    assert "x-hal0-resolved-model" not in response.headers
    assert response.headers["x-hal0-slot"] == "agent"


def test_a_slot_manager_that_raises_costs_only_the_model_header() -> None:
    class _Broken:
        def loaded_model_id(self, name: str) -> str:
            raise RuntimeError("state unreadable")

    request = _request({})
    request.app.state.slot_manager = _Broken()
    assert v1._resolved_identity(request, _call(slot_name="agent")) == ("", "agent")


def test_a_remote_provider_names_the_model_sent_and_no_slot() -> None:
    call = _call(upstream_name="openrouter", resolved_model="vendor/model-x")
    assert v1._resolved_identity(_request({"agent": "local"}), call) == ("vendor/model-x", "")


def test_the_container_slot_name_is_used_when_the_slot_name_is_empty() -> None:
    call = _call(container_slot_name="chat", resolved_model="ignored")
    assert v1._resolved_identity(_request({"chat": "gemma"}), call) == ("gemma", "chat")


# ── the headers are hal0's: an upstream's copy is never passed through ───────


def test_an_upstreams_own_headers_are_dropped_for_a_remote_answer() -> None:
    """A remote provider (another hal0, say) that sends these names must not
    have them reach the client as if this hal0 had said them."""
    response = Response(
        content=b"",
        headers={"X-hal0-Slot": "their-slot", "X-hal0-Resolved-Model": "their-model"},
    )
    v1._stamp_resolved(response, model="vendor/model-x", slot="")
    assert response.headers["x-hal0-resolved-model"] == "vendor/model-x"
    assert "x-hal0-slot" not in response.headers


def test_an_upstreams_own_headers_are_dropped_when_hal0_knows_nothing() -> None:
    response = Response(
        content=b"",
        headers={"X-hal0-Slot": "their-slot", "X-hal0-Resolved-Model": "their-model"},
    )
    v1._stamp_resolved(response, model="", slot="")
    assert "x-hal0-resolved-model" not in response.headers
    assert "x-hal0-slot" not in response.headers


@pytest.mark.parametrize("bad", ["mod\u00e8le", "bad\nslot", " padded", "padded ", "tab\there"])
def test_values_a_header_cannot_carry_are_left_out(bad: str) -> None:
    response = Response(content=b"", headers={"X-hal0-Slot": "stale"})
    v1._stamp_resolved(response, model=bad, slot=bad)
    assert "x-hal0-resolved-model" not in response.headers
    assert "x-hal0-slot" not in response.headers


# ── end to end through the dispatch helper ───────────────────────────────────


@pytest.mark.asyncio
async def test_a_buffered_response_carries_the_slots_model() -> None:
    call = _call(requested_model="other", resolved_model="other", slot_name="embed")
    dispatcher = _Dispatcher(call, Response(content=b"{}", media_type="application/json"))
    response = await v1._dispatch_and_forward(
        _request({"embed": "qwen3-embedding-0-6b-q8-0"}), dispatcher, body={"model": "other"}
    )
    assert response.headers["x-hal0-resolved-model"] == "qwen3-embedding-0-6b-q8-0"
    assert response.headers["x-hal0-slot"] == "embed"


@pytest.mark.asyncio
async def test_a_streaming_response_carries_the_headers_before_its_first_byte() -> None:
    async def chunks():
        yield b"data: {}\n\n"
        yield b"data: [DONE]\n\n"

    call = _call(
        upstream_name="agent",
        requested_model="hal0/agent",
        resolved_model="hal0/agent",
        slot_name="agent",
        streaming=True,
    )
    upstream = StreamingResponse(
        chunks(), media_type="text/event-stream", headers={"X-hal0-Slot": "from-upstream"}
    )
    # A non-chat path keeps the chat normalisation out of the test; the
    # streaming branch of the dispatch helper is the same for every path.
    request = _request({"agent": "qwen3-27b"})
    response = await v1._dispatch_and_forward(
        request, _Dispatcher(call, upstream), body={"model": "hal0/agent", "stream": True}
    )
    assert isinstance(response, StreamingResponse)
    # Headers are fixed before the body iterator is consumed.
    assert response.headers["x-hal0-resolved-model"] == "qwen3-27b"
    assert response.headers["x-hal0-slot"] == "agent"


@pytest.mark.asyncio
async def test_a_remote_answer_end_to_end_has_no_slot_header() -> None:
    call = _call(upstream_name="openrouter", resolved_model="vendor/model-x")
    upstream = Response(
        content=b"{}", media_type="application/json", headers={"X-hal0-Slot": "theirs"}
    )
    response = await v1._dispatch_and_forward(
        _request({}), _Dispatcher(call, upstream), body={"model": "vendor/model-x"}
    )
    assert response.headers["x-hal0-resolved-model"] == "vendor/model-x"
    assert "x-hal0-slot" not in response.headers
