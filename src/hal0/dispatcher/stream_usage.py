"""Exact-usage capture for streamed FastFlowLM chat (#2994).

From FastFlowLM 1.0.10 a streamed ``/v1/chat/completions`` response carries
``usage`` only when the request sets ``stream_options.include_usage``. hal0's
metrics seam reads that usage off the stream's last payload, so without the
flag every FLM stream degrades to estimated token counts and speed.

:func:`ensure_include_usage` asks for the usage chunk on the way upstream;
:func:`strip_usage_only_chunk` removes it again on the way to a client that did
not ask for it. The strip wrapper must sit OUTSIDE the metrics seam so the seam
still sees the usage chunk.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any


def ensure_include_usage(body: bytes) -> tuple[bytes, bool] | None:
    """Return ``(new_body, strip)`` for a streamed chat body, or ``None`` to leave it.

    ``strip`` is True when hal0 added the flag (the client never asked for the
    usage chunk, so it must not see one). A client that already set
    ``include_usage`` — either way — keeps its choice untouched.
    """
    try:
        payload = json.loads(body)
    except ValueError:
        return None
    if not isinstance(payload, dict) or payload.get("stream") is not True:
        return None
    options = payload.get("stream_options")
    if options is None:
        options = {}
    elif not isinstance(options, dict):
        return None
    if "include_usage" in options:
        return None
    payload["stream_options"] = {**options, "include_usage": True}
    return json.dumps(payload).encode("utf-8"), True


def _is_usage_only_event(event: bytes) -> bool:
    """True for an SSE event whose payload is ``{"choices": [], "usage": {...}}``."""
    text = event.decode("utf-8", errors="replace").strip()
    if not text.startswith("data:"):
        return False
    data = text[len("data:") :].strip()
    if not data.startswith("{"):
        return False
    try:
        parsed = json.loads(data)
    except ValueError:
        return False
    return (
        isinstance(parsed, dict)
        and parsed.get("choices") == []
        and isinstance(parsed.get("usage"), dict)
    )


async def strip_usage_only_chunk(source: AsyncIterator[Any]) -> AsyncIterator[Any]:
    """Relay ``source`` minus any usage-only SSE event.

    Upstream chunks are transport reads, not SSE events, so bytes are buffered
    to event boundaries (blank line). Everything else is relayed unchanged.
    """
    buffer = b""
    async for chunk in source:
        if not isinstance(chunk, (bytes, bytearray)):
            yield chunk
            continue
        buffer += chunk
        out = b""
        while True:
            end = buffer.find(b"\n\n")
            if end < 0:
                break
            event, buffer = buffer[: end + 2], buffer[end + 2 :]
            if not _is_usage_only_event(event):
                out += event
        if out:
            yield out
    if buffer:
        yield buffer
