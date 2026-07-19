"""Board executor dispatch seam (KB-5) — interface + registry, no-op default.

hal0 stays authoritative for board state; an OPTIONAL executor runs the work.
This module is the NARROW seam between the two, shaped per the integration
design (``docs/superpowers/specs/2026-07-18-hal0-hermes-integration-suite-design.md``
§"Kanban executor bridge"). It defines the contract only — there is NO Hermes
wiring here. A future ``hal0-hermes-executor`` registers a concrete
:class:`BoardExecutor`; until one is registered every dispatch is an honest
no-op.

Contract (design §"Kanban executor bridge"):

* hal0 dispatches ONE immutable hal0 attempt for a card to an external
  executor. The executor may inspect or cancel that run and reconcile after a
  disconnect, and it translates the executor's heartbeats / blocked states /
  structured handoffs / completion / failure into hal0 attempt events via a
  status **writeback**.
* The executor MAY report an outcome but MUST NOT directly change hal0
  dependencies, ownership, approval state, or canonical completion — those stay
  hal0's. The writeback only appends attempt events/runs; it never mutates the
  card's lane or deps on the executor's behalf.
* Every dispatch carries hal0 correlation (``card_id``, ``attempt_id``) plus the
  executor-side correlation the executor fills in (``board_id`` / ``task_id`` /
  ``run_id`` / ``session_id``). hal0 stores summaries + pointers; raw prompts,
  transcripts, credentials, and unrestricted tool output stay in the executor.

The route/store layer calls :func:`dispatch`; with an empty registry it returns
``DispatchResult(dispatched=False, reason="no executor…")`` and writes nothing,
which is exactly the "Hermes optional / absent" behaviour.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any, Protocol, runtime_checkable

#: A status-writeback sink. The executor (via :func:`dispatch`) hands hal0 an
#: updated :class:`AttemptHandle` on every state change; the sink appends the
#: corresponding hal0 attempt event/run. It never reshapes canonical board
#: state — that stays hal0's exclusive right (design §"Kanban executor bridge").
Writeback = Callable[["AttemptHandle"], None]


@dataclass(frozen=True)
class AttemptHandle:
    """One immutable dispatch attempt + its cross-system correlation.

    hal0 owns ``card_id`` / ``attempt_id``; the executor fills the ``executor``
    /``board_id``/``task_id``/``run_id``/``session_id`` correlation fields as it
    accepts and runs the work. ``status`` is the last observed executor state
    (``pending`` → ``running`` → ``blocked``/``done``/``failed``/``cancelled``).
    Frozen: state transitions produce a NEW handle via :func:`with_status`,
    never mutation, so a handle is always a faithful point-in-time record.
    """

    card_id: str
    attempt_id: str
    target: str
    executor: str | None = None
    board_id: str | None = None
    task_id: str | None = None
    run_id: str | None = None
    session_id: str | None = None
    status: str = "pending"
    detail: dict[str, Any] = field(default_factory=dict)

    def with_status(self, status: str, **correlation: Any) -> AttemptHandle:
        """Return a copy advanced to ``status`` with any new correlation ids."""
        return replace(self, status=status, **correlation)


@dataclass(frozen=True)
class DispatchResult:
    """Outcome of a :func:`dispatch` call."""

    dispatched: bool
    handle: AttemptHandle | None = None
    reason: str | None = None


@runtime_checkable
class BoardExecutor(Protocol):
    """The narrow executor interface a backend (e.g. Hermes) implements.

    Implementations MUST be side-effect-free on canonical hal0 state — they act
    only on their OWN run and report back through the returned handle (which the
    caller feeds to the writeback). None of these methods may change a card's
    lane, deps, ownership, or approval state.
    """

    def dispatch(self, card_id: str, *, context: dict[str, Any]) -> AttemptHandle:
        """Start one external attempt for ``card_id``; return its handle."""
        ...

    def inspect(self, handle: AttemptHandle) -> AttemptHandle:
        """Return the current state of a previously dispatched attempt."""
        ...

    def cancel(self, handle: AttemptHandle) -> AttemptHandle:
        """Cancel the external run; return the terminal handle."""
        ...

    def reconcile(self, handle: AttemptHandle) -> AttemptHandle:
        """Re-sync after a disconnect; return the reconciled handle."""
        ...


# ── registry ─────────────────────────────────────────────────────────────────
#
# Deliberately a plain module-level dict: a single process wires at most one
# executor per named target (e.g. "hermes"). Empty by default — the seam ships
# inert, and the board runs fully with it empty (Hermes optional).

_REGISTRY: dict[str, BoardExecutor] = {}


def register_executor(target: str, executor: BoardExecutor) -> None:
    """Register the executor that services ``target`` (idempotent overwrite)."""
    _REGISTRY[target] = executor


def get_executor(target: str) -> BoardExecutor | None:
    return _REGISTRY.get(target)


def clear_executors() -> None:
    """Drop all registered executors (test isolation / teardown)."""
    _REGISTRY.clear()


# ── entrypoint ───────────────────────────────────────────────────────────────


def dispatch(
    card_id: str,
    target: str,
    *,
    attempt_id: str | None = None,
    context: dict[str, Any] | None = None,
    writeback: Writeback | None = None,
) -> DispatchResult:
    """Dispatch one attempt for ``card_id`` to ``target``'s executor.

    Returns a :class:`DispatchResult`. With no executor registered for
    ``target`` (the default / Hermes-absent case) it dispatches NOTHING and
    reports the reason — the store's nudge then honestly counts zero. When an
    executor IS registered, its returned handle is passed to ``writeback`` (if
    given) so hal0 records the attempt as an event/run without the executor ever
    touching canonical board state.
    """
    executor = get_executor(target)
    if executor is None:
        return DispatchResult(
            dispatched=False, handle=None, reason=f"no executor registered for target {target!r}"
        )
    handle = executor.dispatch(card_id, context=context or {})
    if attempt_id is not None and handle.attempt_id != attempt_id:
        handle = replace(handle, attempt_id=attempt_id)
    if writeback is not None:
        writeback(handle)
    return DispatchResult(dispatched=True, handle=handle, reason=None)


class NoopExecutor:
    """Reference no-op executor: accepts a dispatch but does no external work.

    Not registered by default — provided as the minimal conforming
    implementation and for tests. Every call returns a terminal ``skipped``
    handle, so registering it makes :func:`dispatch` succeed while still doing
    nothing real.
    """

    target = "noop"

    def dispatch(self, card_id: str, *, context: dict[str, Any]) -> AttemptHandle:
        return AttemptHandle(
            card_id=card_id,
            attempt_id=f"noop-{card_id}",
            target=self.target,
            executor=self.target,
            status="skipped",
        )

    def inspect(self, handle: AttemptHandle) -> AttemptHandle:
        return handle

    def cancel(self, handle: AttemptHandle) -> AttemptHandle:
        return handle.with_status("cancelled")

    def reconcile(self, handle: AttemptHandle) -> AttemptHandle:
        return handle


__all__ = [
    "AttemptHandle",
    "BoardExecutor",
    "DispatchResult",
    "NoopExecutor",
    "Writeback",
    "clear_executors",
    "dispatch",
    "get_executor",
    "register_executor",
]
