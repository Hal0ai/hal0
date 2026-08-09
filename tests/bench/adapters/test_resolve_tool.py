"""resolve_tool: PATH first, then the interpreter's own bin dir (venv
console scripts are invisible to the service PATH — found on-box 2026-08-09)."""

from __future__ import annotations

import os
import stat
import sys

from hal0.bench import adapters


def test_path_hit_wins(tmp_path, monkeypatch):
    tool = tmp_path / "sometool"
    tool.write_text("#!/bin/sh\n")
    tool.chmod(tool.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert adapters.resolve_tool("sometool") == str(tool)


def test_venv_bin_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))  # empty dir: no PATH hit
    bindir = tmp_path / "venv-bin"
    bindir.mkdir()
    tool = bindir / "venvtool"
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o755)
    monkeypatch.setattr(sys, "executable", str(bindir / "python3"))
    assert adapters.resolve_tool("venvtool") == str(tool)


def test_missing_everywhere_is_none(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "nowhere" / "python3"))
    assert adapters.resolve_tool("ghost-tool") is None


def test_non_executable_venv_candidate_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    bindir = tmp_path / "vb"
    bindir.mkdir()
    (bindir / "plainfile").write_text("data")
    monkeypatch.setattr(sys, "executable", str(bindir / "python3"))
    if os.access(bindir / "plainfile", os.X_OK):  # exotic umask boxes
        (bindir / "plainfile").chmod(0o644)
    assert adapters.resolve_tool("plainfile") is None
