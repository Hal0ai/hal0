"""converge_components: order, flag fan-out, isolation, bookkeeping."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from hal0.components import runner, state


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))


def _patch_arms(calls):
    def make(name):
        def arm(**kwargs):
            calls.append((name, kwargs))
            return {"status": "converged"}
        return arm
    return (
        patch("hal0.components.registry._openwebui_converge", side_effect=make("openwebui")),
        patch("hal0.components.registry._runner_images_converge", side_effect=make("runner-images")),
        patch("hal0.components.registry._hermes_converge", side_effect=make("hermes")),
        patch("hal0.components.registry._hindsight_converge", side_effect=make("hindsight")),
    )


def test_order_and_flags_update_path() -> None:
    calls: list = []
    p1, p2, p3, p4 = _patch_arms(calls)
    with p1, p2, p3, p4:
        results = runner.converge_components(job_id="j1")
    assert [c[0] for c in calls] == ["openwebui", "runner-images", "hermes", "hindsight"]
    assert calls[3][1] == {"job_id": "j1", "upgrade": True}   # hindsight kwarg spelling
    assert calls[0][1] == {"job_id": "j1", "apply": True}
    assert set(results) == {"openwebui", "runner-images", "hermes", "hindsight"}


def test_boot_path_is_diagnose_only() -> None:
    calls: list = []
    p1, p2, p3, p4 = _patch_arms(calls)
    with p1, p2, p3, p4:
        runner.converge_components(apply=False)
    assert all(
        kw.get("apply") is False or kw.get("upgrade") is False for _, kw in calls
    )


def test_one_arm_crashing_never_blocks_the_next() -> None:
    calls: list = []
    p1, p2, p3, p4 = _patch_arms(calls)
    with p1 as owui, p2, p3, p4:
        owui.side_effect = RuntimeError("bug")
        results = runner.converge_components()
    assert results["openwebui"]["status"] == "build_failed"
    assert [c[0] for c in calls] == ["runner-images", "hermes", "hindsight"]


def test_results_recorded_to_state_store() -> None:
    p1, p2, p3, p4 = _patch_arms([])
    with p1, p2, p3, p4:
        runner.converge_components()
    assert set(state.load_component_state()) == {"openwebui", "runner-images", "hermes", "hindsight"}


# ── M1: boot-time diagnose must not erase a recorded failure breadcrumb ──────


def test_boot_diagnose_does_not_clobber_a_recorded_failure() -> None:
    # Seed a real failure (as a prior apply=True converge would have
    # recorded) — the error/remedy the dashboard Retry button reads.
    state.record_component_result(
        "openwebui",
        {"status": "build_failed", "error": "pull failed", "remedy": "check registry creds"},
    )
    calls: list = []
    p1, p2, p3, p4 = _patch_arms(calls)
    with p1, p2, p3, p4:
        results = runner.converge_components(apply=False)
    # The arm still ran (diagnose is a real probe) and its own return value
    # is handed back to the caller...
    assert results["openwebui"]["status"] == "converged"
    # ...but components.json keeps the prior failure breadcrumb rather than
    # being overwritten with the diagnose-only result that has no
    # error/remedy of its own.
    stored = state.load_component_state()["openwebui"]
    assert stored["status"] == "build_failed"
    assert stored["error"] == "pull failed"
    assert stored["remedy"] == "check registry creds"


def test_boot_diagnose_still_records_when_no_prior_failure() -> None:
    # No pre-existing entry at all — the common case (fresh box / first
    # boot) — must still get recorded so the dashboard has something to show.
    p1, p2, p3, p4 = _patch_arms([])
    with p1, p2, p3, p4:
        runner.converge_components(apply=False)
    stored = state.load_component_state()
    assert stored["openwebui"]["status"] == "converged"


def test_apply_true_converge_overwrites_a_prior_failure() -> None:
    # A retry / `hal0 update` pass (apply=True) is the freshest truth and
    # must always overwrite, whether curing the failure or reconfirming it.
    state.record_component_result(
        "openwebui", {"status": "build_failed", "error": "pull failed"}
    )
    p1, p2, p3, p4 = _patch_arms([])
    with p1, p2, p3, p4:
        runner.converge_components(apply=True)
    stored = state.load_component_state()["openwebui"]
    assert stored["status"] == "converged"
    assert "error" not in stored


def test_converge_component_diagnose_only_flag_gates_recording_directly() -> None:
    # Exercise converge_component() itself (not just via converge_components)
    # since the retry route calls it directly.
    from hal0.components.registry import COMPONENTS

    comp = next(c for c in COMPONENTS if c.id == "openwebui")
    state.record_component_result(
        "openwebui", {"status": "snapshot_failed", "error": "boom", "remedy": "retry"}
    )
    with patch(
        "hal0.components.registry._openwebui_converge",
        side_effect=lambda **kw: {"status": "stale"},
    ):
        result = runner.converge_component(comp, diagnose_only=True, job_id=None, apply=False)
    assert result["status"] == "stale"
    stored = state.load_component_state()["openwebui"]
    assert stored["status"] == "snapshot_failed"  # untouched
