"""RequestSeam -- non-streaming, streaming, and error capture paths.

Uses a fake writer (records enqueue() calls) and minimal Request/UpstreamCall
stand-ins rather than a full FastAPI app, per the module's own design goal:
the seam only touches `request.headers`, `request.client`, and
`call.upstream_name`/`call.resolved_model`.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.responses import StreamingResponse

from hal0.metrics.seam import RequestSeam


class _FakeWriter:
    def __init__(self) -> None:
        self.rows: list[tuple[str, dict[str, Any]]] = []

    def enqueue(self, table: str, row: dict[str, Any]) -> None:
        self.rows.append((table, row))


@dataclass
class _FakeClient:
    host: str


@dataclass
class _FakeRequest:
    headers: dict[str, str] = field(default_factory=dict)
    client: _FakeClient | None = None


@dataclass
class _FakeCall:
    upstream_name: str = "primary"
    resolved_model: str = "qwen3-4b"


@pytest.fixture
def writer() -> _FakeWriter:
    return _FakeWriter()


@pytest.fixture
def seam(writer: _FakeWriter) -> RequestSeam:
    return RequestSeam(writer, enabled=True)


class TestDisabledSeam:
    def test_record_nonstreaming_is_noop_when_disabled(self, writer: _FakeWriter) -> None:
        seam = RequestSeam(writer, enabled=False)
        seam.record_nonstreaming(
            b'{"usage": {"completion_tokens": 5}}',
            call=_FakeCall(),
            request=_FakeRequest(),
            t_entry=time.monotonic(),
            dispatch_started=time.monotonic(),
        )
        assert writer.rows == []

    def test_record_error_is_noop_when_disabled(self, writer: _FakeWriter) -> None:
        seam = RequestSeam(writer, enabled=False)
        seam.record_error(RuntimeError("boom"), call=None, request=_FakeRequest(), t_entry=0.0)
        assert writer.rows == []


class TestRecordNonstreaming:
    def test_writes_one_request_metric_row(self, seam: RequestSeam, writer: _FakeWriter) -> None:
        body = json.dumps(
            {
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
                "timings": {"predicted_per_second": 50.0},
                "choices": [{"finish_reason": "stop"}],
            }
        ).encode()
        t_entry = time.monotonic()
        seam.record_nonstreaming(
            body,
            call=_FakeCall(upstream_name="primary", resolved_model="qwen3-4b"),
            request=_FakeRequest(headers={"x-request-id": "abc"}, client=_FakeClient("127.0.0.1")),
            t_entry=t_entry,
            dispatch_started=t_entry + 0.01,
        )
        assert len(writer.rows) == 1
        table, row = writer.rows[0]
        assert table == "request_metric"
        assert row["slot_id"] == "primary"
        assert row["model_id"] == "qwen3-4b"
        assert row["decode_tps"] == 50.0
        assert row["stop_reason"] == "stop"
        assert row["ok"] == 1
        assert row["request_id"] == "abc"
        assert row["client"] == "127.0.0.1"

    def test_call_none_writes_null_slot_and_model(
        self, seam: RequestSeam, writer: _FakeWriter
    ) -> None:
        seam.record_nonstreaming(
            b"",
            call=None,
            request=_FakeRequest(),
            t_entry=time.monotonic(),
            dispatch_started=time.monotonic(),
        )
        _, row = writer.rows[0]
        assert row["slot_id"] is None
        assert row["model_id"] is None


class TestRecordError:
    def test_writes_ok_zero_with_error_code(self, seam: RequestSeam, writer: _FakeWriter) -> None:
        class _TypedError(Exception):
            code = "dispatcher.no_route"

        seam.record_error(
            _TypedError("no route"),
            call=None,
            request=_FakeRequest(),
            t_entry=time.monotonic(),
        )
        _, row = writer.rows[0]
        assert row["ok"] == 0
        assert row["error_code"] == "dispatcher.no_route"

    def test_falls_back_to_exception_class_name(
        self, seam: RequestSeam, writer: _FakeWriter
    ) -> None:
        seam.record_error(
            RuntimeError("boom"), call=None, request=_FakeRequest(), t_entry=time.monotonic()
        )
        _, row = writer.rows[0]
        assert row["error_code"] == "RuntimeError"


class TestWrapStreaming:
    async def _drain(self, response: StreamingResponse) -> list[bytes]:
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)
        return chunks

    @pytest.mark.asyncio
    async def test_ttft_and_completion_row_written_after_stream_ends(
        self, seam: RequestSeam, writer: _FakeWriter
    ) -> None:
        async def _gen():
            yield b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
            yield b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
            yield b'data: {"choices":[{"delta":{"content":"!"},"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":2}}\n\n'
            yield b"data: [DONE]\n\n"

        response = StreamingResponse(_gen(), media_type="text/event-stream")
        t_entry = time.monotonic()
        wrapped = seam.wrap_streaming(
            response,
            call=_FakeCall(upstream_name="primary", resolved_model="qwen3-4b"),
            request=_FakeRequest(),
            t_entry=t_entry,
            dispatch_started=t_entry,
        )
        chunks = await self._drain(wrapped)
        assert len(chunks) == 4  # every original chunk still forwarded unchanged

        assert len(writer.rows) == 1
        _, row = writer.rows[0]
        assert row["slot_id"] == "primary"
        assert row["ok"] == 1
        assert row["ttft_ms"] is not None
        assert row["completion_tokens"] == 2
        assert row["stop_reason"] == "stop"

    @pytest.mark.asyncio
    async def test_disabled_seam_returns_response_unwrapped(self, writer: _FakeWriter) -> None:
        seam = RequestSeam(writer, enabled=False)

        async def _gen():
            yield b"data: {}\n\n"

        response = StreamingResponse(_gen(), media_type="text/event-stream")
        original_iter = response.body_iterator
        wrapped = seam.wrap_streaming(
            response,
            call=None,
            request=_FakeRequest(),
            t_entry=0.0,
            dispatch_started=0.0,
        )
        assert wrapped.body_iterator is original_iter
        assert writer.rows == []
