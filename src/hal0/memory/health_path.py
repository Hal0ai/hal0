"""The Memory health path: which step needs the operator first (ADR-0058, decision 12).

Health is an ordered repair path, not a list of alarms. The steps form one
dependency chain::

    engine -> extraction -> queue -> fold-in | embedder -> knowledge

and a step that is not fine is *open*. The first open step is ``current``:
it is the cause to deal with, and the one place an action is offered. A later
open step is ``waiting``, and names the nearest open step before it, because
acting on it first would fail or be undone (retrying a queue while extraction
is still failing, refreshing knowledge pages over a store that is about to be
re-embedded). Steps that are fine are ``clear``.

Fold-in and the embedder share a position. While banks are still waiting to
be folded into the two-bank layout the fold-in step stands there, because an
import re-embeds every fact and so repairs the embedder on the way; once none
are left the embedder step does. No backend reports such banks yet, so
:attr:`HealthFacts.fold_in_banks` stays ``0`` until one does.

Pure: no I/O, no clock, no copy. Callers hand in facts and get back states,
reason codes and the figures behind them; the dashboard writes the sentences.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from hal0.memory.embedder_verdict import embedder_verdict

#: How far back a failed retain counts against the extraction step. Older
#: failures are queue work (retry them), not evidence that extraction is
#: failing now.
EXTRACTION_FAILURE_WINDOW_S = 6 * 60 * 60

#: A waiting operation older than this opens the queue step.
QUEUE_WAITING_MAX_AGE_S = 30 * 60

VERDICT_CLEAR = "clear"
VERDICT_ATTENTION = "attention"
VERDICT_UNREACHABLE = "unreachable"

STATE_CURRENT = "current"
STATE_WAITING = "waiting"
STATE_CLEAR = "clear"

STEP_ENGINE = "engine"
STEP_EXTRACTION = "extraction"
STEP_QUEUE = "queue"
STEP_FOLD_IN = "fold_in"
STEP_EMBEDDER = "embedder"
STEP_KNOWLEDGE = "knowledge"

REASON_ENGINE_UNREACHABLE = "engine_unreachable"
REASON_SLOT_UNRESOLVED = "extraction_slot_unresolved"
REASON_LLM_UNREACHABLE = "extraction_llm_unreachable"
REASON_RECENT_FAILURES = "extraction_recent_failures"
REASON_OPERATIONS_FAILED = "queue_operations_failed"
REASON_WAITING_TOO_LONG = "queue_waiting_too_long"
REASON_FOLD_IN_PENDING = "fold_in_pending"
#: The embedder step's code is this prefix plus the first
#: :func:`hal0.memory.embedder_verdict.embedder_verdict` reason.
REASON_EMBEDDER_PREFIX = "embedder_"
REASON_REFRESH_FAILED = "knowledge_refresh_failed"
REASON_STALE = "knowledge_stale"


@dataclass(frozen=True)
class HealthFacts:
    """What the path is decided from. ``None`` means not known, and never opens a step."""

    engine_reachable: bool
    #: Does the extraction slot have a model? ``None`` when no slot is in play.
    slot_resolves: bool | None = None
    llm_reachable: bool | None = None
    #: Failed retains inside :data:`EXTRACTION_FAILURE_WINDOW_S`.
    extraction_failures: int = 0
    #: ``[{"message", "count"}]``, already normalised and capped by the collector.
    extraction_top_errors: tuple[Mapping[str, Any], ...] = ()
    operations_failed: int = 0
    operations_waiting: int = 0
    oldest_waiting_s: float | None = None
    #: An ``embedder_status`` payload.
    embedder: Mapping[str, Any] | None = None
    #: Banks still to be folded into the two-bank layout. Above zero, the
    #: fold-in step takes the embedder step's place.
    fold_in_banks: int = 0
    pages_stale: int = 0
    models_stale: int = 0
    refresh_failed: int = 0


@dataclass(frozen=True)
class Step:
    id: str
    state: str
    waits_on: str | None
    reason_code: str | None
    figures: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "state": self.state,
            "waits_on": self.waits_on,
            "reason_code": self.reason_code,
            "figures": self.figures,
        }


@dataclass(frozen=True)
class HealthPath:
    verdict: str
    steps: tuple[Step, ...]


#: One evaluated step before its place in the chain is known:
#: ``(id, reason code or None when fine, figures)``.
_Check = tuple[str, str | None, dict[str, Any]]


def _extraction(facts: HealthFacts) -> _Check:
    reason: str | None = None
    if facts.slot_resolves is False:
        reason = REASON_SLOT_UNRESOLVED
    elif facts.llm_reachable is False:
        reason = REASON_LLM_UNREACHABLE
    elif facts.extraction_failures > 0:
        reason = REASON_RECENT_FAILURES
    figures = {
        "slot_resolves": facts.slot_resolves,
        "llm_reachable": facts.llm_reachable,
        "failures": facts.extraction_failures,
        "window_s": EXTRACTION_FAILURE_WINDOW_S,
        "top_errors": [dict(row) for row in facts.extraction_top_errors],
    }
    return STEP_EXTRACTION, reason, figures


def _queue(facts: HealthFacts) -> _Check:
    oldest = facts.oldest_waiting_s
    reason: str | None = None
    if facts.operations_failed > 0:
        reason = REASON_OPERATIONS_FAILED
    elif oldest is not None and oldest > QUEUE_WAITING_MAX_AGE_S:
        reason = REASON_WAITING_TOO_LONG
    figures = {
        "failed": facts.operations_failed,
        "waiting": facts.operations_waiting,
        "oldest_waiting_s": oldest,
        "max_waiting_age_s": QUEUE_WAITING_MAX_AGE_S,
    }
    return STEP_QUEUE, reason, figures


def _fold_in(facts: HealthFacts) -> _Check:
    return STEP_FOLD_IN, REASON_FOLD_IN_PENDING, {"banks": facts.fold_in_banks}


def _embedder(facts: HealthFacts) -> _Check:
    status = facts.embedder
    if status is None:
        return STEP_EMBEDDER, None, {}
    verdict = embedder_verdict(status)
    reason = REASON_EMBEDDER_PREFIX + verdict.reasons[0] if verdict.reasons else None
    figures = {
        "recorded": {
            "provider": status.get("recorded_provider"),
            "model": status.get("recorded"),
        },
        "configured": {
            "provider": status.get("configured_provider"),
            "model": status.get("configured"),
        },
        "mixed": bool(status.get("mixed")),
        "conflict": status.get("conflict"),
        "reasons": list(verdict.reasons),
        "informational": verdict.informational,
    }
    return STEP_EMBEDDER, reason, figures


def _knowledge(facts: HealthFacts) -> _Check:
    reason: str | None = None
    if facts.refresh_failed > 0:
        reason = REASON_REFRESH_FAILED
    elif facts.pages_stale > 0 or facts.models_stale > 0:
        reason = REASON_STALE
    figures = {
        "pages_stale": facts.pages_stale,
        "models_stale": facts.models_stale,
        "refresh_failed": facts.refresh_failed,
    }
    return STEP_KNOWLEDGE, reason, figures


def health_path(facts: HealthFacts) -> HealthPath:
    """Order the steps for ``facts``.

    An unreachable engine short-circuits: nothing behind it can be read, so the
    path is the engine step alone and the verdict is ``unreachable``.
    """
    if not facts.engine_reachable:
        engine = Step(
            STEP_ENGINE, STATE_CURRENT, None, REASON_ENGINE_UNREACHABLE, {"reachable": False}
        )
        return HealthPath(VERDICT_UNREACHABLE, (engine,))

    checks: list[_Check] = [
        (STEP_ENGINE, None, {"reachable": True}),
        _extraction(facts),
        _queue(facts),
        _fold_in(facts) if facts.fold_in_banks > 0 else _embedder(facts),
        _knowledge(facts),
    ]
    steps: list[Step] = []
    blocker: str | None = None  # the nearest open step so far
    for step_id, reason, figures in checks:
        if reason is None:
            steps.append(Step(step_id, STATE_CLEAR, None, None, figures))
            continue
        state = STATE_CURRENT if blocker is None else STATE_WAITING
        steps.append(Step(step_id, state, blocker, reason, figures))
        blocker = step_id
    verdict = VERDICT_CLEAR if blocker is None else VERDICT_ATTENTION
    return HealthPath(verdict, tuple(steps))


__all__ = [
    "EXTRACTION_FAILURE_WINDOW_S",
    "QUEUE_WAITING_MAX_AGE_S",
    "HealthFacts",
    "HealthPath",
    "Step",
    "health_path",
]
