"""`hal0 bench run` — a slot the exclusive window left down fails the run (#2770).

Before this fix a failed slot restart was only a stderr warning: the
``bench.session.completed`` event and the exit code both said the session was
fine while the pinned ``brain`` slot sat ``failed`` for thirteen hours.
"""

from __future__ import annotations

import json
from pathlib import Path

from hal0.bench import cli
from hal0.bench.runner import SessionResult
from hal0.bench.schema import Host

ROSTER = Path(__file__).resolve().parents[2] / "installer" / "bench" / "suites" / "roster.toml"


def _run(monkeypatch, capsys, result: SessionResult) -> tuple[int, dict]:
    monkeypatch.setattr(cli, "_fetch_models", lambda args: [])
    monkeypatch.setattr(cli, "plan", lambda suite, models, store: [])
    monkeypatch.setattr(cli, "_session_host", lambda api, exclusive: Host(hal0_version="1.0.0"))
    monkeypatch.setattr(cli, "run_session", lambda *a, **k: result)
    rc = cli.main(["run", "--suite", str(ROSTER)])
    out = capsys.readouterr().out
    event = json.loads(out[out.index("{") :])
    return rc, event


def test_slot_errors_reach_the_completed_event_and_fail_the_run(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HAL0_BENCH_STATE", str(tmp_path))
    result = SessionResult(suite_id="roster", slot_errors=["failed to restart hal0-slot@2: boom"])

    rc, event = _run(monkeypatch, capsys, result)

    assert event["event"] == "bench.session.completed"
    assert event["slot_errors"] == ["failed to restart hal0-slot@2: boom"]
    assert rc == 1


def test_a_clean_session_still_exits_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HAL0_BENCH_STATE", str(tmp_path))

    rc, event = _run(monkeypatch, capsys, SessionResult(suite_id="roster"))

    assert event["slot_errors"] == []
    assert rc == 0
