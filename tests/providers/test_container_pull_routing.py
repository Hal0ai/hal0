"""#2119 — ``ContainerProvider.pull_image_stream`` routes through the rootful
write seam when it is available.

The bug this pins: a dashboard-triggered "pull image" landed in hal0-api's
own ROOTLESS podman store (a bare ``podman pull`` run as the unprivileged
``hal0`` service user), while slots launch from ROOT's store via Quadlet.
The pulled image was therefore invisible to every slot that tried to use it
— the write-side twin of #1889 (reads already fixed to go through
``hal0-podman-ro``; this is ``hal0-podman-rw`` for the pull itself).

``podman_mutate`` is patched wholesale (``rw_seam_available`` +
``pull_image_stream_rootful``) rather than ``subprocess``/``asyncio``, so
these tests never touch sudo, a real ``hal0`` account, or podman.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from hal0.providers import container as container_mod
from hal0.providers import podman_mutate
from hal0.providers.container import ContainerProvider

IMAGE = "ghcr.io/thinmintdev/hal0-runner:v1.0.0-rc.6"


async def _fake_rootful_pull(image: str) -> AsyncIterator[dict[str, Any]]:
    yield {"state": "pulling", "layer": 1, "total_layers": 3, "line": f"pulling {image}"}
    yield {"state": "completed", "layer": 3, "total_layers": 3}


@pytest.mark.asyncio
async def test_pull_routes_through_rootful_seam_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point of #2119: when the seam is usable, its events stream
    straight through and the rootless runtime is never consulted."""
    monkeypatch.setattr(podman_mutate, "rw_seam_available", lambda: True)

    seen: list[str] = []

    async def _rootful(image: str) -> AsyncIterator[dict[str, Any]]:
        seen.append(image)
        async for event in _fake_rootful_pull(image):
            yield event

    monkeypatch.setattr(podman_mutate, "pull_image_stream_rootful", _rootful)

    def _boom() -> str:
        raise AssertionError("rootless runtime was consulted despite the seam being available")

    monkeypatch.setattr(container_mod, "_container_runtime", _boom)

    events = [e async for e in ContainerProvider().pull_image_stream(IMAGE)]

    assert seen == [IMAGE]
    assert events == [
        {"state": "pulling", "layer": 1, "total_layers": 3, "line": f"pulling {IMAGE}"},
        {"state": "completed", "layer": 3, "total_layers": 3},
    ]


@pytest.mark.asyncio
async def test_pull_aclose_propagates_to_rootful_inner_generator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fix-round-1 regression: a dashboard cancel calls ``.aclose()`` on the
    OUTER generator (``runner_pull.run_runner_pull``'s teardown path). A bare
    ``async for ... yield`` delegation does NOT forward that ``.aclose()`` to
    the inner rootful generator, so the ROOT-owned
    ``sudo hal0-podman-rw image-pull`` subprocess it kills in its own
    ``finally`` would keep pulling until GC eventually (maybe) finalizes it.
    This pins that the inner generator's cleanup runs BEFORE the outer
    ``.aclose()`` call returns."""
    monkeypatch.setattr(podman_mutate, "rw_seam_available", lambda: True)

    cleanup_ran = False

    async def _rootful(image: str) -> AsyncIterator[dict[str, Any]]:
        nonlocal cleanup_ran
        try:
            yield {"state": "pulling", "layer": 1, "total_layers": 3, "line": "layer 1"}
            yield {"state": "pulling", "layer": 2, "total_layers": 3, "line": "layer 2"}
            yield {"state": "completed", "layer": 3, "total_layers": 3}
        finally:
            cleanup_ran = True

    monkeypatch.setattr(podman_mutate, "pull_image_stream_rootful", _rootful)

    def _boom() -> str:
        raise AssertionError("rootless runtime was consulted despite the seam being available")

    monkeypatch.setattr(container_mod, "_container_runtime", _boom)

    agen = ContainerProvider().pull_image_stream(IMAGE)
    first = await agen.__anext__()

    assert first["state"] == "pulling"
    assert cleanup_ran is False, "inner generator must still be open after only one event"

    await agen.aclose()

    assert cleanup_ran is True, "outer .aclose() must propagate into the inner rootful generator"


@pytest.mark.asyncio
async def test_pull_falls_back_to_rootless_when_seam_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Dev checkout / CI / no grant: today's bare ``<runtime> pull`` behavior,
    exercised exactly as before the seam existed."""
    monkeypatch.setattr(podman_mutate, "rw_seam_available", lambda: False)

    def _rootful_should_not_run(image: str) -> AsyncIterator[dict[str, Any]]:
        raise AssertionError("rootful pull was invoked despite the seam being unavailable")

    monkeypatch.setattr(podman_mutate, "pull_image_stream_rootful", _rootful_should_not_run)

    fake_runtime = tmp_path / "fake-pull"
    fake_runtime.write_text(
        "#!/bin/sh\n"
        'echo "Pulling from library/alpine"\n'
        'echo "abc123: Pulling fs layer"\n'
        'echo "abc123: Download complete"\n'
        'echo "abc123: Pull complete"\n'
        "exit 0\n"
    )
    fake_runtime.chmod(0o755)
    monkeypatch.setattr(container_mod, "_container_runtime", lambda: str(fake_runtime))

    events = [e async for e in ContainerProvider().pull_image_stream("alpine:latest")]

    states = [e["state"] for e in events]
    assert "pulling" in states
    assert states[-1] == "completed"


class _Py312ReapingProcess:
    """Stand-in for ``asyncio.subprocess.Process`` with CPython 3.12's
    ``kill()`` semantics (#2814).

    On 3.12, ``Process.kill()`` reaches ``Popen.send_signal()``, which calls
    ``Popen.poll()`` (a ``waitpid(pid, WNOHANG)``) before signalling. If the
    child has already exited but asyncio's child watcher has not run yet, that
    poll reaps it, and the watcher then reports a synthetic returncode of 255
    ("child process pid N exit status already read"). This fake models that
    outcome deterministically: once the stream is drained the child counts as
    exited, so a ``kill()`` after EOF reaps it and ``wait()`` returns 255.
    """

    def __init__(self, lines: list[bytes]) -> None:
        self._lines = lines
        self.returncode: int | None = None
        self.killed = False
        self._drained = False
        self._reaped_behind_watcher = False
        self.stdout = self._stream()

    async def _stream(self) -> AsyncIterator[bytes]:
        for line in self._lines:
            yield line
        self._drained = True

    def kill(self) -> None:
        self.killed = True
        if self._drained:
            self._reaped_behind_watcher = True

    async def wait(self) -> int:
        self.returncode = 255 if self._reaped_behind_watcher else 0
        return self.returncode


def _patch_rootless_spawn(monkeypatch: pytest.MonkeyPatch, proc: _Py312ReapingProcess) -> None:
    import asyncio

    monkeypatch.setattr(podman_mutate, "rw_seam_available", lambda: False)
    monkeypatch.setattr(container_mod, "_container_runtime", lambda: "/fake/podman")

    async def _spawn(*_args: Any, **_kwargs: Any) -> _Py312ReapingProcess:
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _spawn)


@pytest.mark.asyncio
async def test_rootless_pull_does_not_kill_after_clean_eof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#2814: the rootless path used to ``proc.kill()`` in a ``finally`` right
    after stdout hit EOF and before ``proc.wait()``. On Python 3.12 that kill
    polls and can reap the already-exited runtime behind asyncio's watcher, so
    a successful pull read back as exit 255 and the stream ended ``failed``.
    The CI flake in ``test_pull_falls_back_to_rootless_when_seam_unavailable``
    and ``test_pull_image_stream_completed_on_success`` was this race."""
    proc = _Py312ReapingProcess([b"Pulling from library/alpine\n", b"abc123: Pull complete\n"])
    _patch_rootless_spawn(monkeypatch, proc)

    events = [e async for e in ContainerProvider().pull_image_stream("alpine:latest")]

    assert proc.killed is False
    assert events[-1]["state"] == "completed"


@pytest.mark.asyncio
async def test_rootless_pull_kills_runtime_when_consumer_aborts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The abort path (a dashboard cancel calls ``.aclose()``) still kills the
    runtime, so dropping the post-EOF kill does not leak a running pull."""
    proc = _Py312ReapingProcess([b"abc123: Pulling fs layer\n", b"abc123: Pull complete\n"])
    _patch_rootless_spawn(monkeypatch, proc)

    agen = ContainerProvider().pull_image_stream("alpine:latest")
    first = await agen.__anext__()
    await agen.aclose()

    assert first["state"] == "pulling"
    assert proc.killed is True
