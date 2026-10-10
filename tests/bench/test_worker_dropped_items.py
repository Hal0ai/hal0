"""#2408 — every path where the worker gives up on a queued item must leave a
recorded failure (failed.json) via ``control.fail`` instead of only printing.

Driven through the real ``cmd_worker`` loop; the loop is stopped from
``time.sleep`` once the queue drains.
"""

from __future__ import annotations

import contextlib
import time
import types

import pytest

from hal0.bench import cli, control, evalrun


class _StopLoop(BaseException):
    pass


REGISTRY = [{"id": "a", "path": "/m/A/model.gguf"}]


@pytest.fixture
def worker(monkeypatch, tmp_path):
    monkeypatch.setenv("HAL0_BENCH_STATE", str(tmp_path))
    control.set_control(state="running")
    monkeypatch.setattr(cli, "fetch_registry_models", lambda api: REGISTRY)
    monkeypatch.setattr(evalrun, "tool_eval_missing", lambda: None)
    calls: list[tuple] = []
    real_fail = control.fail

    def spy(item_id, note, at):
        calls.append((item_id, note))
        return real_fail(item_id, note, at)

    monkeypatch.setattr(control, "fail", spy)

    def stop_when_drained(_):
        if not control.read_queue():
            raise _StopLoop

    monkeypatch.setattr(time, "sleep", stop_when_drained)

    def run(item: dict) -> list[tuple]:
        control.enqueue(item)
        with contextlib.suppress(_StopLoop):
            cli.cmd_worker(types.SimpleNamespace(api="http://x", poll=0))
        return calls

    return run


def _assert_failed(calls, item_id: str, note_part: str) -> dict:
    assert [c[0] for c in calls] == [item_id]
    assert control.read_queue() == []
    failed = control.read_failed()
    assert [f["id"] for f in failed] == [item_id]
    assert failed[0]["outcome"] == "failed"
    assert note_part in failed[0]["note"]
    assert failed[0]["failed_at"]
    return failed[0]


def test_unknown_suite_is_recorded(worker) -> None:
    calls = worker({"id": "s1", "suite": "no-such-suite"})
    rec = _assert_failed(calls, "s1", "unknown suite 'no-such-suite'")
    assert "/" not in rec["note"]  # no operator-local suite dir in the reason


def test_missing_tool_eval_binary_is_recorded(worker, monkeypatch) -> None:
    monkeypatch.setattr(evalrun, "tool_eval_missing", lambda: "tool-eval-bench not installed")
    calls = worker({"id": "e1", "kind": "eval", "model": "a"})
    _assert_failed(calls, "e1", "tool-eval-bench not installed")


def test_item_with_no_suite_or_model_is_recorded(worker) -> None:
    calls = worker({"id": "n1"})
    _assert_failed(calls, "n1", "neither a suite nor a model")


def test_eval_with_unresolved_model_is_recorded(worker, monkeypatch) -> None:
    def no_eval(*a, **k):
        raise AssertionError("an unresolved eval model must not run")

    monkeypatch.setattr(cli, "_worker_eval", no_eval)
    calls = worker({"id": "e2", "kind": "eval", "model": "nope"})
    _assert_failed(calls, "e2", "unknown model 'nope'")


def test_give_up_after_three_attempts_is_recorded_without_secrets(worker, monkeypatch) -> None:
    def boom(api):
        raise RuntimeError("GET http://x/v1 failed: Authorization: Bearer sk-supersecret123456")

    monkeypatch.setattr(cli, "fetch_registry_models", boom)
    calls = worker({"id": "g1", "model": "a", "attempts": 2})
    rec = _assert_failed(calls, "g1", "gave up after 3 attempts: RuntimeError")
    assert "supersecret" not in rec["note"]


def test_transient_error_still_requeues_without_recording(worker, monkeypatch) -> None:
    def boom(api):
        raise RuntimeError("registry blip")

    monkeypatch.setattr(cli, "fetch_registry_models", boom)

    def sleep(_):
        raise _StopLoop

    monkeypatch.setattr(time, "sleep", sleep)
    control.enqueue({"id": "t1", "model": "a"})
    with pytest.raises(_StopLoop):
        cli.cmd_worker(types.SimpleNamespace(api="http://x", poll=0))
    queue = control.read_queue()
    assert [i["id"] for i in queue] == ["t1"]
    assert queue[0]["attempts"] == 1
    assert control.read_failed() == []
