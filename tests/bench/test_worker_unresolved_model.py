"""#2387 — a queued benchmark whose model reference resolves to no registry
model (unknown, or a basename several models share) must leave a recorded
failure behind instead of vanishing from the queue silently.

Driven through the real ``cmd_worker`` loop with the registry injected and the
planner/runner never reached: the loop is stopped from ``time.sleep``.
"""

from __future__ import annotations

import time
import types

import pytest

from hal0.bench import cli, control


class _StopLoop(Exception):
    pass


REGISTRY = [
    {"id": "a", "path": "/m/A/model.gguf"},
    {"id": "b", "path": "/m/B/model.gguf"},
]


@pytest.fixture
def worker(monkeypatch, tmp_path):
    monkeypatch.setenv("HAL0_BENCH_STATE", str(tmp_path))
    control.set_control(state="running")
    monkeypatch.setattr(cli, "fetch_registry_models", lambda api: REGISTRY)

    def no_plan(*a, **k):
        raise AssertionError("an unresolved model must not reach the planner")

    monkeypatch.setattr(cli, "plan", no_plan)
    monkeypatch.setattr(cli, "run_session", no_plan)

    def stop_when_drained(_):
        if not control.read_queue():
            raise _StopLoop

    monkeypatch.setattr(time, "sleep", stop_when_drained)

    def run(item: dict) -> None:
        control.enqueue(item)
        with pytest.raises(_StopLoop):
            cli.cmd_worker(types.SimpleNamespace(api="http://x", poll=0))

    return run


def test_unknown_model_is_dequeued_with_a_recorded_failure(worker) -> None:
    worker({"id": "u1", "label": "nope.gguf", "model": "nope.gguf"})

    assert control.read_queue() == []
    failed = control.read_failed()
    assert [f["id"] for f in failed] == ["u1"]
    assert failed[0]["outcome"] == "failed"
    assert failed[0]["note"] == "unknown model 'nope.gguf'"
    assert failed[0]["failed_at"]  # timestamped


def test_ambiguous_basename_is_dequeued_with_a_recorded_failure(worker) -> None:
    worker({"id": "a1", "label": "model.gguf", "model": "model.gguf"})

    assert control.read_queue() == []
    failed = control.read_failed()
    assert [f["id"] for f in failed] == ["a1"]
    assert failed[0]["outcome"] == "failed"
    assert failed[0]["note"] == ("ambiguous model reference 'model.gguf' (2 registry models match)")
