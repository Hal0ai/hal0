"""FLM streamed-usage injection and strip (#2994)."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.responses import StreamingResponse

from hal0.api.routes.v1 import _request_flm_stream_usage
from hal0.dispatcher.router import UpstreamCall
from hal0.dispatcher.stream_usage import ensure_include_usage, strip_usage_only_chunk
from hal0.metrics.seam import RequestSeam

USAGE_EVENT = (
    b'data: {"choices":[],"usage":{"prompt_tokens":12,"completion_tokens":2,"decoding_speed_tps":31.5,'
    b'"total_tokens":14}}\n\n'
)
STREAM = [
    b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n',
    b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n',
    b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
    USAGE_EVENT,
    b"data: [DONE]\n\n",
]


def _body(**extra: Any) -> bytes:
    return json.dumps({"model": "m", "stream": True, "messages": [], **extra}).encode()


def test_injected_when_absent() -> None:
    out = ensure_include_usage(_body())
    assert out is not None
    new, strip = out
    assert json.loads(new)["stream_options"] == {"include_usage": True}
    assert strip is True


def test_merged_into_other_stream_options() -> None:
    out = ensure_include_usage(_body(stream_options={"continuous_usage_stats": True}))
    assert out is not None
    assert json.loads(out[0])["stream_options"] == {
        "continuous_usage_stats": True,
        "include_usage": True,
    }


@pytest.mark.parametrize("flag", [True, False])
def test_client_include_usage_respected(flag: bool) -> None:
    assert ensure_include_usage(_body(stream_options={"include_usage": flag})) is None


def test_non_streamed_and_garbage_untouched() -> None:
    assert ensure_include_usage(json.dumps({"model": "m", "stream": False}).encode()) is None
    assert ensure_include_usage(json.dumps({"model": "m"}).encode()) is None
    assert ensure_include_usage(b"not json") is None
    assert ensure_include_usage(_body(stream_options="weird")) is None


async def _agen(chunks: list[bytes]):
    for c in chunks:
        yield c


async def _collect(it: Any) -> bytes:
    return b"".join([c async for c in it])


@pytest.mark.asyncio
async def test_strip_removes_usage_chunk_only() -> None:
    out = await _collect(strip_usage_only_chunk(_agen(STREAM)))
    assert out == b"".join(c for c in STREAM if c != USAGE_EVENT)


@pytest.mark.asyncio
async def test_strip_handles_events_split_across_transport_chunks() -> None:
    raw = b"".join(STREAM)
    pieces = [raw[i : i + 7] for i in range(0, len(raw), 7)]
    out = await _collect(strip_usage_only_chunk(_agen(pieces)))
    assert out == b"".join(c for c in STREAM if c != USAGE_EVENT)


@pytest.mark.asyncio
async def test_strip_handles_crlf_events_incrementally() -> None:
    crlf = [c.replace(b"\n\n", b"\r\n\r\n") for c in STREAM]
    gen = strip_usage_only_chunk(_agen(crlf))
    first = await gen.__anext__()  # delivered per event, not buffered to EOF
    assert first == crlf[0]
    rest = first + b"".join([c async for c in gen])
    assert rest == b"".join(c for c in crlf if c != crlf[3])


@pytest.mark.asyncio
async def test_strip_keeps_choice_chunk_carrying_usage() -> None:
    chunk = b'data: {"choices":[{"delta":{"content":"x"}}],"usage":{"prompt_tokens":1}}\n\n'
    assert await _collect(strip_usage_only_chunk(_agen([chunk]))) == chunk


class _Writer:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def enqueue(self, _table: str, row: dict[str, Any]) -> None:
        self.rows.append(row)


@pytest.mark.asyncio
async def test_usage_stripped_from_client_but_captured_by_metrics() -> None:
    writer = _Writer()
    seam = RequestSeam(writer, enabled=True)  # type: ignore[arg-type]
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={})
    call = UpstreamCall(upstream_name="flm", target_url="http://x", resolved_model="m")
    now = time.monotonic()
    response = StreamingResponse(_agen(STREAM), media_type="text/event-stream")
    response = seam.wrap_streaming(
        response,
        call=call,
        request=request,
        t_entry=now,
        dispatch_started=now,  # type: ignore[arg-type]
    )
    response.body_iterator = strip_usage_only_chunk(response.body_iterator)
    sent = await _collect(response.body_iterator)
    assert b'"usage"' not in sent
    assert sent.endswith(b"data: [DONE]\n\n")
    row = writer.rows[0]
    assert row["prompt_tokens"] == 12
    assert row["tps_source"] == "exact"
    assert row["decode_tps"] == 31.5
    assert row["stop_reason"] == "stop"


class _SlotManager:
    def __init__(self, cfg: dict[str, Any]) -> None:
        self._cfg = cfg

    async def get_config(self, _name: str) -> dict[str, Any]:
        return self._cfg


def _req(cfg: dict[str, Any] | None, path: str = "/v1/chat/completions") -> Any:
    sm = _SlotManager(cfg) if cfg is not None else None
    return SimpleNamespace(
        url=SimpleNamespace(path=path), app=SimpleNamespace(state=SimpleNamespace(slot_manager=sm))
    )


def _call(*, streaming: bool = True, slot: str = "flm") -> UpstreamCall:
    return UpstreamCall(
        upstream_name=slot or "openrouter",
        target_url="http://x",
        body=_body() if streaming else json.dumps({"model": "m"}).encode(),
        streaming=streaming,
        slot_name=slot,
    )


@pytest.mark.asyncio
async def test_flm_slot_call_is_rewritten() -> None:
    call = _call()
    assert await _request_flm_stream_usage(_req({"device": "npu"}), call) is True
    assert json.loads(call.body)["stream_options"] == {"include_usage": True}


@pytest.mark.asyncio
async def test_non_flm_upstreams_unchanged() -> None:
    gpu = _call()
    original = gpu.body
    assert await _request_flm_stream_usage(_req({"device": "gpu"}), gpu) is False
    assert gpu.body == original
    remote = _call(slot="")
    assert await _request_flm_stream_usage(_req({"device": "npu"}), remote) is False
    assert remote.body == original


@pytest.mark.asyncio
async def test_non_streamed_and_non_chat_unchanged() -> None:
    call = _call(streaming=False)
    original = call.body
    assert await _request_flm_stream_usage(_req({"device": "npu"}), call) is False
    assert call.body == original
    other = _call()
    original = other.body
    assert (
        await _request_flm_stream_usage(_req({"device": "npu"}, "/v1/completions"), other) is False
    )
    assert other.body == original
