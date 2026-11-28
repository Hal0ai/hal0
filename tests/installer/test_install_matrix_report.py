"""The install-matrix report (scripts/install-matrix/report.py)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
REPORT = REPO / "scripts" / "install-matrix" / "report.py"


def make_result(shape: str, started: str, **over) -> dict:
    res = {
        "schema": "hal0.install-matrix.v1",
        "shape": shape,
        "row": None,
        "source": "stable",
        "hal0_version": "1.8.2",
        "started_at": started,
        "install_exit_code": 0,
        "timed_out": False,
        "idle_timed_out": False,
        "questions_asked": 2,
        "elapsed_seconds": 321,
        "step_durations_seconds": {"Fetch": 10, "Install runners": 200.5, "Doctor": 3},
        "last_step": "Doctor",
        "disk_used_bytes": 12_345_000_000,
        "bytes_downloaded": 5_000_000_000,
        "slot_count": 3,
        "slots": ["agent", "brain", "memory"],
        "doctor": {"ok": 20, "warn": 2, "critical": 0, "exit": 0},
        "failed_units": [],
        "api_health_seconds": 4.5,
        "chat": {"ok": True, "first_token_seconds": 1.25, "model": "qwen"},
        "memory": {"write_ok": True, "read_ok": False},
        "hermes": {"reply_ok": True, "reply_seconds": 7.5},
    }
    res.update(over)
    return res


def put(results: Path, name: str, payload: dict | str) -> None:
    d = results / name
    d.mkdir(parents=True)
    body = payload if isinstance(payload, str) else json.dumps(payload)
    (d / "result.json").write_text(body)


def run(results: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-I", str(REPORT), str(results), *extra],
        capture_output=True, text=True, check=False,
    )  # fmt: skip


def table_rows(out: str) -> list[str]:
    lines = out.split("\n\n")[0].splitlines()
    return [ln for ln in lines[2:] if ln.startswith("|")]


def test_table_has_one_row_per_result(tmp_path):
    put(
        tmp_path,
        "20261128T100000Z-u2604-cpu-terminal",
        make_result("u2604-cpu-terminal", "2026-11-28T10:00:00Z"),
    )
    put(tmp_path, "20261128T110000Z-u2604-gpu", make_result("u2604-gpu", "2026-11-28T11:00:00Z"))
    proc = run(tmp_path)
    assert proc.returncode == 0, proc.stderr
    rows = table_rows(proc.stdout)
    assert len(rows) == 2
    gpu = next(r for r in rows if "u2604-gpu" in r)
    for cell in (
        "stable 1.8.2",
        "5:21",
        "Install runners 200.5s",
        "12.3",
        "5.0",
        "20/2/0",
        "4.5",
        "1.25s qwen",
        "ok/fail",
        "7.5",
    ):
        assert cell in gpu, cell


def test_rows_in_shape_order(tmp_path):
    # Written newest-first and in reverse row order; rows 3, 1, 2 plus an unknown shape.
    put(tmp_path, "a", make_result("u2604-gpu", "2026-11-28T09:00:00Z"))
    put(tmp_path, "b", make_result("mystery-shape", "2026-11-28T09:30:00Z"))
    put(tmp_path, "c", make_result("u2604-cpu-terminal", "2026-11-28T10:00:00Z"))
    put(tmp_path, "d", make_result("u2604-cpu-unattended", "2026-11-28T11:00:00Z"))
    proc = run(tmp_path)
    order = [r.split("|")[1].strip() for r in table_rows(proc.stdout)]
    assert order == ["u2604-cpu-unattended", "u2604-cpu-terminal", "u2604-gpu", "mystery-shape"]


def test_latest_per_shape_keeps_newest(tmp_path):
    put(
        tmp_path,
        "z-old-name",
        make_result("u2604-gpu", "2026-11-28T12:00:00Z", hal0_version="1.9.0"),
    )
    put(
        tmp_path,
        "a-new-name",
        make_result("u2604-gpu", "2026-11-28T08:00:00Z", hal0_version="1.8.0"),
    )
    both = run(tmp_path)
    assert len(table_rows(both.stdout)) == 2
    latest = run(tmp_path, "--latest-per-shape")
    rows = table_rows(latest.stdout)
    assert len(rows) == 1
    assert "1.9.0" in rows[0] and "1.8.0" not in rows[0]


def test_missing_values_print_dash(tmp_path):
    bare = make_result(
        "u2604-gpu", "2026-11-28T10:00:00Z", hal0_version=None, install_exit_code=None,
        elapsed_seconds=None, step_durations_seconds=None, disk_used_bytes=None,
        bytes_downloaded=None, questions_asked=None, slot_count=None, doctor=None,
        api_health_seconds=None, chat=None, memory=None, hermes=None,
    )  # fmt: skip
    put(tmp_path, "x", bare)
    put(tmp_path, "bad-json", "{not json")
    put(tmp_path, "bad-schema", {"schema": "other.v9", "shape": "u2604-gpu"})
    proc = run(tmp_path)
    assert proc.returncode == 0
    rows = table_rows(proc.stdout)
    assert len(rows) == 1
    cells = [c.strip() for c in rows[0].strip("|").split("|")]
    assert cells[0] == "u2604-gpu"
    assert cells[1:].count("-") >= 12
    assert proc.stderr.count("\n") == 2  # one line per skipped file


def test_failed_run_listed_with_last_step(tmp_path):
    put(tmp_path, "ok", make_result("u2604-cpu-terminal", "2026-11-28T10:00:00Z"))
    put(
        tmp_path, "bad",
        make_result(
            "u2604-gpu", "2026-11-28T11:00:00Z", install_exit_code=1, last_step="Install runners",
            failed_units=["hal0-api.service", "hal0-x.service"],
        ),
    )  # fmt: skip
    proc = run(tmp_path)
    failures = proc.stdout.split("\n\n", 1)[1]
    assert "u2604-gpu" in failures
    assert "Install runners" in failures
    assert "hal0-api.service" in failures
    assert "hal0-x.service" not in failures
    assert "u2604-cpu-terminal" not in failures


def test_null_exit_code_listed_as_failed(tmp_path):
    put(
        tmp_path, "crashed",
        make_result(
            "u2604-gpu", "2026-11-28T11:00:00Z", install_exit_code=None, timed_out=None,
            idle_timed_out=None, last_step="Install runners", failed_units=["hal0-api.service"],
        ),
    )  # fmt: skip
    failures = run(tmp_path).stdout.split("\n\n", 1)[1]
    assert "u2604-gpu" in failures
    assert "no driver result" in failures
    assert "Install runners" in failures
    assert "hal0-api.service" in failures


def test_exit_zero_with_failed_units_listed(tmp_path):
    put(
        tmp_path,
        "units",
        make_result("u2604-gpu", "2026-11-28T11:00:00Z", failed_units=["hal0-x.service"]),
    )
    put(tmp_path, "clean", make_result("u2604-cpu-terminal", "2026-11-28T10:00:00Z"))
    failures = run(tmp_path).stdout.split("\n\n", 1)[1]
    assert "failed units" in failures
    assert "hal0-x.service" in failures
    assert "u2604-cpu-terminal" not in failures
