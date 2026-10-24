"""#2979 — a queued run whose exclusive window left a slot down must not leave
the queue as a clean success.

Driven through the real ``cmd_worker`` loop with the planner and the session
stubbed; the loop is stopped from ``time.sleep``.
"""

from __future__ import annotations

import time
import types

import pytest

from hal0.bench import cli, control
from hal0.bench.runner import SessionResult


class _StopLoop(Exception):
    pass


REGISTRY = [{"id": "a", "path": "/m/A/model.gguf"}]


@pytest.fixture
def worker(monkeypatch, tmp_path):
    monkeypatch.setenv("HAL0_BENCH_STATE", str(tmp_path))
    control.set_control(state="running")
    monkeypatch.setattr(cli, "fetch_registry_models", lambda api: REGISTRY)
    monkeypatch.setattr(cli, "plan", lambda suite, models, store: [])
    monkeypatch.setattr(cli, "_session_host", lambda api, exclusive: None)
    monkeypatch.setattr(cli.Store, "reindex", lambda self: 0)
    monkeypatch.setattr(cli, "regress_check", lambda store: [])

    def stop_when_drained(_):
        raise _StopLoop

    monkeypatch.setattr(time, "sleep", stop_when_drained)

    def run(result: SessionResult) -> None:
        monkeypatch.setattr(cli, "run_session", lambda *a, **k: result)
        control.enqueue({"id": "q1", "label": "a", "model": "a"})
        # A clean item drains and the loop idles into the stubbed sleep.
        with pytest.raises(_StopLoop):
            cli.cmd_worker(types.SimpleNamespace(api="http://x", poll=0))

    return run


def test_slot_errors_park_the_item_in_failed_with_a_note(worker) -> None:
    worker(SessionResult(suite_id="s", slot_errors=["failed to restart hal0-slot@2: boom"]))

    assert control.read_queue() == []
    failed = control.read_failed()
    assert [f["id"] for f in failed] == ["q1"]
    assert failed[0]["outcome"] == "failed"
    assert "hal0-slot@2" in failed[0]["note"]
    assert "boom" in failed[0]["note"]


def test_a_clean_session_leaves_no_failure(worker) -> None:
    worker(SessionResult(suite_id="s"))

    assert control.read_queue() == []
    assert control.read_failed() == []
