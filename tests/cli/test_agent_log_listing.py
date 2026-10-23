"""``hal0 agent log``: which provision logs the default listing prints."""

from __future__ import annotations

from pathlib import Path

from hal0.cli.agent_commands import _provision_log_files


def _logs(tmp_path: Path) -> Path:
    for name in ("install.log", "config_write.log", "job-2000000000aa.log", "job-3000000000bb.log"):
        (tmp_path / name).write_text(name)
    (tmp_path / "notes.txt").write_text("not a log")
    return tmp_path


def test_the_default_listing_leaves_out_the_api_started_repair_transcripts(tmp_path: Path) -> None:
    names = [p.name for p in _provision_log_files(_logs(tmp_path), None)]

    assert names == ["config_write.log", "install.log"]


def test_a_phase_still_names_its_own_log(tmp_path: Path) -> None:
    logs = _logs(tmp_path)

    assert [p.name for p in _provision_log_files(logs, "install")] == ["install.log"]
    assert [p.name for p in _provision_log_files(logs, "job-2000000000aa")] == [
        "job-2000000000aa.log"
    ]
    assert _provision_log_files(logs, "missing") == []
