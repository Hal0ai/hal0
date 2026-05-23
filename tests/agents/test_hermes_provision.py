"""Unit tests for :mod:`hal0.agents.hermes_provision` (issue #238).

The scaffold lands with no-op phase stubs; these tests pin the
state-machine invariants downstream slices rely on:

* Every phase runs in declared order.
* Successful runs produce a checkpoint with every phase marked ``ok``.
* Re-runs are no-ops (every phase is skipped because checkpoints exist).
* ``--repair`` forces re-execution of every phase.
* ``--skip-phase`` records ``skip`` for the named phase.
* The state file round-trips through ``BootstrapState.load`` ↔
  ``BootstrapState.save`` losslessly.

Phase ordering matters because downstream phases consume earlier
phases' outputs (env_probe → config_write → mcp_wire). A regression
that re-orders or drops a phase here surfaces before the integration
slice notices.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.agents import hermes_provision as hp


def test_phase_names_in_planned_order() -> None:
    """The planned 12 phases stay in the documented order.

    Mirrors `docs/internal/hermes-bootstrap-plan-2026-05-23.md` §3 —
    if a slice re-orders or drops a phase, this guard catches it
    before the integration scenario notices.
    """
    expected = (
        "preflight",
        "install",
        "env_probe",
        "home_init",
        "config_write",
        "mcp_wire",
        "context_link",
        "namespace_register",
        "model_automap",
        "voice_wire",
        "smoke_tests",
        "self_report",
    )
    assert expected == hp.PHASE_NAMES


def test_run_marks_every_phase_ok_on_fresh(tmp_path: Path) -> None:
    result = hp.run(state_root=tmp_path)
    for name in hp.PHASE_NAMES:
        assert result.phases[name]["status"] == hp.PhaseStatus.OK.value
    assert result.failed == []
    # Skipped is empty on a fresh run because no checkpoint exists.
    assert result.skipped == []


def test_state_file_written_and_round_trips(tmp_path: Path) -> None:
    hp.run(state_root=tmp_path)
    state_file = tmp_path / "provision.json"
    assert state_file.exists()
    loaded = hp.BootstrapState.load(tmp_path)
    assert loaded is not None
    assert loaded.schema_version == hp.SCHEMA_VERSION
    assert set(loaded.phases.keys()) >= set(hp.PHASE_NAMES)
    assert loaded.completed_at is not None


def test_rerun_is_noop_when_all_phases_ok(tmp_path: Path) -> None:
    hp.run(state_root=tmp_path)
    second = hp.run(state_root=tmp_path)
    # All phases skipped because their checkpoint is already ok.
    assert set(second.skipped) == set(hp.PHASE_NAMES)
    assert second.failed == []


def test_repair_flag_forces_rerun(tmp_path: Path) -> None:
    hp.run(state_root=tmp_path)
    second = hp.run(state_root=tmp_path, repair=True)
    # Repair re-runs everything → nothing was skipped via checkpoint.
    assert second.skipped == []
    for name in hp.PHASE_NAMES:
        assert second.phases[name]["status"] == hp.PhaseStatus.OK.value


def test_skip_phase_records_skip_reason(tmp_path: Path) -> None:
    result = hp.run(state_root=tmp_path, skip_phases=("voice_wire", "smoke_tests"))
    assert result.phases["voice_wire"]["status"] == hp.PhaseStatus.SKIP.value
    assert result.phases["voice_wire"]["reason"] == "--skip-phase"
    assert result.phases["smoke_tests"]["status"] == hp.PhaseStatus.SKIP.value
    # Other phases run as normal.
    assert result.phases["preflight"]["status"] == hp.PhaseStatus.OK.value


def test_dry_run_skips_state_persistence(tmp_path: Path) -> None:
    hp.run(state_root=tmp_path, dry_run=True)
    assert not (tmp_path / "provision.json").exists()


def test_load_returns_none_when_state_file_missing(tmp_path: Path) -> None:
    assert hp.BootstrapState.load(tmp_path) is None


def test_load_returns_none_when_state_file_corrupt(tmp_path: Path) -> None:
    (tmp_path / "provision.json").write_text("not-json")
    assert hp.BootstrapState.load(tmp_path) is None


def test_phase_result_to_dict_includes_optional_fields() -> None:
    r = hp.PhaseResult(
        status=hp.PhaseStatus.OK,
        details={"k": "v"},
        hash="abc",
        reason=None,
    )
    out = r.to_dict()
    assert out["status"] == "ok"
    assert out["hash"] == "abc"
    assert out["details"] == {"k": "v"}
    assert "reason" not in out  # None reasons omitted


def test_content_hash_is_stable_and_collision_free() -> None:
    a = hp.content_hash("foo", "bar")
    b = hp.content_hash("foo", "bar")
    c = hp.content_hash("foo", "baz")
    assert a == b
    assert a != c
    # Stable across the str/bytes split.
    d = hp.content_hash(b"foo", "bar")
    assert d == a


def test_failed_phase_surfaces_in_result_and_blocks_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _failing(_state: hp.BootstrapState) -> hp.PhaseResult:
        return hp.PhaseResult(status=hp.PhaseStatus.FAIL, reason="forced")

    # Patch the env_probe stub for this test only.
    new_phases = [(name, _failing if name == "env_probe" else fn) for name, fn in hp.PHASES]
    monkeypatch.setattr(hp, "PHASES", new_phases)

    result = hp.run(state_root=tmp_path)
    assert "env_probe" in result.failed
    assert result.state.completed_at is None
    assert any("env_probe" in e for e in result.state.errors)


def test_cli_entry_returns_zero_on_success(tmp_path: Path) -> None:
    rc = hp.bootstrap_cli(
        repair=False,
        dry_run=False,
        skip_phases=(),
        verbose=False,
        state_root=tmp_path,
    )
    assert rc == 0


def test_cli_entry_returns_one_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _failing(_state: hp.BootstrapState) -> hp.PhaseResult:
        return hp.PhaseResult(status=hp.PhaseStatus.FAIL, reason="boom")

    new_phases = [(name, _failing if name == "preflight" else fn) for name, fn in hp.PHASES]
    monkeypatch.setattr(hp, "PHASES", new_phases)
    rc = hp.bootstrap_cli(
        repair=False,
        dry_run=False,
        skip_phases=(),
        verbose=False,
        state_root=tmp_path,
    )
    assert rc == 1
