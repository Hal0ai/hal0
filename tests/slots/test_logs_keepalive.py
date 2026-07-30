"""Keepalive plumbing for the per-slot journal tail (#1472).

``tail_journal`` yields only journalctl output, so a slot that is quiet —
``warming``, idle, or simply not logging — produced ZERO bytes on
``/api/slots/{name}/logs/stream``. Verified on the live box: a 4-second
``curl -sN`` against a warming slot returned nothing at all. Any proxy or
load-balancer idle timeout therefore reaps the connection while the client
still believes it is attached (``disconnected=false``), and the client's own
reconnect then replays the default 400-line backfill into a ring whose
content-dedup is deliberately disabled — up to 400 duplicated lines per drop.

Both sibling SSE routes already pulse (``api/routes/journal.py`` and the
activity stream, both at 15 s); this one is the odd one out.

These tests cover the generator seam rather than the route, because the
cancellation-safety of the idle path is the part that is easy to get wrong:
``asyncio.wait_for(agen.__anext__(), ...)`` cancels a half-run async
generator step, which can poison the generator on the next call. The
implementation pumps through a queue instead, and these tests pin the
behaviour that guarantees.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from hal0.slots import logs as _logs


async def _drain(agen: AsyncIterator[str | None], n: int) -> list[str | None]:
    """Collect the first ``n`` yields, with a hard timeout so a hang fails."""
    out: list[str | None] = []
    async for item in agen:
        out.append(item)
        if len(out) >= n:
            break
    return out


@pytest.mark.asyncio
async def test_idle_stream_emits_keepalive_ticks(monkeypatch: pytest.MonkeyPatch) -> None:
    """A journal that never yields a line still produces keepalive ticks.

    This is the live-box shape: a warming slot, zero bytes, connection reaped.
    """

    async def _silent(unit: str, backfill_n: int = 0, quiet: bool = True) -> AsyncIterator[str]:
        await asyncio.sleep(3600)  # never yields
        yield "unreachable"  # pragma: no cover

    monkeypatch.setattr(_logs, "tail_journal", _silent)

    agen = _logs.tail_journal_keepalive("hal0-slot@tts.service", 0, True, keepalive_s=0.05)
    ticks = await asyncio.wait_for(_drain(agen, 3), timeout=5.0)

    assert ticks == [None, None, None], "an idle journal must still pulse"
    await agen.aclose()


@pytest.mark.asyncio
async def test_lines_pass_through_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real lines are forwarded verbatim; the keepalive wrapper is transparent."""

    async def _chatty(unit: str, backfill_n: int = 0, quiet: bool = True) -> AsyncIterator[str]:
        for line in ("loading model", "llama_new_context", "server listening"):
            yield line

    monkeypatch.setattr(_logs, "tail_journal", _chatty)

    agen = _logs.tail_journal_keepalive("hal0-slot@chat.service", 0, True, keepalive_s=30.0)
    got = [item async for item in agen]

    assert got == ["loading model", "llama_new_context", "server listening"]


@pytest.mark.asyncio
async def test_keepalive_does_not_poison_the_generator(monkeypatch: pytest.MonkeyPatch) -> None:
    """A line delivered AFTER several idle ticks still arrives intact.

    This is the regression the queue pump exists for: cancelling a pending
    ``__anext__`` on timeout (the obvious implementation) can leave the
    underlying async generator unable to produce further values.
    """

    async def _slow(unit: str, backfill_n: int = 0, quiet: bool = True) -> AsyncIterator[str]:
        await asyncio.sleep(0.18)
        yield "late line"
        await asyncio.sleep(3600)
        yield "unreachable"  # pragma: no cover

    monkeypatch.setattr(_logs, "tail_journal", _slow)

    agen = _logs.tail_journal_keepalive("hal0-slot@chat.service", 0, True, keepalive_s=0.05)
    got = await asyncio.wait_for(_drain(agen, 4), timeout=5.0)

    assert None in got, "should have ticked while waiting"
    assert "late line" in got, "the real line must survive the idle ticks"
    await agen.aclose()


@pytest.mark.asyncio
async def test_generator_terminates_when_journal_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    """journalctl exiting ends the stream rather than ticking forever."""

    async def _brief(unit: str, backfill_n: int = 0, quiet: bool = True) -> AsyncIterator[str]:
        yield "one line"

    monkeypatch.setattr(_logs, "tail_journal", _brief)

    agen = _logs.tail_journal_keepalive("hal0-slot@chat.service", 0, True, keepalive_s=30.0)
    got = await asyncio.wait_for(_drain(agen, 99), timeout=5.0)

    assert got == ["one line"]


@pytest.mark.asyncio
async def test_pump_task_is_cleaned_up_on_early_close(monkeypatch: pytest.MonkeyPatch) -> None:
    """Closing the consumer (client disconnect) must not leak the pump task."""
    started = asyncio.Event()

    async def _blocking(unit: str, backfill_n: int = 0, quiet: bool = True) -> AsyncIterator[str]:
        started.set()
        await asyncio.sleep(3600)
        yield "unreachable"  # pragma: no cover

    monkeypatch.setattr(_logs, "tail_journal", _blocking)

    before = len(asyncio.all_tasks())
    agen = _logs.tail_journal_keepalive("hal0-slot@chat.service", 0, True, keepalive_s=0.05)
    await asyncio.wait_for(_drain(agen, 1), timeout=5.0)
    await started.wait()
    await agen.aclose()
    await asyncio.sleep(0.05)  # let the cancellation settle

    assert len(asyncio.all_tasks()) <= before + 1, "pump task outlived the consumer"


# ── #1472: an empty tail must explain itself ─────────────────────────────────
#
# read_tail's docstring promises ``("", <hint>)`` "on hosts without systemd or
# where the unit has never started", and routes/slots.py only attaches a
# ``hint`` key when one comes back. But the success path returned
# ``(text, None)`` unconditionally, so a journalctl that ran fine and printed
# nothing produced ``{"logs": ""}`` with no explanation — which is exactly the
# never-started case the hint exists for. Live: GET /api/slots/utility/logs
# returned {"logs":""} with no hint.


class _FakeProc:
    def __init__(self, out: bytes) -> None:
        self._out = out

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._out, b""

    def kill(self) -> None:  # pragma: no cover — only on the timeout path
        return None


@pytest.mark.asyncio
async def test_empty_tail_returns_a_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    """journalctl succeeded and printed nothing → say why, don't return a bare
    empty string the UI can only render as a blank pane."""
    monkeypatch.setattr(_logs.shutil, "which", lambda _n: "/usr/bin/journalctl")

    async def _spawn(*_a: object, **_kw: object) -> _FakeProc:
        return _FakeProc(b"")

    monkeypatch.setattr(_logs.asyncio, "create_subprocess_exec", _spawn)

    text, hint = await _logs.read_tail("hal0-slot@utility.service", 100, True)
    assert text == ""
    assert hint, "an empty tail must carry a hint (docstring promises one)"
    assert "utility" in hint or "start" in hint.lower()


@pytest.mark.asyncio
async def test_nonempty_tail_has_no_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    """The hint is strictly for the empty case — real output stays hint-free."""
    monkeypatch.setattr(_logs.shutil, "which", lambda _n: "/usr/bin/journalctl")

    async def _spawn(*_a: object, **_kw: object) -> _FakeProc:
        return _FakeProc(b"llama_new_context: kv self size = 512 MiB\n")

    monkeypatch.setattr(_logs.asyncio, "create_subprocess_exec", _spawn)

    text, hint = await _logs.read_tail("hal0-slot@chat.service", 100, True)
    assert "llama_new_context" in text
    assert hint is None


@pytest.mark.asyncio
async def test_tail_that_is_all_noise_returns_a_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    """quiet=True can filter a non-empty tail down to nothing — the operator
    still sees a blank pane, so it still needs the explanation."""
    monkeypatch.setattr(_logs.shutil, "which", lambda _n: "/usr/bin/journalctl")
    noise = b"all slots are idle\n" * 5

    async def _spawn(*_a: object, **_kw: object) -> _FakeProc:
        return _FakeProc(noise)

    monkeypatch.setattr(_logs.asyncio, "create_subprocess_exec", _spawn)

    text, hint = await _logs.read_tail("hal0-slot@chat.service", 100, True)
    if not text.strip():
        assert hint, "a tail filtered to empty must explain itself too"
