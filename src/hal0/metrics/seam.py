"""RequestSeam -- the ONE T1 measurement point (plan §7.6 / S12).

Wraps ``api/routes/v1.py::_dispatch_and_forward`` (the single hook site
identified by the OBS-1 census, hal0-specs/spec-obs-metrics.md Part 0.1)
without changing its control flow: existing behaviour (the ``tps_events``/
``ttft_events`` deques, ``slot_throughput``/``slot_kv_occupancy`` dicts)
keeps running untouched so ``GET /api/stats/throughput/history`` and
``GET /api/slots/metrics`` stay green. ``RequestSeam`` observes the same
call + response objects a second time and persists an exact
``request_metric`` row through :class:`hal0.metrics.writer.MetricsWriter`,
off the hot path (the writer's ``enqueue`` never awaits or blocks).

When ``enabled=False`` every method is a no-op that costs one attribute
lookup and an early return -- the "near-zero when off" contract (plan
§13.5).
"""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING, Any

import structlog
from fastapi.responses import StreamingResponse

from hal0.db.repository import now_iso
from hal0.metrics.capture import (
    build_request_metric_row,
    extract_timings_fields,
    extract_usage_fields,
    parse_json_object,
    truncate_client,
)

if TYPE_CHECKING:
    from fastapi import Request

    from hal0.dispatcher.router import UpstreamCall
    from hal0.metrics.writer import MetricsWriter

log = structlog.get_logger("hal0.metrics.seam")

_TABLE = "request_metric"


def _current_request_id(request: Request) -> str:
    """Best-effort read of the id ``request_id.install()`` bound this request to.

    The middleware only writes the resolved id onto the OUTGOING response
    header + structlog contextvars, not onto ``request`` itself, so we
    read it back from contextvars (falling back to the incoming header,
    then a fresh uuid for tests that bypass the middleware entirely).
    """
    bound = structlog.contextvars.get_contextvars().get("request_id")
    if isinstance(bound, str) and bound:
        return bound
    header = request.headers.get("x-request-id")
    if header:
        return header
    return uuid.uuid4().hex


def _client_host(request: Request) -> str | None:
    client = request.client
    return truncate_client(client.host if client is not None else None)


class RequestSeam:
    """Captures one ``request_metric`` row per request through the v1 seam."""

    def __init__(self, writer: MetricsWriter, *, enabled: bool = True) -> None:
        self._writer = writer
        self.enabled = enabled

    # ── non-streaming ────────────────────────────────────────────────────

    def record_nonstreaming(
        self,
        body_bytes: bytes,
        *,
        call: UpstreamCall | None,
        request: Request,
        t_entry: float,
        dispatch_started: float,
    ) -> None:
        if not self.enabled:
            return
        now = time.monotonic()
        payload = parse_json_object(body_bytes) if body_bytes else None
        row = build_request_metric_row(
            ts=now_iso(),
            request_id=_current_request_id(request),
            slot_id=getattr(call, "upstream_name", None) or None,
            model_id=getattr(call, "resolved_model", None) or None,
            queue_ms=(dispatch_started - t_entry) * 1000.0,
            total_ms=(now - t_entry) * 1000.0,
            ok=True,
            client=_client_host(request),
            payload=payload,
        )
        self._writer.enqueue(_TABLE, row)

    # ── streaming ─────────────────────────────────────────────────────────

    def wrap_streaming(
        self,
        response: StreamingResponse,
        *,
        call: UpstreamCall | None,
        request: Request,
        t_entry: float,
        dispatch_started: float,
    ) -> StreamingResponse:
        if not self.enabled:
            return response
        original = response.body_iterator
        slot_id = getattr(call, "upstream_name", None) or None
        model_id = getattr(call, "resolved_model", None) or None
        request_id = _current_request_id(request)
        client = _client_host(request)

        async def _seam_iter() -> Any:
            first_content_ts: float | None = None
            token_chunks = 0
            last_payload: dict[str, Any] | None = None
            ok = True
            error_code: str | None = None
            try:
                async for chunk in original:
                    raw = chunk if isinstance(chunk, (bytes, bytearray)) else None
                    text = (
                        raw.decode("utf-8", errors="replace")
                        if raw is not None
                        else (chunk if isinstance(chunk, str) else "")
                    )
                    if '"delta":' in text:
                        token_chunks += text.count('"delta":')
                        if first_content_ts is None:
                            first_content_ts = time.monotonic()
                    for line in text.splitlines():
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        data_str = line[len("data:") :].strip()
                        if not data_str or data_str == "[DONE]":
                            continue
                        parsed = parse_json_object(data_str)
                        if parsed is not None:
                            last_payload = parsed
                    yield chunk
            except Exception as exc:  # pragma: no cover -- upstream/client disconnect
                ok = False
                error_code = type(exc).__name__
                raise
            finally:
                now = time.monotonic()
                ttft_ms = (
                    (first_content_ts - dispatch_started) * 1000.0
                    if first_content_ts is not None
                    else None
                )
                elapsed_since_first = (
                    now - first_content_ts if first_content_ts is not None else None
                )
                row = build_request_metric_row(
                    ts=now_iso(),
                    request_id=request_id,
                    slot_id=slot_id,
                    model_id=model_id,
                    queue_ms=(dispatch_started - t_entry) * 1000.0,
                    total_ms=(now - t_entry) * 1000.0,
                    ok=ok,
                    error_code=error_code,
                    client=client,
                    ttft_ms=ttft_ms,
                    payload=last_payload,
                    fallback_completion_tokens=token_chunks or None,
                    fallback_elapsed_s=elapsed_since_first,
                )
                if last_payload is not None:
                    row.update(extract_usage_fields(last_payload))
                    timings = extract_timings_fields(last_payload)
                    if timings:
                        row.update(timings)
                    if row.get("ttft_ms") is None:
                        row["ttft_ms"] = ttft_ms
                self._writer.enqueue(_TABLE, row)

        response.body_iterator = _seam_iter()
        return response

    # ── error path ───────────────────────────────────────────────────────

    def record_error(
        self,
        exc: BaseException,
        *,
        call: UpstreamCall | None,
        request: Request,
        t_entry: float,
    ) -> None:
        if not self.enabled:
            return
        now = time.monotonic()
        row = build_request_metric_row(
            ts=now_iso(),
            request_id=_current_request_id(request),
            slot_id=getattr(call, "upstream_name", None) or None,
            model_id=getattr(call, "resolved_model", None) or None,
            queue_ms=None,
            total_ms=(now - t_entry) * 1000.0,
            ok=False,
            error_code=getattr(exc, "code", None) or type(exc).__name__,
            client=_client_host(request),
        )
        self._writer.enqueue(_TABLE, row)


__all__ = ["RequestSeam"]
