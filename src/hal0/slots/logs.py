"""journalctl-backed per-slot log access.

Extracted from ``routes/slots.py`` (P3-routers §J) so the route layer keeps
only the thin ``StreamingResponse`` SSE wrapper (which must stay in the route)
and the one-shot envelope. Slot containers run under the
``hal0-slot@<name>.service`` template unit (podman ``--log-driver=none`` so
conmon→journal is the single sink), so the container's llama-server / ComfyUI
stdout — including the one-shot model-loading lines — lands in journald and is
reachable here.

Interface contract:

    is_log_noise(line) -> bool
        True for high-frequency heartbeat lines with no diagnostic value.
    read_tail(unit, lines, quiet=True) -> tuple[str, str | None]
        One-shot ``journalctl -n`` tail. Returns ``(logs_text, hint)`` where
        ``hint`` is a non-None reason string when the tail is empty/best-effort
        (journalctl missing, timed out). Never raises.
    tail_journal(unit, backfill_n=0, quiet=True) -> AsyncIterator[str]
        Follow (``journalctl -f``) generator yielding filtered log lines. The
        caller checks :func:`shutil.which` before opening it and formats each
        line as an SSE frame. Cleans up the subprocess on cancel/close.
    tail_journal_keepalive(unit, ..., keepalive_s) -> AsyncIterator[str | None]
        Same stream, but yields ``None`` every ``keepalive_s`` of silence so
        the SSE route can pulse a comment frame and keep proxies from reaping
        an idle connection (#1472).

The ``asyncio``/``shutil`` modules are referenced module-globally so tests can
monkeypatch the subprocess spawn.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
from collections.abc import AsyncIterator

# Heartbeat lines llama-server prints every few seconds while idle. Left
# unfiltered they flood the last-N tail window within minutes and push the
# (one-shot, at-startup) model-loading lines out of view — which is why the
# UI "stopped" showing detailed load logs. The ``quiet`` param drops them.
LOG_NOISE_MARKERS: tuple[str, ...] = (
    "update_slots: all slots are idle",
    "prompt processing progress",
    "kv cache rm",
)


def is_log_noise(line: str) -> bool:
    """True for high-frequency heartbeat lines with no diagnostic value."""
    return any(marker in line for marker in LOG_NOISE_MARKERS)


def _suppress_proc():
    """Suppress the narrow set of errors raised killing a dead subprocess."""
    return contextlib.suppress(ProcessLookupError, OSError)


async def read_tail(unit: str, lines: int, quiet: bool = True) -> tuple[str, str | None]:
    """Return the last ``lines`` of ``unit``'s journal output (one-shot).

    ``quiet`` (default on) drops idle heartbeat spam. Best-effort: on hosts
    without systemd or where the unit has never started, returns
    ``("", <hint>)`` rather than raising — the UI renders "No logs available".
    """
    if shutil.which("journalctl") is None:
        return "", "journalctl not available on this host"

    want = max(1, min(int(lines or 200), 5000))
    # When filtering noise, over-fetch so the post-filter result still holds
    # ~``want`` meaningful lines even if most of the raw tail is heartbeat.
    fetch = min(want * 8, 20000) if quiet else want
    cmd = [
        "journalctl",
        "-u",
        f"{unit}",
        "-n",
        str(fetch),
        "--no-pager",
        "-o",
        "short-iso",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=8.0)
    except TimeoutError:
        with _suppress_proc():
            proc.kill()
        return "", "journalctl timed out"
    text = stdout.decode("utf-8", errors="replace")
    if quiet:
        kept = [ln for ln in text.splitlines() if ln and not is_log_noise(ln)]
        text = "\n".join(kept[-want:])
    if not text.strip():
        # #1472: journalctl ran fine and produced nothing — which is exactly
        # the "unit has never started" case this function's contract promises a
        # hint for, and the case the route only attaches ``hint`` for. Returning
        # ``None`` here meant GET /api/slots/{name}/logs answered a bare
        # ``{"logs": ""}`` and the drawer rendered a blank pane with no reason.
        # Covers the quiet-filtered-to-empty shape too: the operator sees the
        # same blank pane either way, so it needs the same explanation.
        return "", (
            f"no journal entries for {unit} — the slot may not have started yet, "
            "or its journal has been rotated away"
        )
    return text, None


async def tail_journal(unit: str, backfill_n: int = 0, quiet: bool = True) -> AsyncIterator[str]:
    """Follow ``unit``'s journal output, yielding filtered lines.

    ``backfill_n`` replays recent history before the live tail (CRITICAL: the
    model-loading lines are emitted once at container start). ``quiet``
    (default on) drops idle heartbeat spam. The caller is responsible for
    checking ``shutil.which("journalctl")`` before iterating (so it can emit an
    SSE ``degraded`` frame); this generator assumes journalctl exists. The
    subprocess is killed when the consumer stops iterating (client disconnect).
    """
    backfill_n = max(0, min(int(backfill_n or 0), 5000))
    cmd = [
        "journalctl",
        "-u",
        f"{unit}",
        "-f",
        "-n",
        str(backfill_n),
        "--output=cat",
        "--no-pager",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip("\n")
            if not line:
                continue
            if quiet and is_log_noise(line):
                continue
            yield line
    except asyncio.CancelledError:
        raise
    finally:
        with _suppress_proc():
            proc.kill()
        with _suppress_proc():
            await proc.wait()


# ── SSE keepalive for the follow stream (#1472) ──────────────────────────────
#
# ``tail_journal`` yields only journalctl output, so a quiet slot emits ZERO
# bytes — verified on the live box, where a 4-second curl against a `warming`
# slot returned nothing at all. Any proxy idle timeout then reaps the
# connection while the client still reports ``disconnected=false``, and the
# client's own reconnect replays the default 400-line backfill into a ring
# whose content-dedup is deliberately off (raw journald legitimately repeats
# progress-bar lines), duplicating up to 400 lines per drop. Both sibling SSE
# routes already pulse at 15 s; this one was the exception.

#: Idle cadence, matched to ``api/routes/journal.py`` so one proxy timeout
#: value (>15 s) covers every hal0 SSE stream.
KEEPALIVE_S: float = 15.0

#: Sentinel pushed by the pump when the underlying journal generator ends.
_PUMP_DONE = object()


async def tail_journal_keepalive(
    unit: str,
    backfill_n: int = 0,
    quiet: bool = True,
    *,
    keepalive_s: float = KEEPALIVE_S,
) -> AsyncIterator[str | None]:
    """:func:`tail_journal`, plus a ``None`` yield every ``keepalive_s`` of silence.

    ``None`` means "nothing to report, emit a keepalive"; the SSE route turns
    it into a ``: keepalive`` comment frame. Real lines pass through verbatim,
    so this wrapper is transparent to a chatty stream.

    **Why a queue rather than the obvious ``asyncio.wait_for``.** Wrapping the
    generator step directly — ``await asyncio.wait_for(agen.__anext__(), t)``
    — cancels a *half-executed* async-generator frame on every idle tick. An
    async generator is not re-entrant across a cancelled step: the next
    ``__anext__`` can raise ``RuntimeError: anext(): asynchronous generator is
    already running``, or silently lose the value that was in flight. Since
    idle ticks are the common case on a quiet slot, that failure mode would be
    the normal path, not an edge case. Pumping into a queue moves the
    cancellation onto ``Queue.get()``, which is designed for it, and leaves the
    journal generator advancing on its own task.
    """
    queue: asyncio.Queue[str | object] = asyncio.Queue()

    async def _pump() -> None:
        try:
            async for line in tail_journal(unit, backfill_n, quiet):
                await queue.put(line)
        finally:
            # Always signal termination — including on cancellation — so a
            # consumer blocked on get() is never stranded waiting for a
            # producer that has already gone away.
            queue.put_nowait(_PUMP_DONE)

    task = asyncio.create_task(_pump())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=keepalive_s)
            except TimeoutError:
                yield None  # idle — caller emits the comment frame
                continue
            if item is _PUMP_DONE:
                return
            yield item  # type: ignore[misc]
    finally:
        # Client disconnect / consumer aclose(): stop the pump, which closes
        # the journalctl subprocess through tail_journal's own finally block.
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
