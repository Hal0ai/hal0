"""``journalctl`` exiting under the per-slot log tail must surface a reason.

Observed on a box whose ``hal0-api.service`` predates the
``SupplementaryGroups=systemd-journal`` grant (#2435): ``journalctl`` prints
``No journal files were opened due to insufficient permissions.`` to stderr,
exits 0 with empty stdout, and :func:`hal0.slots.logs.tail_journal` ended
cleanly as if the follow had simply finished. The SSE route then closed the
response after zero bytes, the browser's ``EventSource`` treated stream-end
as an error, and the slot log drawer (and the Logs page "slot" channel) sat
in a permanent "reconnecting…" loop with no lines and no explanation —
while the one-shot ``/logs`` twin claimed "no journal entries".

``journalctl -f`` never returns on its own on a healthy host, so the follow
generator ending is itself the signal. These tests pin that it is reported
as a :class:`JournalTailExited` carrying an actionable reason, that the
keepalive wrapper turns it into a terminal :class:`JournalDegraded` item,
and that the one-shot tail hints at the permission problem instead of the
misleading "slot may not have started yet".
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from hal0.slots import logs as _logs

_PERM_DENIED = b"No journal files were opened due to insufficient permissions.\n"


class _FakeStream:
    """Minimal ``asyncio.StreamReader`` stand-in: async-iterable + ``read``."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def __aiter__(self) -> AsyncIterator[bytes]:
        async def _gen() -> AsyncIterator[bytes]:
            for line in self._data.splitlines(keepends=True):
                yield line

        return _gen()

    async def read(self, _n: int = -1) -> bytes:
        return self._data


class _FakeProc:
    def __init__(self, out: bytes, err: bytes, rc: int = 0) -> None:
        self.stdout = _FakeStream(out)
        self.stderr = _FakeStream(err)
        self._err = err
        self.returncode = rc
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        return self.stdout._data, self._err

    async def wait(self) -> int:
        return self.returncode

    def kill(self) -> None:
        self.killed = True


def _spawn_returning(proc: _FakeProc):
    async def _spawn(*_a: object, **_kw: object) -> _FakeProc:
        return _spawn.proc  # type: ignore[attr-defined]

    _spawn.proc = proc  # type: ignore[attr-defined]
    return _spawn


@pytest.mark.asyncio
async def test_tail_journal_raises_when_journalctl_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty stdout + permission text on stderr + exit → JournalTailExited with
    a reason that names the missing group, not a silent clean finish."""
    proc = _FakeProc(b"", _PERM_DENIED, rc=0)
    monkeypatch.setattr(_logs.asyncio, "create_subprocess_exec", _spawn_returning(proc))

    with pytest.raises(_logs.JournalTailExited) as excinfo:
        async for _line in _logs.tail_journal("hal0-slot@1.service", 3, True):
            pytest.fail("no lines were expected")

    reason = excinfo.value.reason
    assert "systemd-journal" in reason, reason
    assert "insufficient permissions" in reason, reason
    assert proc.killed, "the subprocess is always reaped on the way out"


@pytest.mark.asyncio
async def test_tail_journal_reports_lines_before_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lines that did arrive are yielded first; the exit is reported after."""
    proc = _FakeProc(b"loading model\nserver listening\n", b"", rc=1)
    monkeypatch.setattr(_logs.asyncio, "create_subprocess_exec", _spawn_returning(proc))

    got: list[str] = []
    with pytest.raises(_logs.JournalTailExited) as excinfo:
        async for line in _logs.tail_journal("hal0-slot@2.service", 0, True):
            got.append(line)

    assert got == ["loading model", "server listening"]
    assert excinfo.value.lines == 2
    assert "status 1" in excinfo.value.reason, excinfo.value.reason
    assert "hal0-slot@2.service" in excinfo.value.reason


@pytest.mark.asyncio
async def test_tail_journal_uses_last_stderr_line_as_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc = _FakeProc(b"", b"Failed to add match: Invalid argument\n", rc=1)
    monkeypatch.setattr(_logs.asyncio, "create_subprocess_exec", _spawn_returning(proc))

    with pytest.raises(_logs.JournalTailExited) as excinfo:
        async for _line in _logs.tail_journal("hal0-slot@3.service", 0, True):
            pass

    assert "Failed to add match: Invalid argument" in excinfo.value.reason


@pytest.mark.asyncio
async def test_keepalive_turns_exit_into_terminal_degraded_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The keepalive wrapper yields ONE JournalDegraded and then stops — the
    route turns it into the ``degraded`` SSE frame the clients listen for."""

    async def _dies(unit: str, backfill_n: int = 0, quiet: bool = True) -> AsyncIterator[str]:
        yield "one real line"
        raise _logs.JournalTailExited("journalctl: permission problem", lines=1)

    monkeypatch.setattr(_logs, "tail_journal", _dies)

    agen = _logs.tail_journal_keepalive("hal0-slot@1.service", 0, True, keepalive_s=5.0)
    got = await asyncio.wait_for(_collect(agen), timeout=5.0)

    assert got[0] == "one real line"
    assert isinstance(got[1], _logs.JournalDegraded)
    assert got[1].reason == "journalctl: permission problem"
    assert len(got) == 2, "degraded is terminal — nothing (not even a keepalive) follows"


async def _collect(agen: AsyncIterator[object]) -> list[object]:
    return [item async for item in agen]


@pytest.mark.asyncio
async def test_read_tail_hints_at_permissions_not_missing_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one-shot twin: empty stdout WITH a stderr reason must not claim
    "no journal entries — the slot may not have started yet"."""
    monkeypatch.setattr(_logs.shutil, "which", lambda _n: "/usr/bin/journalctl")
    proc = _FakeProc(b"", _PERM_DENIED, rc=0)
    monkeypatch.setattr(_logs.asyncio, "create_subprocess_exec", _spawn_returning(proc))

    text, hint = await _logs.read_tail("hal0-slot@1.service", 100, True)

    assert text == ""
    assert hint is not None
    assert "systemd-journal" in hint, hint
    assert "may not have started" not in hint, hint


@pytest.mark.asyncio
async def test_read_tail_keeps_missing_entries_hint_when_stderr_is_quiet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard for #1472: a clean, genuinely-empty tail keeps its hint."""
    monkeypatch.setattr(_logs.shutil, "which", lambda _n: "/usr/bin/journalctl")
    proc = _FakeProc(b"", b"", rc=0)
    monkeypatch.setattr(_logs.asyncio, "create_subprocess_exec", _spawn_returning(proc))

    text, hint = await _logs.read_tail("hal0-slot@9.service", 100, True)

    assert text == ""
    assert hint is not None and "hal0-slot@9.service" in hint
