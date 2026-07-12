"""test_worker_eval.py — the queued-eval worker path (resume + queue politeness).

Two behaviours, both exercised without a live agent or GPU:

  * Resume (bug A): an eval that defers after N of M tasks resumes under the SAME
    run_id and runs only the remaining tasks — it never re-runs or double-writes a
    record for a task that already completed. Contrast the old behaviour, which
    minted a fresh ``_now_stamp`` run_id every tick and re-ran the whole set.
  * Head-of-line politeness (bug B): an eval that defers over live traffic yields
    its head-of-line position so a non-eval item behind it can drain, while an eval
    paused by Stop stays at the head so Start resumes the same item.
"""

from __future__ import annotations

import types

import pytest

from hal0.bench import cli, control, evalrun


def _fake_tasks(n: int) -> list:
    return [types.SimpleNamespace(id=f"t{i}") for i in range(n)]


def _rec(run_id: str, task_id: str) -> evalrun.EvalRecord:
    return evalrun.EvalRecord(
        run_id=run_id,
        suite="agentic",
        task_id=task_id,
        kind="code",
        model="m",
        outcome="ok",
        score=1.0,
        correct=True,
        expected="x",
        answer="x",
        checkpoints_hit=[],
        checkpoints_total=0,
        metrics={"wall_s": 0.1},
    )


def test_worker_eval_resumes_without_duplicate_records(monkeypatch, tmp_path):
    monkeypatch.setenv("HAL0_BENCH_STATE", str(tmp_path))
    control.set_control(state="running")

    tasks = _fake_tasks(4)
    monkeypatch.setattr(evalrun, "TASKS", tasks)

    ran: list[str] = []

    def fake_run_task(task, model, run_id, api, workroot):
        ran.append(task.id)
        return _rec(run_id, task.id)

    monkeypatch.setattr(evalrun, "run_task", fake_run_task)

    # Live traffic appears once 2 tasks have landed on the first tick, then clears
    # before the second tick (a transient burst).
    gate = {"busy": True, "after": 2}

    def fake_traffic(api, *a, **k):
        return gate["busy"] and len(evalrun.read_evals()) >= gate["after"]

    monkeypatch.setattr(cli, "traffic_in_flight", fake_traffic)

    item = {"id": "abcd1234", "model": "m", "kind": "eval"}

    # Tick 1: runs t0, t1, then backs off on traffic -> deferred.
    assert cli._worker_eval("m", "http://x", item) is False
    assert ran == ["t0", "t1"]
    evals = evalrun.read_evals()
    assert len(evals) == 2
    run_id = cli._eval_run_id(item)
    assert {r["run_id"] for r in evals} == {run_id}

    # Traffic clears; Tick 2 resumes and finishes only the remainder.
    gate["busy"] = False
    assert cli._worker_eval("m", "http://x", item) is True

    # Each task ran exactly once across both ticks — no re-runs, no duplicates.
    assert ran == ["t0", "t1", "t2", "t3"]
    evals = evalrun.read_evals()
    assert len(evals) == 4
    assert sorted(r["task_id"] for r in evals) == ["t0", "t1", "t2", "t3"]
    assert {r["run_id"] for r in evals} == {run_id}  # one stable run_id throughout


def test_worker_eval_noop_when_all_tasks_already_done(monkeypatch, tmp_path):
    monkeypatch.setenv("HAL0_BENCH_STATE", str(tmp_path))
    control.set_control(state="running")
    monkeypatch.setattr(evalrun, "TASKS", _fake_tasks(2))
    item = {"id": "deadbeef", "model": "m", "kind": "eval"}
    run_id = cli._eval_run_id(item)
    for tid in ("t0", "t1"):
        evalrun.append_eval(_rec(run_id, tid))

    def boom(*a, **k):  # must not run any task
        raise AssertionError("run_task called for an already-completed run")

    monkeypatch.setattr(evalrun, "run_task", boom)
    monkeypatch.setattr(cli, "traffic_in_flight", lambda *a, **k: False)

    assert cli._worker_eval("m", "http://x", item) is True
    assert len(evalrun.read_evals()) == 2  # unchanged


class _StopLoop(BaseException):
    # BaseException (not Exception) so the worker's broad ``except Exception``
    # guard doesn't swallow our loop-breaker and drop the item under test.
    pass


def _worker_args() -> types.SimpleNamespace:
    return types.SimpleNamespace(api="http://x", poll=0)


def test_deferred_eval_yields_head_of_line(monkeypatch, tmp_path):
    monkeypatch.setenv("HAL0_BENCH_STATE", str(tmp_path))
    control.set_control(state="running")
    control.enqueue({"id": "ev1", "model": "m-eval", "kind": "eval"})
    control.enqueue({"id": "su1", "suite": "roster"})

    monkeypatch.setattr(cli, "fetch_registry_models", lambda api: [])
    # eval always defers over (simulated) live traffic
    monkeypatch.setattr(cli, "_worker_eval", lambda model, api, item: False)

    import time as _time

    def fake_sleep(_):
        raise _StopLoop  # break out of the worker's forever-loop after one tick

    monkeypatch.setattr(_time, "sleep", fake_sleep)

    with pytest.raises(_StopLoop):
        cli.cmd_worker(_worker_args())

    # The eval rotated to the tail; the suite item is now head and can drain.
    assert [i["id"] for i in control.read_queue()] == ["su1", "ev1"]


def test_stopped_eval_keeps_head_of_line(monkeypatch, tmp_path):
    monkeypatch.setenv("HAL0_BENCH_STATE", str(tmp_path))
    control.set_control(state="running")
    control.enqueue({"id": "ev1", "model": "m-eval", "kind": "eval"})
    control.enqueue({"id": "su1", "suite": "roster"})

    monkeypatch.setattr(cli, "fetch_registry_models", lambda api: [])

    def stop_then_defer(model, api, item):
        control.set_control(state="stopped")  # operator hits Stop mid-eval
        return False

    monkeypatch.setattr(cli, "_worker_eval", stop_then_defer)

    import time as _time

    def fake_sleep(_):
        raise _StopLoop

    monkeypatch.setattr(_time, "sleep", fake_sleep)

    with pytest.raises(_StopLoop):
        cli.cmd_worker(_worker_args())

    # Stopped: the eval stays at the head so Start resumes THIS item.
    assert [i["id"] for i in control.read_queue()] == ["ev1", "su1"]
