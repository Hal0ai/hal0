"""Unit tests for :mod:`hal0.providers.podman_mutate` — the write-side twin
of :mod:`hal0.providers.podman_introspect` (runner-images v3, D1(a)/D2).

Covers:
  * :func:`rw_seam_available` — service-user gate AND binary-exists check,
    no sudo probe.
  * :func:`remove_image` — outcome mapping for every documented exit code
    (0/removed, 0/missing, 67/in-use, 1/64/65/66 -> unknown+reason), the bad
    -ref and not-service-user short-circuits (NO subprocess spawned), and the
    undefined-stdout-on-rc0 / exec-failure fallbacks.
  * :func:`pull_image_stream_rootful` — bad ref short-circuits with no
    subprocess spawned; pulling -> completed happy path; nonzero exit ->
    failed.
  * :class:`PullLineParser` — parity test pinning the exact event sequence
    the pre-extraction ``ContainerProvider.pull_image_stream`` heuristic
    produced, so the container.py refactor in this same change cannot drift.

``run`` / ``is_hal0_user`` / ``asyncio.create_subprocess_exec`` are injected
or monkeypatched so this never touches sudo, a real ``hal0`` user, or an
actual podman binary.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from hal0.providers import podman_mutate
from hal0.providers.podman_mutate import (
    RW_SEAM_BIN,
    PullLineParser,
    pull_image_stream_rootful,
    remove_image,
    rw_seam_available,
)

_REF = "ghcr.io/hal0ai/hal0-toolbox-cpu:latest"
_BAD_REF = "not a ref!"


def _completed(returncode: int = 0, stdout: str = "") -> MagicMock:
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = ""
    return m


def _recorder(*, returncode: int = 0, stdout: str = "removed\n"):
    calls: list[list[str]] = []

    def _run(argv: object, **kwargs: object) -> MagicMock:
        calls.append(list(argv))  # type: ignore[arg-type]
        return _completed(returncode, stdout)

    return calls, _run


# ── rw_seam_available ────────────────────────────────────────────────────────


def test_rw_seam_available_false_when_not_hal0_user(tmp_path, monkeypatch) -> None:
    fake_bin = tmp_path / "hal0-podman-rw"
    fake_bin.write_text("#!/bin/sh\n")
    monkeypatch.setattr(podman_mutate, "RW_SEAM_BIN", str(fake_bin))
    assert rw_seam_available(is_hal0_user=lambda: False) is False


def test_rw_seam_available_false_when_binary_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(podman_mutate, "RW_SEAM_BIN", str(tmp_path / "nope"))
    assert rw_seam_available(is_hal0_user=lambda: True) is False


def test_rw_seam_available_true_when_user_and_binary_present(tmp_path, monkeypatch) -> None:
    fake_bin = tmp_path / "hal0-podman-rw"
    fake_bin.write_text("#!/bin/sh\n")
    monkeypatch.setattr(podman_mutate, "RW_SEAM_BIN", str(fake_bin))
    assert rw_seam_available(is_hal0_user=lambda: True) is True


# ── remove_image: short-circuits (no subprocess call) ───────────────────────


def test_remove_image_bad_ref_short_circuits_without_subprocess() -> None:
    calls, run = _recorder()
    outcome, reason = remove_image(_BAD_REF, run=run, is_hal0_user=lambda: True)

    assert calls == []
    assert (outcome, reason) == ("unknown", "invalid-argument")


def test_remove_image_not_service_user_short_circuits_without_subprocess(monkeypatch) -> None:
    """Pins the persona explicitly: non-service-user AND non-root. Without
    this pin, a CI runner that executes tests as root (euid 0) falls through
    into the root fallback added for FIX 3 and calls podman directly instead
    of short-circuiting — this test is specifically about the short-circuit,
    so it must not depend on the ambient euid of whatever box runs it."""
    monkeypatch.setattr(podman_mutate.os, "geteuid", lambda: 1000)
    calls, run = _recorder()
    outcome, reason = remove_image(_REF, run=run, is_hal0_user=lambda: False)

    assert calls == []
    assert (outcome, reason) == ("unknown", "not-service-user")


# ── remove_image: seam argv ──────────────────────────────────────────────────


def test_remove_image_seam_argv() -> None:
    calls, run = _recorder()
    remove_image(_REF, run=run, is_hal0_user=lambda: True)

    assert calls == [["sudo", "-n", RW_SEAM_BIN, "image-rm", _REF]]


# ── remove_image: outcome mapping ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("returncode", "stdout", "expected"),
    [
        (0, "removed\n", ("removed", None)),
        (0, "missing\n", ("missing", None)),
        (67, "", ("in-use", None)),
        (1, "", ("unknown", "grant-denied")),
        (64, "", ("unknown", "invalid-argument")),
        (65, "", ("unknown", "podman-absent")),
        (66, "", ("unknown", "podman-failed")),
        (99, "", ("unknown", "seam-error")),  # rc the contract does not define
        (0, "bogus\n", ("unknown", "seam-error")),  # rc0 with undefined stdout
    ],
)
def test_remove_image_outcome_mapping(returncode, stdout, expected) -> None:
    _, run = _recorder(returncode=returncode, stdout=stdout)
    assert remove_image(_REF, run=run, is_hal0_user=lambda: True) == expected


def test_remove_image_exec_failure_is_seam_error() -> None:
    def _raise(*_args: object, **_kwargs: object) -> MagicMock:
        raise OSError("no such file")

    assert remove_image(_REF, run=_raise, is_hal0_user=lambda: True) == ("unknown", "seam-error")


# ── remove_image: root fallback (not the hal0 service user, but euid 0) ─────


def test_remove_image_root_fallback_removed_no_sudo_in_argv(monkeypatch) -> None:
    monkeypatch.setattr(podman_mutate.os, "geteuid", lambda: 0)
    calls, run = _recorder(returncode=0, stdout="")

    outcome, reason = remove_image(
        _REF, run=run, is_hal0_user=lambda: False, which=lambda _name: "/usr/bin/podman"
    )

    assert (outcome, reason) == ("removed", None)
    assert calls == [["/usr/bin/podman", "rmi", "--", _REF]]
    assert "sudo" not in calls[0]


@pytest.mark.parametrize(
    ("returncode", "expected"),
    [
        (0, ("removed", None)),
        (1, ("missing", None)),
        (2, ("in-use", None)),
        (3, ("unknown", "podman-failed")),
    ],
)
def test_remove_image_root_fallback_outcome_mapping(monkeypatch, returncode, expected) -> None:
    monkeypatch.setattr(podman_mutate.os, "geteuid", lambda: 0)
    _, run = _recorder(returncode=returncode, stdout="")

    outcome = remove_image(
        _REF, run=run, is_hal0_user=lambda: False, which=lambda _name: "/usr/bin/podman"
    )

    assert outcome == expected


def test_remove_image_root_fallback_podman_absent(monkeypatch) -> None:
    monkeypatch.setattr(podman_mutate.os, "geteuid", lambda: 0)
    calls, run = _recorder()

    outcome = remove_image(_REF, run=run, is_hal0_user=lambda: False, which=lambda _name: None)

    assert outcome == ("unknown", "podman-absent")
    assert calls == []


def test_remove_image_root_fallback_exec_failure_is_podman_failed(monkeypatch) -> None:
    monkeypatch.setattr(podman_mutate.os, "geteuid", lambda: 0)

    def _raise(*_args: object, **_kwargs: object) -> MagicMock:
        raise OSError("no such file")

    outcome = remove_image(
        _REF, run=_raise, is_hal0_user=lambda: False, which=lambda _name: "/usr/bin/podman"
    )

    assert outcome == ("unknown", "podman-failed")


def test_remove_image_non_root_non_service_user_unchanged(monkeypatch) -> None:
    """Not root and not the hal0 service account: still ``not-service-user``,
    no fallback, no subprocess call."""
    monkeypatch.setattr(podman_mutate.os, "geteuid", lambda: 1000)
    calls, run = _recorder()

    outcome = remove_image(_REF, run=run, is_hal0_user=lambda: False)

    assert outcome == ("unknown", "not-service-user")
    assert calls == []


# ── pull_image_stream_rootful ────────────────────────────────────────────────


class _FakeStdout:
    def __init__(self, lines: list[bytes]) -> None:
        self._iter = iter(lines)

    def __aiter__(self) -> _FakeStdout:
        return self

    async def __anext__(self) -> bytes:
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration from None


class _FakeProc:
    def __init__(self, lines: list[bytes], returncode: int) -> None:
        self.stdout = _FakeStdout(lines)
        self._returncode = returncode
        self.killed = False
        self.terminated = False

    def kill(self) -> None:
        self.killed = True

    def terminate(self) -> None:
        self.terminated = True

    async def wait(self) -> int:
        return self._returncode


class _HangingStdout:
    """A ``proc.stdout`` whose iterator never advances past its first line.

    Used to hold :func:`podman_mutate.pull_image_stream_rootful` paused
    mid-stream so the test can ``aclose()`` the generator from the outside —
    the same way a cancelled consumer (e.g. an HTTP client disconnecting
    mid-download) tears the generator down — and observe which signal method
    the ``finally`` block invokes on the still-running seam process.
    """

    def __init__(self, lines: list[bytes]) -> None:
        self._iter = iter(lines)
        self._yielded_first = False

    def __aiter__(self) -> _HangingStdout:
        return self

    async def __anext__(self) -> bytes:
        if self._yielded_first:
            # Never resolves on its own — the test tears the generator down
            # with aclose() instead of letting this line complete.
            await asyncio.Future()
        self._yielded_first = True
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration from None


class _HangingFakeProc(_FakeProc):
    def __init__(self, lines: list[bytes], returncode: int) -> None:
        super().__init__(lines, returncode)
        self.stdout = _HangingStdout(lines)


class _TermIgnoringFakeProc(_HangingFakeProc):
    """A hung process that ignores SIGTERM: ``wait()`` only ever resolves
    after ``kill()`` — models a wedged ``sudo``/``podman pull`` that never
    reacts to the relayed SIGTERM, so the teardown path's escalation to
    SIGKILL is the only thing that can end it."""

    def __init__(self, lines: list[bytes], returncode: int) -> None:
        super().__init__(lines, returncode)
        self._exited = asyncio.Event()

    def kill(self) -> None:
        super().kill()
        self._exited.set()

    async def wait(self) -> int:
        await self._exited.wait()
        return self._returncode


async def test_pull_image_stream_rootful_bad_ref_short_circuits(monkeypatch) -> None:
    calls: list[tuple] = []

    async def _fake_create(*args: object, **kwargs: object) -> _FakeProc:
        calls.append(args)
        return _FakeProc([], 0)

    monkeypatch.setattr(podman_mutate.asyncio, "create_subprocess_exec", _fake_create)

    events = [e async for e in pull_image_stream_rootful(_BAD_REF)]

    assert calls == []
    assert events == [{"state": "failed", "error": f"invalid image reference: {_BAD_REF}"}]


async def test_pull_image_stream_rootful_argv(monkeypatch) -> None:
    calls: list[tuple] = []

    async def _fake_create(*args: object, **kwargs: object) -> _FakeProc:
        calls.append(args)
        return _FakeProc([], 0)

    monkeypatch.setattr(podman_mutate.asyncio, "create_subprocess_exec", _fake_create)

    async for _ in pull_image_stream_rootful(_REF):
        pass

    assert calls == [("sudo", "-n", RW_SEAM_BIN, "image-pull", _REF)]


async def test_pull_image_stream_rootful_pulling_then_completed(monkeypatch) -> None:
    lines = [
        b"Pulling fs layer\n",
        b"Download complete\n",
    ]

    async def _fake_create(*_args: object, **_kwargs: object) -> _FakeProc:
        return _FakeProc(lines, 0)

    monkeypatch.setattr(podman_mutate.asyncio, "create_subprocess_exec", _fake_create)

    events = [e async for e in pull_image_stream_rootful(_REF)]

    assert events == [
        {"state": "pulling", "layer": 0, "total_layers": 1, "line": "Pulling fs layer"},
        {"state": "pulling", "layer": 1, "total_layers": 1, "line": "Download complete"},
        {"state": "completed", "layer": 1, "total_layers": 1},
    ]


async def test_pull_image_stream_rootful_nonzero_exit_is_failed(monkeypatch) -> None:
    async def _fake_create(*_args: object, **_kwargs: object) -> _FakeProc:
        return _FakeProc([b"some error\n"], 1)

    monkeypatch.setattr(podman_mutate.asyncio, "create_subprocess_exec", _fake_create)

    events = [e async for e in pull_image_stream_rootful(_REF)]

    assert events[-1] == {"state": "failed", "error": "pull exited with code 1"}


# ── pull_image_stream_rootful: grant-denied rc-1 message enrichment ────────


_GRANT_DENIED_MESSAGE = (
    "write seam grant denied or misconfigured — check /etc/sudoers.d/hal0-podman-rw "
    "(pull exited with code 1)"
)


async def test_pull_image_stream_rootful_rc1_no_events_yields_grant_denied(monkeypatch) -> None:
    """``sudo -n`` denies before the child even runs: no stdout at all, rc 1.
    That is the "no events yielded" shape of a grant-denied failure —
    enrich the message so an operator isn't left staring at a bare
    ``pull exited with code 1``."""

    async def _fake_create(*_args: object, **_kwargs: object) -> _FakeProc:
        return _FakeProc([], 1)

    monkeypatch.setattr(podman_mutate.asyncio, "create_subprocess_exec", _fake_create)

    events = [e async for e in pull_image_stream_rootful(_REF)]

    assert events == [{"state": "failed", "error": _GRANT_DENIED_MESSAGE}]


async def test_pull_image_stream_rootful_rc1_sudo_error_line_yields_grant_denied(
    monkeypatch,
) -> None:
    """``sudo -n`` prints its own diagnostic to stderr (merged into stdout
    here) before exiting 1 — every yielded line is sudo-shaped, so this is
    still honestly "no real pull activity happened", just enriched."""

    async def _fake_create(*_args: object, **_kwargs: object) -> _FakeProc:
        return _FakeProc([b"sudo: a password is required\n"], 1)

    monkeypatch.setattr(podman_mutate.asyncio, "create_subprocess_exec", _fake_create)

    events = [e async for e in pull_image_stream_rootful(_REF)]

    assert events[-1] == {"state": "failed", "error": _GRANT_DENIED_MESSAGE}


async def test_pull_image_stream_rootful_rc1_after_real_progress_stays_bare(monkeypatch) -> None:
    """The pull actually started (a real podman progress line came through)
    before something killed it at rc 1 — that is NOT a grant-denial shape,
    so the bare exit-code message stays honest rather than blaming sudo for
    a failure that happened well past the sudo gate."""

    async def _fake_create(*_args: object, **_kwargs: object) -> _FakeProc:
        return _FakeProc([b"Pulling fs layer\n"], 1)

    monkeypatch.setattr(podman_mutate.asyncio, "create_subprocess_exec", _fake_create)

    events = [e async for e in pull_image_stream_rootful(_REF)]

    assert events[-1] == {"state": "failed", "error": "pull exited with code 1"}


async def test_pull_image_stream_rootful_rc2_stays_bare_even_with_no_events(monkeypatch) -> None:
    """The enrichment is scoped to rc 1 (the sudo/grant exit code) only —
    any other nonzero exit keeps the plain message, even with zero
    events yielded."""

    async def _fake_create(*_args: object, **_kwargs: object) -> _FakeProc:
        return _FakeProc([], 2)

    monkeypatch.setattr(podman_mutate.asyncio, "create_subprocess_exec", _fake_create)

    events = [e async for e in pull_image_stream_rootful(_REF)]

    assert events == [{"state": "failed", "error": "pull exited with code 2"}]


async def test_pull_image_stream_rootful_clean_eof_never_signals_process(monkeypatch) -> None:
    """Regression: a fully successful pull must not be torn down by the
    ``finally`` block. Before the fix, ``finally: proc.kill()`` fired
    unconditionally — including on a clean EOF, in the window before
    ``await proc.wait()`` — and could turn a completed pull into a reported
    ``{"state": "failed", "error": "pull exited with code -9"}``."""
    proc = _FakeProc([b"Pulling fs layer\n", b"Download complete\n"], 0)

    async def _fake_create(*_args: object, **_kwargs: object) -> _FakeProc:
        return proc

    monkeypatch.setattr(podman_mutate.asyncio, "create_subprocess_exec", _fake_create)

    events = [e async for e in pull_image_stream_rootful(_REF)]

    assert proc.killed is False
    assert proc.terminated is False
    assert events[-1] == {"state": "completed", "layer": 1, "total_layers": 1}


async def test_pull_image_stream_rootful_cancel_calls_terminate_not_kill(monkeypatch) -> None:
    """A consumer that tears the generator down mid-stream (cancellation,
    early ``break``/``aclose()``) must ``terminate()`` (SIGTERM) the seam
    process — and, when the process exits within the grace period (as this
    fake does, immediately), must never escalate to ``kill()`` (SIGKILL).

    ``proc`` here is ``sudo``, not ``podman`` directly — sudo can relay a
    catchable signal like SIGTERM to the podman child it launched, but
    SIGKILL cannot be caught or relayed by sudo at all, so a ``.kill()``
    here would leave the root-owned ``podman pull`` running, orphaned,
    to completion or failure with nobody watching. Escalation exists only
    for the wedged case — see the ``escalates_to_kill_after_grace`` test.
    """
    proc = _HangingFakeProc([b"Pulling fs layer\n"], 0)

    async def _fake_create(*_args: object, **_kwargs: object) -> _HangingFakeProc:
        return proc

    monkeypatch.setattr(podman_mutate.asyncio, "create_subprocess_exec", _fake_create)

    agen = pull_image_stream_rootful(_REF)
    first_event = await agen.__anext__()
    assert first_event["state"] == "pulling"

    await agen.aclose()

    assert proc.terminated is True
    assert proc.killed is False


async def test_pull_image_stream_rootful_cancel_escalates_to_kill_after_grace(
    monkeypatch,
) -> None:
    """SIGTERM is polite, not a guarantee: a wedged ``sudo``/``podman pull``
    that never exits after ``terminate()`` must be ``kill()``ed once the
    grace period lapses, or teardown blocks forever on a process that will
    never leave on its own.

    ``TERMINATE_GRACE_SECONDS`` is patched near zero so this test does not
    actually sit out the production grace period.
    """
    proc = _TermIgnoringFakeProc([b"Pulling fs layer\n"], 0)

    async def _fake_create(*_args: object, **_kwargs: object) -> _TermIgnoringFakeProc:
        return proc

    monkeypatch.setattr(podman_mutate.asyncio, "create_subprocess_exec", _fake_create)
    monkeypatch.setattr(podman_mutate, "TERMINATE_GRACE_SECONDS", 0.01)

    agen = pull_image_stream_rootful(_REF)
    first_event = await agen.__anext__()
    assert first_event["state"] == "pulling"

    await asyncio.wait_for(agen.aclose(), timeout=5.0)

    assert proc.terminated is True
    assert proc.killed is True


# ── PullLineParser: parity with the pre-extraction container.py heuristic ──


def test_pull_line_parser_matches_pre_extraction_sequence() -> None:
    """Canned podman/docker-style non-TTY pull output, fed line-by-line.

    This is the exact event sequence ``ContainerProvider.pull_image_stream``
    produced before its layer-counting logic was extracted into
    :class:`PullLineParser` — pins the heuristic so container.py's refactor
    in this change cannot silently drift from it.
    """
    lines = [
        "Trying to pull docker.io/library/alpine:latest...",
        "Getting image source signatures",
        "Pulling fs layer",
        "Waiting",
        "Verifying Checksum",
        "Download complete",
        "Pull complete",
        "Already exists",
        "Writing manifest to image destination",
    ]

    parser = PullLineParser()
    events = [parser.feed(line) for line in lines]

    assert events == [
        {"state": "pulling", "layer": 0, "total_layers": 0, "line": lines[0]},
        {"state": "pulling", "layer": 0, "total_layers": 0, "line": lines[1]},
        {"state": "pulling", "layer": 0, "total_layers": 1, "line": lines[2]},
        {"state": "pulling", "layer": 0, "total_layers": 2, "line": lines[3]},
        {"state": "pulling", "layer": 0, "total_layers": 3, "line": lines[4]},
        {"state": "pulling", "layer": 1, "total_layers": 3, "line": lines[5]},
        {"state": "pulling", "layer": 2, "total_layers": 3, "line": lines[6]},
        {"state": "pulling", "layer": 3, "total_layers": 4, "line": lines[7]},
        {"state": "pulling", "layer": 3, "total_layers": 4, "line": lines[8]},
    ]
