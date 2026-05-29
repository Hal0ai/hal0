"""δ-harness — Hermes ``delegate_task`` over the LOCAL execution backend.

Reference: upstream pin ``0554ef1a`` (Hal0ai/hal0 pyproject ``[tool.hal0.upstream-hermes]``).

The LOCAL backend (``tools/environments/local.py::LocalEnvironment``)
is the default for ``TERMINAL_ENV`` and the one every hal0 user hits
unless they explicitly switch backends.  This file gates the spawn →
local-backend dispatch hop end-to-end.

Tests use ``FakeLocalBackend`` (signature-compatible with upstream's
``BaseEnvironment``) so no real subprocess is required.  CI runs this
on any platform; the equivalent gamma-tier coverage is provided by
``scripts/release-test.sh`` on the hal0-test LXC.

Findings rows for the first green run live in
``tests/harness/FINDINGS.md`` §46.
"""

from __future__ import annotations

import json

import pytest
from tests.harness.integration._delegate_fakes import (
    FakeBackendResult,
    FakeLocalBackend,
)
from tests.harness.integration._delegate_runner import (
    DelegateTaskSpec,
    FakeDelegateRunner,
)


def _runner_for_backend(backend: FakeLocalBackend) -> FakeDelegateRunner:
    """Wire a runner that hands the scripted ``backend`` to every task."""
    runner = FakeDelegateRunner()
    runner.register_backend("local", lambda _kw: backend)
    return runner


def test_local_backend_round_trips_simple_echo() -> None:
    """Happy path: echo "hello" round-trips into the assistant response."""
    backend = FakeLocalBackend()
    backend.queue_result(FakeBackendResult(output="hello\n", returncode=0))
    runner = _runner_for_backend(backend)

    spec = DelegateTaskSpec(
        goal="say hello",
        backend="local",
        commands=["echo hello"],
    )
    trace = runner.run_delegate_task([spec])

    assert "hello" in trace.final_response, trace.final_response
    assert len(trace.results) == 1
    assert trace.results[0].error is None
    assert trace.results[0].backend == "local"
    assert backend.session_initialised is True
    assert backend.cleanup_called is True


def test_local_backend_records_invocation_count_and_payload() -> None:
    """The backend captures the exact command + cwd the runner dispatched."""
    backend = FakeLocalBackend(cwd="/tmp/hermes-local-test")
    backend.queue_result(FakeBackendResult(output="ok"))
    runner = _runner_for_backend(backend)

    runner.run_delegate_task(
        [
            DelegateTaskSpec(
                goal="check current dir",
                backend="local",
                commands=["pwd && echo done"],
            ),
        ]
    )

    assert len(backend.invocations) == 1
    inv = backend.invocations[0]
    assert inv.command == "pwd && echo done"
    assert inv.cwd == "/tmp/hermes-local-test"
    assert inv.backend_context["backend"] == "local"


def test_local_backend_error_envelope_does_not_crash_parent() -> None:
    """A backend ``execute()`` raise propagates as a per-task ``error`` slot."""
    backend = FakeLocalBackend()
    backend.queue_result(FakeBackendResult(raises=RuntimeError("simulated shell crash")))
    runner = _runner_for_backend(backend)

    trace = runner.run_delegate_task(
        [
            DelegateTaskSpec(
                goal="cause an error",
                backend="local",
                commands=["bad command"],
            ),
        ]
    )

    assert len(trace.results) == 1
    result = trace.results[0]
    assert result.error is not None
    assert "simulated shell crash" in result.error
    # Final response surfaces the error rather than crashing.
    assert "error" in trace.final_response.lower()
    # Envelope still emits valid JSON for the parent's tool_result.
    envelope = json.loads(trace.raw_envelope_json)
    assert envelope["results"][0]["error"] is not None


def test_local_backend_empty_goal_rejected_before_dispatch() -> None:
    """Mirrors upstream tools/delegate_tool.py:2034 — empty goal is a hard reject."""
    runner = _runner_for_backend(FakeLocalBackend())

    with pytest.raises(ValueError, match="empty goal"):
        runner.run_delegate_task(
            [
                DelegateTaskSpec(
                    goal="   ",
                    backend="local",
                    commands=["echo never reached"],
                ),
            ]
        )
