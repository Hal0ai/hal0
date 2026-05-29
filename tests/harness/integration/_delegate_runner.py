"""In-process orchestration harness for δ-tier ``delegate_task`` tests.

Why a custom harness instead of running real Hermes
---------------------------------------------------
Running upstream ``AIAgent`` end-to-end inside CI would mean booting a
full conversation loop, an LLM provider, MCP transports, and the
tool registry just to prove the spawn → backend dispatch hop works.
That is the gamma-tier suite's job (live LXC + real model).  The δ-tier
question is much smaller: *given a delegate_task call with a chosen
execution-environment backend, does Hermes's spawn-and-route logic
hand the child's commands to the right backend with the right
payload?*

This harness simulates only that hop.  The simulation is intentionally
close to upstream's actual flow:

  parent agent → delegate_task(goal, tasks=[{goal, env_kwargs}, ...])
              → for each task: build a FakeChildAgent
              → child runs a scripted "tool call" sequence
              → child's terminal/code tool calls go through the
                injected backend
              → child returns a JSON result envelope identical to the
                upstream ``delegate_task`` return shape
              → parent assembles the final assistant response

The result envelope matches what
``tools/delegate_tool.py::delegate_task`` returns: a JSON string with
``{"results": [{"task_id": ..., "goal": ..., "output": ...,
"error": ..., "duration_ms": ...}, ...]}`` (verified against
upstream pin ``0554ef1a``).

Tests assert:
* the right backend was constructed (via the factory callback)
* the backend received the expected ``execute()`` payload
* the backend's output round-trips into the assembled response
* errors raised by the backend show up in the per-task ``error`` slot
  rather than crashing the dispatch
"""

from __future__ import annotations

import contextlib
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from tests.harness.integration._delegate_fakes import _BackendContract

# ---------------------------------------------------------------------------
# Per-task spec — what the parent agent emits in its delegate_task call
# ---------------------------------------------------------------------------


@dataclass
class DelegateTaskSpec:
    """One task entry the parent passes to delegate_task.

    Mirrors the upstream shape ``{"goal": str, "context": str?,
    "toolsets": [str]?, ...}`` plus a hal0-internal
    ``backend_kwargs`` blob the test uses to script the child's
    terminal-tool execution.
    """

    goal: str
    backend: str  # "local" | "docker" | "modal"
    # Commands the child's tool loop will run inside its execution backend.
    # The harness invokes backend.execute(cmd) once per entry.
    commands: list[str] = field(default_factory=list)
    # Per-task overrides passed to the backend factory (image, sandbox_kwargs).
    backend_kwargs: dict[str, Any] = field(default_factory=dict)
    context: str | None = None
    toolsets: list[str] | None = None
    role: str = "leaf"


# ---------------------------------------------------------------------------
# Backend factory — one per backend name; tests register fakes via this
# ---------------------------------------------------------------------------


BackendFactory = Callable[[dict[str, Any]], _BackendContract]


# ---------------------------------------------------------------------------
# Result envelopes — mirror upstream's delegate_task return shape
# ---------------------------------------------------------------------------


@dataclass
class TaskResult:
    """One row in delegate_task's results array."""

    task_id: str
    goal: str
    output: str
    error: str | None
    duration_ms: int
    backend: str


@dataclass
class DelegateTrace:
    """Everything the harness recorded for a single delegate_task call.

    The runner returns this so tests can assert on:
    * the final "assistant response" string the parent assembled
    * the per-backend invocation list (count + payloads)
    * the per-task results array (output + error envelopes)
    """

    final_response: str
    results: list[TaskResult]
    backends_used: dict[str, _BackendContract]
    raw_envelope_json: str


# ---------------------------------------------------------------------------
# The harness itself
# ---------------------------------------------------------------------------


class FakeDelegateRunner:
    """Simulated delegate_task dispatcher.

    Usage:

        runner = FakeDelegateRunner()
        runner.register_backend("local", lambda kw: FakeLocalBackend(**kw))
        runner.register_backend("docker", lambda kw: FakeDockerBackend(**kw))

        trace = runner.run_delegate_task([
            DelegateTaskSpec(goal="echo hi", backend="local",
                             commands=["echo hi"]),
        ])
        assert "hi" in trace.final_response
        assert len(trace.backends_used["local"].invocations) == 1
    """

    def __init__(self) -> None:
        self._factories: dict[str, BackendFactory] = {}

    def register_backend(self, name: str, factory: BackendFactory) -> None:
        self._factories[name] = factory

    # ------------------------------------------------------------------
    # Top-level entry — what the parent agent's tool_executor calls
    # ------------------------------------------------------------------

    def run_delegate_task(
        self,
        tasks: list[DelegateTaskSpec],
    ) -> DelegateTrace:
        """Execute one delegate_task call covering ``tasks``.

        Returns the trace + the assembled final response.
        """
        # Input validation mirrors upstream tools/delegate_tool.py:2008-2035.
        if not tasks:
            raise ValueError("delegate_task: no tasks provided")
        for i, t in enumerate(tasks):
            if not t.goal.strip():
                raise ValueError(f"delegate_task: task {i} has empty goal")

        results: list[TaskResult] = []
        backends_used: dict[str, _BackendContract] = {}

        for t in tasks:
            backend = self._build_backend(t)
            backends_used[t.backend] = backend

            task_id = f"task-{uuid.uuid4().hex[:8]}"
            start = time.monotonic()
            output_buf: list[str] = []
            error: str | None = None

            try:
                backend.init_session()
                for cmd in t.commands:
                    res = backend.execute(cmd, cwd="")
                    output_buf.append(res.get("output", ""))
                    if res.get("returncode", 0) != 0 and error is None:
                        # First non-zero stays as the surfaced error
                        error = f"command exited {res['returncode']}: {cmd!r}"
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            finally:
                # Cleanup is best-effort; backend teardown failures should
                # not mask the original task error.
                with contextlib.suppress(Exception):  # pragma: no cover
                    backend.cleanup()

            duration_ms = int((time.monotonic() - start) * 1000)
            results.append(
                TaskResult(
                    task_id=task_id,
                    goal=t.goal,
                    output="\n".join(output_buf),
                    error=error,
                    duration_ms=duration_ms,
                    backend=t.backend,
                )
            )

        envelope = self._assemble_envelope(results)
        final = self._assemble_final_response(results)
        return DelegateTrace(
            final_response=final,
            results=results,
            backends_used=backends_used,
            raw_envelope_json=envelope,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_backend(self, spec: DelegateTaskSpec) -> _BackendContract:
        if spec.backend not in self._factories:
            raise KeyError(
                f"backend {spec.backend!r} not registered. Available: {sorted(self._factories)}"
            )
        return self._factories[spec.backend](spec.backend_kwargs)

    @staticmethod
    def _assemble_envelope(results: list[TaskResult]) -> str:
        """Serialise the per-task results in upstream's envelope shape.

        Reference: upstream's ``delegate_task`` returns
        ``json.dumps({"results": [...]})``.  We mirror that exactly so
        the assertion shape matches what a real Hermes parent agent
        would see in its tool-call return value.
        """
        return json.dumps(
            {
                "results": [
                    {
                        "task_id": r.task_id,
                        "goal": r.goal,
                        "output": r.output,
                        "error": r.error,
                        "duration_ms": r.duration_ms,
                        "backend": r.backend,
                    }
                    for r in results
                ]
            }
        )

    @staticmethod
    def _assemble_final_response(results: list[TaskResult]) -> str:
        """Compose the assistant message the parent would emit after the
        delegate_task tool returns.

        Mirrors what a sane parent agent does: stitch successful outputs
        with newline separators, surface errors inline.  Not bit-exact
        with upstream (the LLM writes that text); shape good enough for
        the δ-tier assertion "the child's output reached the user".
        """
        lines: list[str] = []
        for r in results:
            if r.error:
                lines.append(f"[{r.goal}] error: {r.error}")
            else:
                lines.append(f"[{r.goal}] {r.output}")
        return "\n".join(lines)
