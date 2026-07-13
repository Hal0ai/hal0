"""Unit tests for the shared provisioning state machine.

Covers the agent-agnostic engine (checkpointing, skip-if-ok, --repair,
fatal-abort, needs-graph validation) without any hal0/agent deps.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from hal0.agents import provision_engine as engine
from hal0.agents.provision_engine import (
    BootstrapState,
    Phase,
    PhaseContext,
    PhaseNeedError,
    PhaseResult,
    PhaseStatus,
)


@dataclass
class _State(BootstrapState):
    agent_id: str = "test"
    home: str = "/tmp/x"


def _ok(details: dict | None = None):
    def _fn(ctx: PhaseContext) -> PhaseResult:
        return PhaseResult(PhaseStatus.OK, details=details or {})

    return _fn


def test_run_executes_all_phases_and_stamps_completed(tmp_path: Path) -> None:
    calls: list[str] = []

    def make(name: str):
        def _fn(ctx: PhaseContext) -> PhaseResult:
            calls.append(name)
            return PhaseResult(PhaseStatus.OK, details={"n": name})

        return _fn

    phases = [Phase("a", make("a")), Phase("b", make("b"), needs=("a",))]
    engine.validate_phase_graph(phases)
    res = engine.run(phases, state_root=tmp_path, state_cls=_State)
    assert calls == ["a", "b"]
    assert res.failed == []
    assert res.state.completed_at is not None
    assert (tmp_path / "provision.json").exists()


def test_rerun_skips_completed_phases(tmp_path: Path) -> None:
    calls: list[str] = []

    def make(name: str):
        def _fn(ctx: PhaseContext) -> PhaseResult:
            calls.append(name)
            return PhaseResult(PhaseStatus.OK)

        return _fn

    phases = [Phase("a", make("a")), Phase("b", make("b"))]
    engine.run(phases, state_root=tmp_path, state_cls=_State)
    calls.clear()
    res = engine.run(phases, state_root=tmp_path, state_cls=_State)
    assert calls == []  # both already ok → skipped
    assert set(res.skipped) == {"a", "b"}


def test_repair_forces_rerun(tmp_path: Path) -> None:
    calls: list[str] = []
    phases = [Phase("a", lambda c: (calls.append("a"), PhaseResult(PhaseStatus.OK))[1])]
    engine.run(phases, state_root=tmp_path, state_cls=_State)
    calls.clear()
    engine.run(phases, state_root=tmp_path, state_cls=_State, repair=True)
    assert calls == ["a"]


def test_always_run_phase_reruns_even_when_ok(tmp_path: Path) -> None:
    calls: list[str] = []
    phases = [
        Phase("a", lambda c: (calls.append("a"), PhaseResult(PhaseStatus.OK))[1], always_run=True),
    ]
    engine.run(phases, state_root=tmp_path, state_cls=_State)
    engine.run(phases, state_root=tmp_path, state_cls=_State)
    assert calls == ["a", "a"]


def test_fatal_failure_aborts_and_skips_rest(tmp_path: Path) -> None:
    calls: list[str] = []

    def boom(ctx: PhaseContext) -> PhaseResult:
        calls.append("boom")
        return PhaseResult(PhaseStatus.FAIL, reason="nope", fatal=True)

    phases = [
        Phase("boom", boom),
        Phase("after", lambda c: (calls.append("after"), PhaseResult(PhaseStatus.OK))[1]),
    ]
    res = engine.run(phases, state_root=tmp_path, state_cls=_State)
    assert calls == ["boom"]  # 'after' never ran
    assert res.aborted is True
    assert res.abort_reason == "nope"
    assert res.state.phases["after"]["status"] == PhaseStatus.SKIP.value


def test_nonfatal_failure_is_run_all(tmp_path: Path) -> None:
    calls: list[str] = []
    phases = [
        Phase("bad", lambda c: (calls.append("bad"), PhaseResult(PhaseStatus.FAIL, reason="x"))[1]),
        Phase("good", lambda c: (calls.append("good"), PhaseResult(PhaseStatus.OK))[1]),
    ]
    res = engine.run(phases, state_root=tmp_path, state_cls=_State)
    assert calls == ["bad", "good"]  # non-fatal → keep going
    assert res.failed == ["bad"]
    assert res.state.completed_at is None  # not stamped when anything failed


def test_output_of_reads_declared_producer(tmp_path: Path) -> None:
    seen: dict = {}

    def reader(ctx: PhaseContext) -> PhaseResult:
        seen.update(ctx.output_of("producer"))
        return PhaseResult(PhaseStatus.OK)

    phases = [Phase("producer", _ok({"k": 42})), Phase("reader", reader, needs=("producer",))]
    engine.run(phases, state_root=tmp_path, state_cls=_State)
    assert seen == {"k": 42}


def test_undeclared_output_of_raises_phaseneederror() -> None:
    ctx = PhaseContext(state=_State(), phase_name="r", allowed_needs=frozenset())
    with pytest.raises(PhaseNeedError):
        ctx.output_of("x")


@pytest.mark.parametrize(
    "phases",
    [
        [Phase("a", _ok()), Phase("a", _ok())],  # duplicate name
        [Phase("a", _ok(), needs=("missing",))],  # unknown need
        [Phase("a", _ok(), needs=("b",)), Phase("b", _ok())],  # need doesn't precede
    ],
    ids=["duplicate", "unknown-need", "need-not-preceding"],
)
def test_validate_phase_graph_rejects_bad_graphs(phases: list[Phase]) -> None:
    with pytest.raises(ValueError):
        engine.validate_phase_graph(phases)


def test_validate_phase_graph_accepts_needs_previous_forward_edge() -> None:
    # needs_previous target must FOLLOW the reader (cross-run edge) — valid.
    engine.validate_phase_graph([Phase("a", _ok(), needs_previous=("b",)), Phase("b", _ok())])


def test_state_roundtrips_subclass_fields(tmp_path: Path) -> None:
    st = _State(home="/custom/home", agent_id="zz")
    st.save(tmp_path)
    loaded = _State.load(tmp_path)
    assert loaded is not None
    assert loaded.home == "/custom/home"
    assert loaded.agent_id == "zz"
