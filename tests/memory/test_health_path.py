"""The Memory health path as an ordering table (ADR-0058, decision 12).

``hal0.memory.health_path`` is pure, so every case here is facts in, steps
out. The table lists each combination of open steps by hand: an expectation
computed from the same rule the module applies would prove nothing.

Run targeted:
    .venv/bin/python -m pytest tests/memory/test_health_path.py -q
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from hal0.cli import doctor_all as da
from hal0.memory import health_path as hp
from hal0.memory.embedder_verdict import embedder_verdict
from hal0.memory.health_path import HealthFacts, health_path

_BGE = "BAAI/bge-small-en-v1.5"  # the engine default
_HS = "hindsight-ai/hs-embed-1"  # the shipped [memory.models] default
_OTHER = "example-org/another-embedder"


def _embedder(recorded: str, configured: str, *, mixed: bool = False, **extra: Any) -> dict:
    """An ``embedder_status`` payload (the shape the doctor tests build)."""
    status: dict[str, Any] = {
        "recorded": recorded,
        "recorded_provider": "local",
        "configured": configured,
        "configured_provider": "local",
        "mixed": mixed,
        "source": "engine-default",
        "recorded_at": "2026-11-10T21:00:00+00:00",
        "effective": None,
        "conflict": None,
        "record_error": None,
        "store_unavailable": None,
        **extra,
    }
    status["needs_switch"] = mixed or (status["recorded_provider"], recorded) != (
        status["configured_provider"],
        configured,
    )
    return status


#: Facts on which every step is fine.
_FINE = HealthFacts(
    engine_reachable=True,
    slot_resolves=True,
    llm_reachable=True,
    operations_waiting=4,
    oldest_waiting_s=20.0,
    embedder=_embedder(_HS, _HS),
)

#: One way to open each step, applied on top of ``_FINE``.
_OPEN: dict[str, dict[str, Any]] = {
    "extraction": {"extraction_failures": 3},
    "queue": {"operations_failed": 2},
    "embedder": {"embedder": _embedder(_BGE, _OTHER)},
    "knowledge": {"pages_stale": 1},
}


def _facts(*open_steps: str, **extra: Any) -> HealthFacts:
    changes: dict[str, Any] = {}
    for step in open_steps:
        changes.update(_OPEN[step])
    changes.update(extra)
    return replace(_FINE, **changes)


C, W, K = "current", "waiting", "clear"


def _ids(table: list[tuple[tuple[str, ...], Any]]) -> list[str]:
    return ["+".join(open_steps) or "none" for open_steps, _expected in table]


# open steps -> (extraction, queue, embedder, knowledge) as (state, waits_on).
_ORDERING: list[tuple[tuple[str, ...], tuple[tuple[str, str | None], ...]]] = [
    ((), ((K, None), (K, None), (K, None), (K, None))),
    (("extraction",), ((C, None), (K, None), (K, None), (K, None))),
    (("queue",), ((K, None), (C, None), (K, None), (K, None))),
    (("embedder",), ((K, None), (K, None), (C, None), (K, None))),
    (("knowledge",), ((K, None), (K, None), (K, None), (C, None))),
    (("extraction", "queue"), ((C, None), (W, "extraction"), (K, None), (K, None))),
    (("extraction", "embedder"), ((C, None), (K, None), (W, "extraction"), (K, None))),
    (("extraction", "knowledge"), ((C, None), (K, None), (K, None), (W, "extraction"))),
    (("queue", "embedder"), ((K, None), (C, None), (W, "queue"), (K, None))),
    (("queue", "knowledge"), ((K, None), (C, None), (K, None), (W, "queue"))),
    (("embedder", "knowledge"), ((K, None), (K, None), (C, None), (W, "embedder"))),
    (
        ("extraction", "queue", "embedder"),
        ((C, None), (W, "extraction"), (W, "queue"), (K, None)),
    ),
    (
        ("extraction", "queue", "knowledge"),
        ((C, None), (W, "extraction"), (K, None), (W, "queue")),
    ),
    (
        ("extraction", "embedder", "knowledge"),
        ((C, None), (K, None), (W, "extraction"), (W, "embedder")),
    ),
    (
        ("queue", "embedder", "knowledge"),
        ((K, None), (C, None), (W, "queue"), (W, "embedder")),
    ),
    (
        ("extraction", "queue", "embedder", "knowledge"),
        ((C, None), (W, "extraction"), (W, "queue"), (W, "embedder")),
    ),
]


@pytest.mark.parametrize(("open_steps", "expected"), _ORDERING, ids=_ids(_ORDERING))
def test_ordering_of_every_combination_of_open_steps(
    open_steps: tuple[str, ...], expected: tuple[tuple[str, str | None], ...]
) -> None:
    path = health_path(_facts(*open_steps))
    assert [s.id for s in path.steps] == ["engine", "extraction", "queue", "embedder", "knowledge"]
    assert (path.steps[0].state, path.steps[0].reason_code) == (K, None)
    assert [(s.state, s.waits_on) for s in path.steps[1:]] == list(expected)
    assert path.verdict == ("attention" if open_steps else "clear")
    for step in path.steps:
        # A reason code exactly on the steps that are open.
        assert (step.reason_code is not None) == (step.state != K)


def test_the_table_covers_every_combination() -> None:
    assert len({frozenset(row[0]) for row in _ORDERING}) == 2 ** len(_OPEN)


# ── the short-circuit ────────────────────────────────────────────────────


def test_an_unreachable_engine_short_circuits_everything() -> None:
    facts = replace(
        _facts("extraction", "queue", "embedder", "knowledge"),
        engine_reachable=False,
        fold_in_banks=3,
    )
    path = health_path(facts)
    assert path.verdict == "unreachable"
    assert [s.as_dict() for s in path.steps] == [
        {
            "id": "engine",
            "state": "current",
            "waits_on": None,
            "reason_code": "engine_unreachable",
            "figures": {"reachable": False},
        }
    ]


# ── extraction ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"slot_resolves": False}, "extraction_slot_unresolved"),
        ({"llm_reachable": False}, "extraction_llm_unreachable"),
        ({"extraction_failures": 1}, "extraction_recent_failures"),
        # The cause comes first: a slot with no model explains the failures.
        (
            {"slot_resolves": False, "llm_reachable": False, "extraction_failures": 9},
            "extraction_slot_unresolved",
        ),
        ({"llm_reachable": False, "extraction_failures": 9}, "extraction_llm_unreachable"),
    ],
)
def test_extraction_opens_on_each_trigger(changes: dict[str, Any], reason: str) -> None:
    step = health_path(replace(_FINE, **changes)).steps[1]
    assert (step.id, step.state, step.reason_code) == ("extraction", C, reason)


def test_extraction_stays_clear_on_what_is_not_known() -> None:
    step = health_path(replace(_FINE, slot_resolves=None, llm_reachable=None)).steps[1]
    assert (step.state, step.reason_code) == (K, None)


def test_extraction_figures_carry_the_counts_the_window_and_the_errors() -> None:
    errors = ({"message": "Task exceeded the <n>s wall-clock limit", "count": 3},)
    facts = replace(_FINE, extraction_failures=3, extraction_top_errors=errors)
    assert health_path(facts).steps[1].figures == {
        "slot_resolves": True,
        "llm_reachable": True,
        "failures": 3,
        "window_s": 6 * 60 * 60,
        "top_errors": [{"message": "Task exceeded the <n>s wall-clock limit", "count": 3}],
    }


# ── queue ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("changes", "state", "reason"),
    [
        ({"operations_failed": 1}, C, "queue_operations_failed"),
        ({"oldest_waiting_s": 30 * 60 + 1.0}, C, "queue_waiting_too_long"),
        ({"operations_failed": 1, "oldest_waiting_s": 9000.0}, C, "queue_operations_failed"),
        # Exactly at the threshold is not older than it.
        ({"oldest_waiting_s": 30 * 60.0}, K, None),
        # A deep queue that is moving is not a problem.
        ({"operations_waiting": 5000, "oldest_waiting_s": 60.0}, K, None),
        ({"operations_waiting": 0, "oldest_waiting_s": None}, K, None),
    ],
)
def test_queue_opens_on_a_failure_or_an_old_waiting_operation(
    changes: dict[str, Any], state: str, reason: str | None
) -> None:
    step = health_path(replace(_FINE, **changes)).steps[2]
    assert (step.id, step.state, step.reason_code) == ("queue", state, reason)


def test_queue_figures_carry_the_ages_and_the_threshold() -> None:
    facts = replace(_FINE, operations_failed=2, operations_waiting=7, oldest_waiting_s=4000.0)
    assert health_path(facts).steps[2].figures == {
        "failed": 2,
        "waiting": 7,
        "oldest_waiting_s": 4000.0,
        "max_waiting_age_s": 30 * 60,
    }


def test_thresholds_are_the_named_constants() -> None:
    assert hp.EXTRACTION_FAILURE_WINDOW_S == 6 * 60 * 60
    assert hp.QUEUE_WAITING_MAX_AGE_S == 30 * 60


# ── embedder ─────────────────────────────────────────────────────────────

_CONFLICT = {"live": {"provider": "local", "model": _HS}}

#: name -> (status, reason code or None when the step is clear).
_EMBEDDER_CASES: dict[str, tuple[dict[str, Any], str | None]] = {
    "matches": (_embedder(_BGE, _BGE), None),
    # The ordinary upgraded box: the store on the engine default, the config
    # still at the shipped default, not mixed, no conflict.
    "upgraded box": (_embedder(_BGE, _HS), None),
    "operator set another model": (_embedder(_BGE, _OTHER), "embedder_needs_switch"),
    "operator set another provider": (
        _embedder(_BGE, _BGE, configured_provider="openai"),
        "embedder_needs_switch",
    ),
    "operator moved back to the engine default": (_embedder(_HS, _BGE), "embedder_needs_switch"),
    "mixed": (_embedder(_HS, _HS, mixed=True), "embedder_mixed"),
    "upgraded box, mixed": (_embedder(_BGE, _HS, mixed=True), "embedder_mixed"),
    "conflict": (_embedder(_BGE, _BGE, conflict=_CONFLICT), "embedder_conflict"),
    "upgraded box, conflict": (_embedder(_BGE, _HS, conflict=_CONFLICT), "embedder_conflict"),
    "record unreadable": (
        _embedder(_BGE, _BGE, record_error="/state/memory-embedder.json"),
        "embedder_record_unreadable",
    ),
    "store unavailable": (
        _embedder(_BGE, _HS, store_unavailable="/store"),
        "embedder_store_unavailable",
    ),
}


@pytest.mark.parametrize("case", sorted(_EMBEDDER_CASES))
def test_embedder_step_per_status(case: str) -> None:
    status, reason = _EMBEDDER_CASES[case]
    step = health_path(replace(_FINE, embedder=status)).steps[3]
    assert step.id == "embedder"
    assert step.reason_code == reason
    assert step.state == (K if reason is None else C)


def test_the_upgraded_box_is_clear_and_says_why() -> None:
    step = health_path(replace(_FINE, embedder=_embedder(_BGE, _HS))).steps[3]
    assert step.state == K
    assert step.figures == {
        "recorded": {"provider": "local", "model": _BGE},
        "configured": {"provider": "local", "model": _HS},
        "mixed": False,
        "conflict": None,
        "reasons": [],
        "informational": True,
    }


@pytest.mark.parametrize("case", sorted(_EMBEDDER_CASES))
def test_embedder_step_agrees_with_the_doctor_row(case: str) -> None:
    """One rule: the step is open exactly when ``memory_embedder`` warns."""
    status, _reason = _EMBEDDER_CASES[case]
    step = health_path(replace(_FINE, embedder=status)).steps[3]
    row = da.check_memory_embedder(status=status)
    assert (step.state != K) == (row.status == "warn")
    assert bool(embedder_verdict(status).reasons) == (row.status == "warn")


def test_embedder_step_is_clear_when_the_status_is_not_known() -> None:
    step = health_path(replace(_FINE, embedder=None)).steps[3]
    assert (step.id, step.state, step.reason_code, step.figures) == ("embedder", K, None, {})


# ── knowledge ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"pages_stale": 2}, "knowledge_stale"),
        ({"models_stale": 1}, "knowledge_stale"),
        ({"refresh_failed": 1}, "knowledge_refresh_failed"),
        ({"pages_stale": 4, "refresh_failed": 1}, "knowledge_refresh_failed"),
    ],
)
def test_knowledge_opens_on_a_stale_item_or_a_failed_refresh(
    changes: dict[str, Any], reason: str
) -> None:
    step = health_path(replace(_FINE, **changes)).steps[4]
    assert (step.id, step.state, step.reason_code) == ("knowledge", C, reason)
    assert step.figures == {"pages_stale": 0, "models_stale": 0, "refresh_failed": 0, **changes}


# ── the fold-in branch ───────────────────────────────────────────────────


def test_without_banks_to_fold_in_the_embedder_step_holds_the_position() -> None:
    ids = [s.id for s in health_path(replace(_FINE, fold_in_banks=0)).steps]
    assert ids == ["engine", "extraction", "queue", "embedder", "knowledge"]


def test_banks_to_fold_in_put_the_fold_in_step_in_the_embedder_position() -> None:
    # Even a store that would open the embedder step: the fold-in re-embeds.
    facts = replace(_FINE, fold_in_banks=3, embedder=_embedder(_HS, _HS, mixed=True))
    path = health_path(facts)
    assert [s.id for s in path.steps] == ["engine", "extraction", "queue", "fold_in", "knowledge"]
    assert path.steps[3].as_dict() == {
        "id": "fold_in",
        "state": "current",
        "waits_on": None,
        "reason_code": "fold_in_pending",
        "figures": {"banks": 3},
    }
    assert path.verdict == "attention"


# open steps beside the fold-in -> (extraction, queue, fold_in, knowledge).
_FOLD_IN_ORDERING: list[tuple[tuple[str, ...], tuple[tuple[str, str | None], ...]]] = [
    ((), ((K, None), (K, None), (C, None), (K, None))),
    (("extraction",), ((C, None), (K, None), (W, "extraction"), (K, None))),
    (("queue",), ((K, None), (C, None), (W, "queue"), (K, None))),
    (("knowledge",), ((K, None), (K, None), (C, None), (W, "fold_in"))),
    (("extraction", "queue"), ((C, None), (W, "extraction"), (W, "queue"), (K, None))),
    (("extraction", "knowledge"), ((C, None), (K, None), (W, "extraction"), (W, "fold_in"))),
    (("queue", "knowledge"), ((K, None), (C, None), (W, "queue"), (W, "fold_in"))),
    (
        ("extraction", "queue", "knowledge"),
        ((C, None), (W, "extraction"), (W, "queue"), (W, "fold_in")),
    ),
]


@pytest.mark.parametrize(("open_steps", "expected"), _FOLD_IN_ORDERING, ids=_ids(_FOLD_IN_ORDERING))
def test_ordering_with_the_fold_in_step(
    open_steps: tuple[str, ...], expected: tuple[tuple[str, str | None], ...]
) -> None:
    path = health_path(_facts(*open_steps, fold_in_banks=1))
    assert [s.id for s in path.steps[1:]] == ["extraction", "queue", "fold_in", "knowledge"]
    assert [(s.state, s.waits_on) for s in path.steps[1:]] == list(expected)
    assert path.verdict == "attention"


def test_a_step_serialises_to_exactly_the_wire_keys() -> None:
    for step in health_path(_facts("queue", "knowledge")).steps:
        assert set(step.as_dict()) == {"id", "state", "waits_on", "reason_code", "figures"}
