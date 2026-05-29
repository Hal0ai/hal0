"""δ-harness — Hermes ``delegate_task`` over the DOCKER execution backend.

Reference: upstream pin ``0554ef1a`` (Hal0ai/hal0 pyproject ``[tool.hal0.upstream-hermes]``).

The DOCKER backend (``tools/environments/docker.py::DockerEnvironment``)
is the per-subagent isolation story upstream pitches for "container
trust boundary" workflows.  It accepts ``image`` + ``cpu`` + ``memory``
+ ``disk`` + ``volumes`` knobs and is selected via ``TERMINAL_ENV=docker``.

These tests prove the dispatch hop without launching real containers:
``FakeDockerBackend`` captures the image + sandbox kwargs alongside the
``execute()`` call, lets tests simulate "docker not available" via
``unavailable=True``, and round-trips scripted output back through the
parent's assistant response.

Findings rows for the first green run live in
``tests/harness/FINDINGS.md`` §46.
"""

from __future__ import annotations

import json

from tests.harness.integration._delegate_fakes import (
    FakeBackendResult,
    FakeDockerBackend,
)
from tests.harness.integration._delegate_runner import (
    DelegateTaskSpec,
    FakeDelegateRunner,
)


def test_docker_backend_round_trips_with_image_kwargs() -> None:
    """Happy path: ``image=alpine:3.20`` reaches the backend + output returns."""
    backend = FakeDockerBackend(image="alpine:3.20")
    backend.queue_result(FakeBackendResult(output="hi from alpine"))

    runner = FakeDelegateRunner()
    runner.register_backend("docker", lambda _kw: backend)

    trace = runner.run_delegate_task(
        [
            DelegateTaskSpec(
                goal="echo from alpine",
                backend="docker",
                commands=["echo 'hi from alpine'"],
                backend_kwargs={"image": "alpine:3.20"},
            ),
        ]
    )

    assert "hi from alpine" in trace.final_response
    assert trace.results[0].error is None
    assert backend.session_initialised
    assert backend.cleanup_called


def test_docker_backend_unavailable_degrades_gracefully() -> None:
    """``init_session()`` raise (no docker daemon) becomes a per-task error,
    not a parent crash.

    Mirrors the real-world failure mode where the user picked
    ``TERMINAL_ENV=docker`` but the docker socket isn't reachable.
    The upstream code path uses the same try/finally envelope around
    ``init_session`` → execute → cleanup that
    ``FakeDelegateRunner.run_delegate_task`` mirrors.
    """
    backend = FakeDockerBackend(image="alpine:3.20", unavailable=True)
    runner = FakeDelegateRunner()
    runner.register_backend("docker", lambda _kw: backend)

    trace = runner.run_delegate_task(
        [
            DelegateTaskSpec(
                goal="echo from alpine",
                backend="docker",
                commands=["echo hi"],
            ),
        ]
    )

    result = trace.results[0]
    assert result.error is not None
    assert "docker daemon not reachable" in result.error
    # No execute() invocations because init_session() crashed first.
    assert backend.invocations == []
    envelope = json.loads(trace.raw_envelope_json)
    assert envelope["results"][0]["error"] is not None


def test_docker_backend_payload_includes_container_kwargs() -> None:
    """Capture the full sandbox-spec so tests can assert provisioning intent.

    The DA must-fix #2 specifically asked: "does the docker backend
    actually receive the image + cwd + cpu/memory kwargs?".  Without
    this assertion a regression where upstream renames ``image`` to
    ``container_image`` (or similar drift) silently breaks dispatch.
    """
    backend = FakeDockerBackend(
        image="python:3.12-slim",
        cwd="/workspace/delegate",
        cpu=2,
        memory=8192,
        disk=20480,
        volumes=["/host/code:/workspace/code"],
        env={"PYTHONUNBUFFERED": "1"},
    )
    backend.queue_result(FakeBackendResult(output="python ok"))

    runner = FakeDelegateRunner()
    runner.register_backend("docker", lambda _kw: backend)

    runner.run_delegate_task(
        [
            DelegateTaskSpec(
                goal="run python",
                backend="docker",
                commands=["python --version"],
            ),
        ]
    )

    assert len(backend.invocations) == 1
    ctx = backend.invocations[0].backend_context
    assert ctx["backend"] == "docker"
    assert ctx["image"] == "python:3.12-slim"
    assert ctx["cpu"] == 2
    assert ctx["memory"] == 8192
    assert ctx["disk"] == 20480
    assert ctx["volumes"] == ["/host/code:/workspace/code"]
    assert ctx["env"]["PYTHONUNBUFFERED"] == "1"
    # cwd defaults from the backend constructor since DelegateTaskSpec
    # didn't override.
    assert backend.invocations[0].cwd == "/workspace/delegate"


def test_docker_backend_nonzero_returncode_surfaces_as_error() -> None:
    """Exit code 127 (command not found) becomes an inline error."""
    backend = FakeDockerBackend()
    backend.queue_result(FakeBackendResult(output="not found", returncode=127))

    runner = FakeDelegateRunner()
    runner.register_backend("docker", lambda _kw: backend)

    trace = runner.run_delegate_task(
        [
            DelegateTaskSpec(
                goal="run missing tool",
                backend="docker",
                commands=["totally-fake-bin"],
            ),
        ]
    )

    assert trace.results[0].error is not None
    assert "127" in trace.results[0].error
    # Output captured even when nonzero — caller may want to inspect it.
    assert "not found" in trace.results[0].output
