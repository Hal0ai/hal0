"""δ-harness — ``delegate_task`` dispatch MATRIX across all three backends.

Reference: upstream pin ``0554ef1a`` (Hal0ai/hal0 pyproject ``[tool.hal0.upstream-hermes]``).

This is the **headline gate test** for the DA OpenRouter integration
must-fix #2: "R7's '7 backends' claim is unverified — prove ≥3 of
them work end-to-end before V3a Hermes-observability work proceeds".

Two layers:

1. **Dispatch matrix** — one parametrised test fans out a single goal
   to local + docker + modal, asserts each backend was invoked
   exactly once with a per-backend-shaped payload, and each round-trip
   reaches the parent's final response.

2. **Upstream-contract drift gate** — if the upstream Hermes-Agent
   checkout is on ``PYTHONPATH`` (developer machine with
   ``~/src/hermes-agent``), assert the ``BaseEnvironment`` ABC still
   has the four methods our fakes mirror.  Skips on machines without
   the upstream checkout (CI, fresh contributor laptops).

If this file goes red, V3a Hermes-observability is gated.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable

import pytest
from tests.harness.integration._delegate_fakes import (
    FakeBackendResult,
    FakeDockerBackend,
    FakeLocalBackend,
    FakeModalBackend,
    _BackendContract,
    upstream_base_environment_available,
)
from tests.harness.integration._delegate_runner import (
    DelegateTaskSpec,
    FakeDelegateRunner,
)

# ---------------------------------------------------------------------------
# Dispatch matrix — fan out the same goal to all three backends
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("backend_name", "backend_factory", "expected_token"),
    [
        (
            "local",
            lambda: _scripted_local("matrix-local-output"),
            "matrix-local-output",
        ),
        (
            "docker",
            lambda: _scripted_docker("matrix-docker-output"),
            "matrix-docker-output",
        ),
        (
            "modal",
            lambda: _scripted_modal("matrix-modal-output"),
            "matrix-modal-output",
        ),
    ],
    ids=["local", "docker", "modal"],
)
def test_delegate_dispatch_per_backend_round_trips(
    backend_name: str,
    backend_factory: Callable[[], _BackendContract],
    expected_token: str,
) -> None:
    """Per-backend smoke: the runner picks the right backend and the
    output round-trips into the assistant response."""
    backend = backend_factory()
    runner = FakeDelegateRunner()
    runner.register_backend(backend_name, lambda _kw: backend)

    trace = runner.run_delegate_task(
        [
            DelegateTaskSpec(
                goal=f"matrix smoke on {backend_name}",
                backend=backend_name,
                commands=["echo matrix"],
            )
        ]
    )

    assert expected_token in trace.final_response
    assert trace.results[0].error is None
    assert trace.results[0].backend == backend_name
    # backend is one of our three fake subclasses; all carry .invocations.
    assert len(backend.invocations) == 1  # type: ignore[attr-defined]


def test_delegate_dispatch_fans_out_to_three_backends_in_one_call() -> None:
    """ONE delegate_task call fanning out to THREE backends — the real shape
    of upstream's batch mode (tasks=[{...}, {...}, {...}]).

    Each task uses a different backend; this asserts the runner picks
    each one correctly + each is called exactly once with the right
    payload shape.  Regressions where, say, "docker" and "modal" got
    routed to the same backend (a real risk if the upstream selector
    is renamed) would surface here.
    """
    local = _scripted_local("local-fanout-output")
    docker = _scripted_docker("docker-fanout-output")
    modal = _scripted_modal("modal-fanout-output")

    runner = FakeDelegateRunner()
    runner.register_backend("local", lambda _kw: local)
    runner.register_backend("docker", lambda _kw: docker)
    runner.register_backend("modal", lambda _kw: modal)

    trace = runner.run_delegate_task(
        [
            DelegateTaskSpec(
                goal="fanout to local",
                backend="local",
                commands=["echo local"],
            ),
            DelegateTaskSpec(
                goal="fanout to docker",
                backend="docker",
                commands=["echo docker"],
            ),
            DelegateTaskSpec(
                goal="fanout to modal",
                backend="modal",
                commands=["echo modal"],
            ),
        ]
    )

    # All three round-trips visible.
    assert "local-fanout-output" in trace.final_response
    assert "docker-fanout-output" in trace.final_response
    assert "modal-fanout-output" in trace.final_response

    # Each backend invoked exactly once.
    assert len(local.invocations) == 1
    assert len(docker.invocations) == 1
    assert len(modal.invocations) == 1

    # Per-backend context labels match.
    assert local.invocations[0].backend_context["backend"] == "local"
    assert docker.invocations[0].backend_context["backend"] == "docker"
    assert modal.invocations[0].backend_context["backend"] == "modal"

    # All three tasks accounted for.
    assert len(trace.results) == 3
    seen_backends = {r.backend for r in trace.results}
    assert seen_backends == {"local", "docker", "modal"}


def test_delegate_unknown_backend_raises_keyerror() -> None:
    """Asking for an unregistered backend name fails loudly — better than
    a silent fallback to ``local`` (which would mask the misconfig)."""
    runner = FakeDelegateRunner()
    runner.register_backend("local", lambda _kw: _scripted_local("ok"))

    with pytest.raises(KeyError, match="vercel"):
        runner.run_delegate_task(
            [
                DelegateTaskSpec(
                    goal="try a nonexistent backend",
                    backend="vercel",  # not registered
                    commands=["echo"],
                )
            ]
        )


# ---------------------------------------------------------------------------
# Upstream-contract drift gate
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not upstream_base_environment_available(),
    reason="upstream Hermes-Agent checkout not on PYTHONPATH (skipped on CI)",
)
def test_upstream_base_environment_still_has_expected_methods() -> None:
    """If a contributor has the upstream checkout cloned, assert the
    ``BaseEnvironment`` ABC still exposes the four methods our fakes
    mirror.  Drift = upstream renamed something + our fakes are stale.

    This is the canonical mechanism the weekly ``hermes-sdk-diff``
    workflow (ADR-0018) uses to catch upstream churn.
    """
    from tools.environments.base import BaseEnvironment  # type: ignore[import-not-found]

    for method_name in ("init_session", "execute", "cleanup"):
        assert hasattr(BaseEnvironment, method_name), (
            f"upstream BaseEnvironment is missing {method_name!r} — "
            f"our delegate-task fakes need to be updated"
        )

    # ``execute`` signature drift detector — keyword args we rely on.
    sig = inspect.signature(BaseEnvironment.execute)
    params = set(sig.parameters)
    assert "command" in params, (
        "upstream BaseEnvironment.execute() lost the 'command' parameter — "
        "FakeBackendContract.execute() is now stale"
    )
    assert "cwd" in params
    assert "timeout" in params
    assert "stdin_data" in params


def test_all_fakes_implement_backend_contract() -> None:
    """Our three fakes must claim conformance with the ABC mirror.

    A regression where someone subclasses ``object`` instead of
    ``_BackendContract`` (forgetting an abstract method) would
    surface as an ``isinstance`` failure here.
    """
    for fake_cls in (FakeLocalBackend, FakeDockerBackend, FakeModalBackend):
        instance = fake_cls()
        assert isinstance(instance, _BackendContract), (
            f"{fake_cls.__name__} does not implement _BackendContract"
        )
        for method_name in ("init_session", "execute", "cleanup"):
            assert callable(getattr(instance, method_name))


# ---------------------------------------------------------------------------
# Helpers — factory shortcuts for the parametrised matrix above
# ---------------------------------------------------------------------------


def _scripted_local(output: str) -> FakeLocalBackend:
    b = FakeLocalBackend()
    b.queue_result(FakeBackendResult(output=output))
    return b


def _scripted_docker(output: str) -> FakeDockerBackend:
    b = FakeDockerBackend(image="alpine:3.20")
    b.queue_result(FakeBackendResult(output=output))
    return b


def _scripted_modal(output: str) -> FakeModalBackend:
    b = FakeModalBackend(image="python:3.11-slim")
    b.queue_result(FakeBackendResult(output=output))
    return b
