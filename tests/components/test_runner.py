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
