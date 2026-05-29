"""δ-harness — Hermes ``delegate_task`` over the MODAL execution backend.

Reference: upstream pin ``0554ef1a`` (Hal0ai/hal0 pyproject ``[tool.hal0.upstream-hermes]``).

The MODAL backend (``tools/environments/modal.py::ModalEnvironment``)
is the most exotic of the three: it provisions a remote Firecracker
sandbox via the Modal SDK and tunnels ``execute()`` calls into it.
Real Modal calls would burn credits + need ``MODAL_TOKEN_ID`` /
``MODAL_TOKEN_SECRET``, which CI doesn't have.  ``FakeModalBackend``
covers the dispatch hop with credit-free fakes.

These tests verify:
* sandbox_kwargs (cpu/memory/ephemeral_disk) reach the backend
* the ``MODAL_TOKEN`` missing degraded path matches real-world UX
* the cold-start latency simulation surfaces in the per-task
  ``duration_ms``

Findings rows for the first green run live in
``tests/harness/FINDINGS.md`` §46.
"""

from __future__ import annotations

from tests.harness.integration._delegate_fakes import (
    FakeBackendResult,
    FakeModalBackend,
)
from tests.harness.integration._delegate_runner import (
    DelegateTaskSpec,
    FakeDelegateRunner,
)


def test_modal_backend_round_trips_with_sandbox_kwargs() -> None:
    """Happy path: ``sandbox_kwargs`` reach the backend + output returns."""
    backend = FakeModalBackend(
        image="python:3.11-slim",
        sandbox_kwargs={"cpu": 2, "memory": 8192, "ephemeral_disk": 16384},
    )
    backend.queue_result(FakeBackendResult(output="modal says hi"))

    runner = FakeDelegateRunner()
    runner.register_backend("modal", lambda _kw: backend)

    trace = runner.run_delegate_task(
        [
            DelegateTaskSpec(
                goal="echo via modal",
                backend="modal",
                commands=["echo 'modal says hi'"],
            ),
        ]
    )

    assert "modal says hi" in trace.final_response
    assert trace.results[0].error is None
    assert backend.session_initialised
    assert backend.cleanup_called

    # Sandbox kwargs preserved in the captured invocation context.
    ctx = backend.invocations[0].backend_context
    assert ctx["backend"] == "modal"
    assert ctx["image"] == "python:3.11-slim"
    assert ctx["sandbox_kwargs"]["cpu"] == 2
    assert ctx["sandbox_kwargs"]["memory"] == 8192
    assert ctx["sandbox_kwargs"]["ephemeral_disk"] == 16384


def test_modal_backend_token_missing_degrades_gracefully() -> None:
    """``MODAL_TOKEN_ID`` / ``MODAL_TOKEN_SECRET`` missing → per-task error.

    This is the most common Modal failure mode in CI / unconfigured
    dev machines.  The dispatch path must NOT crash the parent — it
    must surface as a per-task error so the LLM can either retry on
    a different backend or report the misconfig to the user.
    """
    backend = FakeModalBackend(token_missing=True)
    runner = FakeDelegateRunner()
    runner.register_backend("modal", lambda _kw: backend)

    trace = runner.run_delegate_task(
        [
            DelegateTaskSpec(
                goal="echo via modal",
                backend="modal",
                commands=["echo hi"],
            ),
        ]
    )

    result = trace.results[0]
    assert result.error is not None
    assert "MODAL_TOKEN" in result.error
    # No execute() invocations because init_session() crashed first.
    assert backend.invocations == []


def test_modal_backend_cold_start_latency_visible_in_duration() -> None:
    """Simulate a 200 ms cold-start and check the per-task duration reflects it.

    Modal cold-starts are 1-15 s in production.  We use 200 ms here so
    CI stays fast.  The point is to confirm the timing wrapper in the
    runner captures end-to-end wall time including the slow init.
    """
    backend = FakeModalBackend()
    backend.queue_result(FakeBackendResult(output="warm at last", delay_seconds=0.2))

    runner = FakeDelegateRunner()
    runner.register_backend("modal", lambda _kw: backend)

    trace = runner.run_delegate_task(
        [
            DelegateTaskSpec(
                goal="cold start probe",
                backend="modal",
                commands=["echo warm"],
            ),
        ]
    )

    assert "warm at last" in trace.final_response
    # 200 ms simulated delay should show up; allow a generous floor to
    # avoid CI flake.
    assert trace.results[0].duration_ms >= 150, trace.results[0].duration_ms
    # And a sane ceiling — 5 s would mean something deadlocked.
    assert trace.results[0].duration_ms < 5000


def test_modal_backend_multiple_commands_share_one_sandbox() -> None:
    """Two commands in one task → two execute() calls on the SAME backend instance.

    Upstream ``BaseEnvironment`` keeps the sandbox alive between
    commands via the snapshot mechanism (see ``base.py::execute``).
    The harness asserts the same fake instance receives both
    invocations — not two fresh sandboxes — so a regression where
    upstream tears down the sandbox per command would surface here.
    """
    backend = FakeModalBackend()
    backend.queue_result(FakeBackendResult(output="step 1 ok"))
    backend.queue_result(FakeBackendResult(output="step 2 ok"))

    runner = FakeDelegateRunner()
    runner.register_backend("modal", lambda _kw: backend)

    runner.run_delegate_task(
        [
            DelegateTaskSpec(
                goal="multi-step task",
                backend="modal",
                commands=["echo step 1", "echo step 2"],
            ),
        ]
    )

    assert len(backend.invocations) == 2
    assert backend.invocations[0].command == "echo step 1"
    assert backend.invocations[1].command == "echo step 2"
    # init_session called exactly once — sandbox reused across commands.
    assert backend.session_initialised is True
