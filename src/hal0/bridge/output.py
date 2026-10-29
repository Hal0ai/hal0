"""A run's output, read in 16 KiB windows (ADR-0043 decision 9).

The ledger keeps a run's whole ``output`` in ``runs.output``, while its events
keep only the newest :data:`~hal0.bridge.store.EVENT_CAP` rows, so the events
cannot give back a long answer. A reader pages the stored text instead:
``agent_run_status(run_id, offset=N)`` on ``hal0-agents`` and
``GET /api/bridge/runs/{run_id}?offset=N`` return the window of the output that
starts ``N`` bytes in, with

* ``output_offset``: the byte offset the window starts at,
* ``output_total``: the length of the whole text in bytes,
* ``next_offset``: the offset of the next window, or None after the last one.

Offsets count UTF-8 bytes, and a window never splits a character: an offset
that lands inside one moves forward to the next character, and a window ends
on a character boundary. No Hermes import here: the routes and the MCP server
use this without loading the bridge service (golden path #15).
"""

from __future__ import annotations

from typing import Any

__all__ = ["OUTPUT_WINDOW_BYTES", "output_window"]

#: The size of one window of a run's output, in bytes.
OUTPUT_WINDOW_BYTES = 16 * 1024


def _continuation(byte: int) -> bool:
    """True for a UTF-8 continuation byte (``10xxxxxx``): not the start of a character."""
    return byte & 0xC0 == 0x80


def output_window(
    text: str, offset: int = 0, *, size: int = OUTPUT_WINDOW_BYTES
) -> tuple[str, dict[str, Any]]:
    """The window of ``text`` that starts ``offset`` bytes in, and its paging fields.

    Returns ``(window, {"output_offset", "output_total", "next_offset"})``. A
    negative offset reads from 0; an offset past the end gives an empty window
    at the end, with ``next_offset`` None.
    """
    raw = text.encode("utf-8")
    total = len(raw)
    start = min(max(int(offset), 0), total)
    while start < total and _continuation(raw[start]):
        start += 1
    end = min(start + max(size, 4), total)  # 4 bytes: room for any one character
    while end < total and _continuation(raw[end]):
        end -= 1
    page: dict[str, Any] = {
        "output_offset": start,
        "output_total": total,
        "next_offset": end if end < total else None,
    }
    return raw[start:end].decode("utf-8"), page
