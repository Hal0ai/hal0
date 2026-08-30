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


def test_remove_image_not_service_user_short_circuits_without_subprocess() -> None:
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

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
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
