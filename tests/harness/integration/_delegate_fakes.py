"""Fake execution-environment backends for δ-harness delegate_task coverage.

Background — why this file exists
---------------------------------
Upstream Hermes-Agent's ``delegate_task`` tool spawns one or more
child ``AIAgent`` threads.  Each child runs its own tool loop, and
when a child's tool loop calls ``terminal_tool``/``code_execution``,
those shell commands are dispatched through one of upstream's
**execution-environment backends** declared in
``tools/environments/`` and selected by the ``TERMINAL_ENV`` env var
(see ``tools/terminal_tool.py::_create_environment``).

The 7-backend claim R7 of the OpenRouter research catalogued in
``openrouter-research-2026-05-28/notes/r7-compete.md`` says hal0
"already ships" Hermes's 7 spawn backends via ``delegate_task``.  The
DA must-fix #2 demanded δ-harness coverage of ≥3 of those backends
before V3a Hermes-observability work could proceed.

What this file mocks
--------------------
Upstream's actual ABC is ``tools.environments.base.BaseEnvironment``
(see ``~/src/hermes-agent/tools/environments/base.py``, pin
``0554ef1a``).  Concrete implementations live in:

* ``tools/environments/local.py``    — ``LocalEnvironment``
* ``tools/environments/docker.py``   — ``DockerEnvironment``
* ``tools/environments/modal.py``    — ``ModalEnvironment``
* (plus singularity / ssh / daytona / managed_modal — out of scope)

The public surface every backend exposes is:

* ``__init__(cwd, timeout, env=None, ...)``  (per-backend extra kwargs)
* ``init_session() -> None``
* ``execute(command, cwd="", *, timeout=None, stdin_data=None) -> dict``
  → ``{"output": str, "returncode": int}``
* ``cleanup() -> None``

That's the contract our fakes mirror.

Why we vendor the ABC instead of importing it
---------------------------------------------
hal0 does **not** vendor upstream Hermes-Agent into its repo (ADR-0018:
hal0 v0.3 shims against a pinned upstream commit but doesn't carry
``tools/`` in tree).  Tests that ``from tools.environments.base import
BaseEnvironment`` would only run on machines where the upstream
checkout is on ``PYTHONPATH``.  Instead we declare the contract here
(``_BackendContract``) and assert each fake satisfies it via runtime
``isinstance``.  A separate signature-snapshot test
(``test_delegate_task_dispatch_matrix.py::test_signature_snapshot_*``)
gates drift against the pinned commit when the upstream checkout is
available.
"""

from __future__ import annotations

import abc
import time
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Contract — mirrors upstream tools/environments/base.py::BaseEnvironment
# ---------------------------------------------------------------------------


class _BackendContract(abc.ABC):
    """The public shape every Hermes execution backend exposes.

    Kept deliberately minimal (only the four methods delegate_task's
    spawned children actually call).  See module docstring for the full
    contract + the upstream reference.
    """

    @abc.abstractmethod
    def init_session(self) -> None: ...

    @abc.abstractmethod
    def execute(
        self,
        command: str,
        cwd: str = "",
        *,
        timeout: int | None = None,
        stdin_data: str | None = None,
    ) -> dict[str, Any]: ...

    @abc.abstractmethod
    def cleanup(self) -> None: ...


# ---------------------------------------------------------------------------
# Invocation trace — what every fake records for assertions
# ---------------------------------------------------------------------------


@dataclass
class BackendInvocation:
    """One ``execute()`` call captured for assertions."""

    command: str
    cwd: str
    timeout: int | None
    stdin_data: str | None
    # Backend-specific context (image, working dir, function name, etc.)
    backend_context: dict[str, Any] = field(default_factory=dict)


@dataclass
class FakeBackendResult:
    """The ``execute()`` return value as scripted by the test."""

    output: str = ""
    returncode: int = 0
    # If set, ``execute()`` raises this instead of returning a result.
    raises: BaseException | None = None
    # If > 0, ``execute()`` blocks this long before returning (simulates
    # cold-start latency for Modal etc.).
    delay_seconds: float = 0.0


# ---------------------------------------------------------------------------
# Local backend fake
# ---------------------------------------------------------------------------


class FakeLocalBackend(_BackendContract):
    """In-process stand-in for ``LocalEnvironment``.

    Captures every ``execute()`` invocation and returns whatever
    ``next_result`` (or the queue) scripts.  No real subprocess.
    """

    BACKEND_NAME = "local"

    def __init__(
        self,
        cwd: str = "/tmp",
        timeout: int = 120,
        env: dict[str, str] | None = None,
    ) -> None:
        self.cwd = cwd
        self.timeout = timeout
        self.env = env or {}
        self.invocations: list[BackendInvocation] = []
        self.session_initialised = False
        self.cleanup_called = False
        self.results_queue: list[FakeBackendResult] = []
        self.default_result = FakeBackendResult(output="", returncode=0)

    def queue_result(self, result: FakeBackendResult) -> None:
        self.results_queue.append(result)

    def init_session(self) -> None:
        self.session_initialised = True

    def execute(
        self,
        command: str,
        cwd: str = "",
        *,
        timeout: int | None = None,
        stdin_data: str | None = None,
    ) -> dict[str, Any]:
        self.invocations.append(
            BackendInvocation(
                command=command,
                cwd=cwd or self.cwd,
                timeout=timeout,
                stdin_data=stdin_data,
                backend_context={"backend": self.BACKEND_NAME, "env": dict(self.env)},
            )
        )
        result = self.results_queue.pop(0) if self.results_queue else self.default_result
        if result.delay_seconds > 0:
            time.sleep(result.delay_seconds)
        if result.raises is not None:
            raise result.raises
        return {"output": result.output, "returncode": result.returncode}

    def cleanup(self) -> None:
        self.cleanup_called = True


# ---------------------------------------------------------------------------
# Docker backend fake
# ---------------------------------------------------------------------------


class FakeDockerBackend(_BackendContract):
    """Stand-in for ``DockerEnvironment``.

    Captures the image + container kwargs alongside the ``execute()``
    call so tests can assert the right image was selected.
    """

    BACKEND_NAME = "docker"

    def __init__(
        self,
        image: str = "alpine:3.20",
        cwd: str = "/workspace",
        timeout: int = 120,
        env: dict[str, str] | None = None,
        cpu: int = 1,
        memory: int = 5120,
        disk: int = 51200,
        volumes: list[str] | None = None,
        # If True, simulate "docker not available" — raise on init_session.
        unavailable: bool = False,
    ) -> None:
        self.image = image
        self.cwd = cwd
        self.timeout = timeout
        self.env = env or {}
        self.cpu = cpu
        self.memory = memory
        self.disk = disk
        self.volumes = volumes or []
        self.unavailable = unavailable
        self.invocations: list[BackendInvocation] = []
        self.session_initialised = False
        self.cleanup_called = False
        self.results_queue: list[FakeBackendResult] = []
        self.default_result = FakeBackendResult(output="", returncode=0)

    def queue_result(self, result: FakeBackendResult) -> None:
        self.results_queue.append(result)

    def init_session(self) -> None:
        if self.unavailable:
            raise RuntimeError("FakeDockerBackend simulated: docker daemon not reachable")
        self.session_initialised = True

    def execute(
        self,
        command: str,
        cwd: str = "",
        *,
        timeout: int | None = None,
        stdin_data: str | None = None,
    ) -> dict[str, Any]:
        if not self.session_initialised:
            raise RuntimeError(
                "FakeDockerBackend.execute() before init_session() — "
                "the upstream BaseEnvironment.execute() flow requires "
                "init_session() to be called first"
            )
        self.invocations.append(
            BackendInvocation(
                command=command,
                cwd=cwd or self.cwd,
                timeout=timeout,
                stdin_data=stdin_data,
                backend_context={
                    "backend": self.BACKEND_NAME,
                    "image": self.image,
                    "cpu": self.cpu,
                    "memory": self.memory,
                    "disk": self.disk,
                    "volumes": list(self.volumes),
                    "env": dict(self.env),
                },
            )
        )
        result = self.results_queue.pop(0) if self.results_queue else self.default_result
        if result.delay_seconds > 0:
            time.sleep(result.delay_seconds)
        if result.raises is not None:
            raise result.raises
        return {"output": result.output, "returncode": result.returncode}

    def cleanup(self) -> None:
        self.cleanup_called = True


# ---------------------------------------------------------------------------
# Modal backend fake
# ---------------------------------------------------------------------------


class FakeModalBackend(_BackendContract):
    """Stand-in for ``ModalEnvironment``.

    Modal is the closest analog to "remote sandbox" — Strix Halo
    inference is not Modal's market, but R7 cited Modal as one of the 7
    spawn targets and DA's must-fix #2 asked for it specifically.

    Captures the Modal-specific sandbox kwargs (cpu/memory/ephemeral
    disk) so tests can assert provisioning intent.  Simulates the
    "API key missing" failure mode by raising on ``init_session()``.
    """

    BACKEND_NAME = "modal"

    def __init__(
        self,
        image: str = "python:3.11-slim",
        cwd: str = "/workspace",
        timeout: int = 300,
        env: dict[str, str] | None = None,
        sandbox_kwargs: dict[str, Any] | None = None,
        # If True, simulate Modal token missing — raise on init_session.
        token_missing: bool = False,
    ) -> None:
        self.image = image
        self.cwd = cwd
        self.timeout = timeout
        self.env = env or {}
        self.sandbox_kwargs = sandbox_kwargs or {"cpu": 1, "memory": 5120}
        self.token_missing = token_missing
        self.invocations: list[BackendInvocation] = []
        self.session_initialised = False
        self.cleanup_called = False
        self.results_queue: list[FakeBackendResult] = []
        self.default_result = FakeBackendResult(output="", returncode=0)

    def queue_result(self, result: FakeBackendResult) -> None:
        self.results_queue.append(result)

    def init_session(self) -> None:
        if self.token_missing:
            raise RuntimeError(
                "FakeModalBackend simulated: MODAL_TOKEN_ID / MODAL_TOKEN_SECRET not set"
            )
        self.session_initialised = True

    def execute(
        self,
        command: str,
        cwd: str = "",
        *,
        timeout: int | None = None,
        stdin_data: str | None = None,
    ) -> dict[str, Any]:
        if not self.session_initialised:
            raise RuntimeError(
                "FakeModalBackend.execute() before init_session() — "
                "Modal sandboxes need a successful auth handshake first"
            )
        self.invocations.append(
            BackendInvocation(
                command=command,
                cwd=cwd or self.cwd,
                timeout=timeout,
                stdin_data=stdin_data,
                backend_context={
                    "backend": self.BACKEND_NAME,
                    "image": self.image,
                    "sandbox_kwargs": dict(self.sandbox_kwargs),
                    "env": dict(self.env),
                },
            )
        )
        result = self.results_queue.pop(0) if self.results_queue else self.default_result
        if result.delay_seconds > 0:
            # Simulate Modal cold-start latency.
            time.sleep(result.delay_seconds)
        if result.raises is not None:
            raise result.raises
        return {"output": result.output, "returncode": result.returncode}

    def cleanup(self) -> None:
        self.cleanup_called = True


# ---------------------------------------------------------------------------
# Optional upstream-contract drift gate
# ---------------------------------------------------------------------------


def upstream_base_environment_available() -> bool:
    """Return True if the upstream Hermes checkout is on PYTHONPATH.

    Used by the matrix-test signature snapshot to skip cleanly on
    machines where upstream isn't cloned (CI, contributor laptops
    without ``~/src/hermes-agent``).
    """
    try:
        # The upstream pin maintains the same module path; this import
        # is the canonical drift detector.
        import tools.environments.base  # type: ignore[import-not-found]  # noqa: F401

        return True
    except ImportError:
        return False
