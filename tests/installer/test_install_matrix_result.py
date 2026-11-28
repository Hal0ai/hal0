"""The install-matrix result assembler (scripts/install-matrix/result.py)."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RESULT = REPO / "scripts" / "install-matrix" / "result.py"
INSTALL_SH = REPO / "installer" / "install.sh"

PRE = "disk_used_bytes=1000\nnet_rx_bytes=500\n"
POST = (
    "disk_used_bytes=9000\nnet_rx_bytes=2500\nslot_count=3\nslots=agent,brain,memory\n"
    "failed_units=hal0-x.service\ninstall_log=/var/log/hal0/install-1.log\nfailure_report=\n"
)
PROBE = (
    "api_health_s=4.5\ndoctor_ok=20\ndoctor_warn=2\ndoctor_critical=0\ndoctor_exit=0\n"
    "chat_ok=1\nchat_first_token_s=1.25\nchat_model=qwen\nmemory_write_ok=1\nmemory_read_ok=0\n"
    "hermes_reply_ok=1\nhermes_reply_s=7.5\n"
)


def titles() -> list[str]:
    return re.findall(r'^ui_step\s+"([^"]+)"', INSTALL_SH.read_text(), re.MULTILINE)


def assemble(
    tmp_path: Path, env: dict[str, str] | None = None, **files: str | None
) -> tuple[subprocess.CompletedProcess, dict | None]:
    out = tmp_path / "result.json"
    cmd = [
        sys.executable, "-I", str(RESULT), "assemble",
        "--shape", "u2604-cpu-unattended", "--source", "stable", "--vmid", "172",
        "--prereqs", "curl ca-certificates", "--started", "2026-11-28T10:00:00Z",
        "--out", str(out),
    ]  # fmt: skip
    for name in ("pre", "post", "probe", "driver", "summary"):
        content = files.get(name)
        path = tmp_path / f"{name}.in"
        if content is not None:
            path.write_text(content)
        cmd += [f"--{name}", str(path)]  # a path that may not exist
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,  # a red test must fail, never hang
        env={**os.environ, **env} if env else None,
    )
    return proc, (json.loads(out.read_text()) if out.exists() else None)


def full_inputs() -> dict[str, str]:
    t = titles()
    return {
        "pre": PRE,
        "post": POST,
        "probe": PROBE,
        "driver": json.dumps(
            {"exit_code": 0, "questions_answered": 2, "timed_out": False, "idle_timed_out": False}
        ),
        "summary": json.dumps(
            {
                "hal0_version": "1.8.2",
                "elapsed_seconds": 321,
                "warnings": 1,
                "errors": 0,
                "step_durations_seconds": {t[1]: 5, t[0]: 3},
                "components": {"hermes": "selected"},
            }
        ),
    }


def test_assemble_from_fixtures(tmp_path):
    proc, res = assemble(tmp_path, **full_inputs())
    assert proc.returncode == 0, proc.stderr
    assert res["shape"] == "u2604-cpu-unattended"
    assert res["row"] == 1
    assert res["source"] == "stable"
    assert res["hal0_version"] == "1.8.2"
    assert res["started_at"] == "2026-11-28T10:00:00Z"
    assert res["install_exit_code"] == 0
    assert res["timed_out"] is False and res["idle_timed_out"] is False
    assert res["questions_asked"] == 2
    assert res["elapsed_seconds"] == 321
    assert res["disk_used_bytes"] == 8000
    assert res["bytes_downloaded"] == 2000
    assert res["slot_count"] == 3
    assert res["slots"] == ["agent", "brain", "memory"]
    assert res["failed_units"] == ["hal0-x.service"]
    assert res["doctor"] == {"ok": 20, "warn": 2, "critical": 0, "exit": 0}
    assert res["api_health_seconds"] == 4.5
    assert res["chat"] == {"ok": True, "first_token_seconds": 1.25, "model": "qwen"}
    assert res["memory"] == {"write_ok": True, "read_ok": False}
    assert res["hermes"] == {"reply_ok": True, "reply_seconds": 7.5}
    assert res["harness_prereqs"] == ["curl", "ca-certificates"]
    assert res["warnings"] == 1 and res["errors"] == 0
    assert res["components"] == {"hermes": "selected"}


def test_missing_inputs_give_nulls(tmp_path):
    proc, res = assemble(tmp_path)
    assert proc.returncode == 0, proc.stderr
    for key in (
        "hal0_version", "install_exit_code", "timed_out", "idle_timed_out", "questions_asked",
        "elapsed_seconds", "step_durations_seconds", "last_step", "disk_used_bytes",
        "bytes_downloaded", "slot_count", "slots", "doctor", "failed_units",
        "api_health_seconds", "chat", "memory", "hermes", "warnings", "errors",
        "components",
    ):  # fmt: skip
        assert res[key] is None, key
    assert res["harness_prereqs"] == ["curl", "ca-certificates"]


def test_unknown_measurements_are_null(tmp_path):
    files = full_inputs()
    files["probe"] = "api_health_s=-1\nchat_ok=0\nchat_first_token_s=-1\nchat_model=\n"
    _, res = assemble(tmp_path, **files)
    assert res["api_health_seconds"] is None
    assert res["chat"] == {"ok": False, "first_token_seconds": None, "model": None}


def test_last_step_follows_installer_order(tmp_path):
    t = titles()
    assert len(t) >= 5
    files = full_inputs()
    # Hash order puts a LATER step first; the installer's order must win.
    reached = {t[3]: 1, t[0]: 1, t[2]: 1, "Not a step": 9}
    files["summary"] = json.dumps({"step_durations_seconds": reached})
    _, res = assemble(tmp_path, **files)
    assert res["last_step"] == t[3]
    assert res["step_durations_seconds"] == reached


def test_last_step_null_without_known_titles(tmp_path):
    files = full_inputs()
    files["summary"] = json.dumps({"step_durations_seconds": {"Made up": 1}})
    _, res = assemble(tmp_path, **files)
    assert res["last_step"] is None


def test_schema_name(tmp_path):
    _, res = assemble(tmp_path)
    assert res["schema"] == "hal0.install-matrix.v1"


def test_result_file_is_private(tmp_path):
    assemble(tmp_path, **full_inputs())
    mode = stat.S_IMODE((tmp_path / "result.json").stat().st_mode)
    assert mode == 0o600


def test_output_keys_are_exactly_the_documented_set(tmp_path):
    _, res = assemble(tmp_path, **full_inputs())
    assert set(res) == {
        "schema", "shape", "row", "source", "hal0_version", "started_at", "install_exit_code",
        "timed_out", "idle_timed_out", "questions_asked", "elapsed_seconds",
        "step_durations_seconds", "last_step", "disk_used_bytes", "bytes_downloaded",
        "slot_count", "slots", "doctor", "failed_units", "api_health_seconds", "chat", "memory",
        "hermes", "harness_prereqs", "warnings", "errors", "components", "aborted",
        "abort_detail",
    }  # fmt: skip


def run_note(tmp_path: Path, text: str | None) -> dict:
    path = tmp_path / "run.txt"
    if text is not None:
        path.write_text(text)
    out = tmp_path / "result.json"
    cmd = [
        sys.executable, "-I", str(RESULT), "assemble",
        "--shape", "u2604-cpu-unattended", "--source", "stable", "--vmid", "172",
        "--started", "2026-11-28T10:00:00Z", "--out", str(out), "--run", str(path),
    ]  # fmt: skip
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(out.read_text())


def test_run_note_gives_the_abort_reason_and_detail(tmp_path):
    res = run_note(
        tmp_path,
        "vmid=172\naborted=host_memory_low\n"
        "abort_detail=host memory fell below 12 GB (5 GB available)\n",
    )
    assert res["aborted"] == "host_memory_low"
    assert res["abort_detail"] == "host memory fell below 12 GB (5 GB available)"


def test_run_note_without_an_abort_gives_nulls(tmp_path):
    res = run_note(tmp_path, "vmid=172\nuninstall_ok=1\n")
    assert res["aborted"] is None and res["abort_detail"] is None


def test_missing_run_note_or_option_gives_nulls(tmp_path):
    res = run_note(tmp_path, None)
    assert res["aborted"] is None and res["abort_detail"] is None
    _, res = assemble(tmp_path)  # no --run at all
    assert res["aborted"] is None and res["abort_detail"] is None


# --- F4 and F13: inputs are capped, and a result is always written ----------

SMALL = {"HAL0_MATRIX_MAX_INPUT_BYTES": "2000"}


def test_an_input_over_the_cap_is_unreadable_with_one_line_on_stderr(tmp_path):
    big_post = "slot_count=2\n" + "x" * 5000 + "\n"
    big_summary = json.dumps({"hal0_version": "1.8.2", "pad": "y" * 5000})
    proc, res = assemble(tmp_path, env=SMALL, post=big_post, summary=big_summary)
    assert proc.returncode == 0, proc.stderr
    assert res["slot_count"] is None and res["hal0_version"] is None
    lines = [x for x in proc.stderr.splitlines() if x]
    assert len(lines) == 2  # one per unreadable input
    assert all("larger than" in x for x in lines)


def test_an_input_at_the_cap_is_read(tmp_path):
    post = "slot_count=2\n" + "#" * (2000 - len("slot_count=2\n"))
    assert len(post) == 2000
    proc, res = assemble(tmp_path, env=SMALL, post=post)
    assert res["slot_count"] == 2
    assert proc.stderr == ""


@pytest.mark.parametrize("bad", ["", "0", "-1", "8k", "1; id"])
def test_a_bad_input_cap_variable_means_the_default(tmp_path, bad):
    big = "slot_count=2\n" + "x" * 5000
    _, res = assemble(tmp_path, env={"HAL0_MATRIX_MAX_INPUT_BYTES": bad}, post=big)
    assert res["slot_count"] == 2


def test_a_fifo_is_never_read(tmp_path):
    os.mkfifo(tmp_path / "post.in")
    proc, res = assemble(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert res["slot_count"] is None


# --- F13: a result is always written ----------------------------------------


def test_deeply_nested_summary_still_gives_a_result(tmp_path):
    proc, res = assemble(tmp_path, summary="[" * 200000)
    assert proc.returncode == 0, proc.stderr
    assert res is not None and res["hal0_version"] is None
    assert res["schema"] == "hal0.install-matrix.v1"


def test_nested_values_that_load_do_not_stop_the_result(tmp_path):
    deep = '{"components": ' + "[" * 900 + "]" * 900 + ', "warnings": {"a": 1}, "hal0_version": 7}'
    proc, res = assemble(tmp_path, summary=deep)
    assert proc.returncode == 0, proc.stderr
    assert res is not None
    assert res["components"] is None and res["warnings"] is None and res["hal0_version"] is None


def test_the_result_is_written_whatever_the_inputs_are(tmp_path):
    proc, res = assemble(
        tmp_path,
        pre="\x00\xff=1\n=\n",
        post="slot_count=\x00\n",
        probe="doctor_ok=" + "9" * 400 + "\nchat_first_token_s=1" + "9" * 400 + "\n",
        driver='{"exit_code": {"a": [1]}}',
        summary='{"step_durations_seconds": {"A": {"b": 1}}, "errors": [1], "elapsed_seconds": 1e999}',
    )
    assert proc.returncode == 0, proc.stderr
    assert res is not None
    assert res["step_durations_seconds"] is None and res["errors"] is None
    assert res["install_exit_code"] is None and res["elapsed_seconds"] is None
    assert res["chat"]["first_token_seconds"] is None  # inf is not a measurement
