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

from hal0.bridge.store import TERMINAL_STATUSES
from hal0.redaction import MASK, redact_log_line, secret_spans, token_head_at_end

__all__ = [
    "OUTPUT_PREVIEW_CHARS",
    "OUTPUT_WINDOW_BYTES",
    "mask_run",
    "masked_output",
    "output_window",
    "run_list_item",
]

#: The size of one window of a run's output, in bytes.
OUTPUT_WINDOW_BYTES = 16 * 1024

#: Characters of a run's output a list item previews.
OUTPUT_PREVIEW_CHARS = 200


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


def _merged(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """``spans`` sorted, with overlapping or touching spans joined into one."""
    out: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def mask_run(texts: list[str], *, at_edge: bool = False) -> list[str]:
    """``texts`` masked as one string, each given back its own part of the result.

    A span that covers part of a text becomes one MASK in it; a text wholly inside a
    span becomes MASK. ``at_edge`` says the joined text ends at the newest stored
    event: a token the redactor would mask only once it is whole is masked from
    its known prefix to the end (:func:`~hal0.redaction.token_head_at_end`).
    """
    joined = "".join(texts)
    spans = secret_spans(joined)
    if at_edge and (head := token_head_at_end(joined)) is not None:
        spans = _merged([*spans, (head, len(joined))])
    if not spans:
        return list(texts)
    out: list[str] = []
    start = 0
    for text in texts:
        end = start + len(text)
        parts: list[str] = []
        pos = start
        for span_start, span_end in spans:
            if span_end <= start or span_start >= end:
                continue
            lo, hi = max(span_start, start), min(span_end, end)
            parts += (joined[pos:lo], MASK)
            pos = hi
        parts.append(joined[pos:end])
        out.append("".join(parts))
        start = end
    return out


def masked_output(view: dict[str, Any], output: str) -> str:
    """A run's ``output`` with its secrets masked, by the run's status.

    A finished run's output is complete: the log redactor masks it. A run that is
    not finished has its output extended on every flush of the watcher, so its end
    is the live edge, as at the end of an events page: a known token prefix running
    to the end is also masked through the end (:func:`mask_run` ``at_edge``). A
    view without a status is treated as live. Without such a prefix at the end the
    redactor's output is used as is.

    The one masking of a run's output: ``agent_run_status`` and the other views of
    ``hal0-agents`` (through ``hal0.mcp.agents``) and both run lists
    (:func:`run_list_item`) use it.
    """
    if view.get("status") in TERMINAL_STATUSES or token_head_at_end(output) is None:
        return redact_log_line(output)
    return mask_run([output], at_edge=True)[0]


def run_list_item(view: dict[str, Any]) -> dict[str, Any]:
    """A RunView as a run-list item: ``output_preview`` in place of ``output``.

    A list is for finding a run, not reading it, and a long output repeated in
    every row of a polled list is wasted bytes. The item keeps every other field
    (``error`` included) and drops ``output``; ``output_preview`` is the first
    :data:`OUTPUT_PREVIEW_CHARS` characters of the secret-masked output
    (:func:`masked_output`: a run that has not finished is masked to its live
    edge), or None for a run without one. ``GET /api/bridge/runs/{run_id}`` and
    ``agent_run_status`` read the whole output.

    The one projection both lists use: ``GET /api/bridge/runs`` and the
    ``agent_runs_list`` tool of ``hal0-agents``.
    """
    item = dict(view)
    output = item.pop("output", None)
    item["output_preview"] = (
        masked_output(item, output)[:OUTPUT_PREVIEW_CHARS] if isinstance(output, str) else None
    )
    return item
