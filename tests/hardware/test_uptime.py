"""Direct tests for :mod:`hal0.hardware.uptime` (#1905).

The API-route tests monkeypatch ``read_uptime_s`` away, so the
parse/failure branches need their own coverage here. ``probe._read_uptime_s``
delegates to this module, so this is the single seam for both callers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.hardware import uptime as uptime_mod


def _patch_path(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    monkeypatch.setattr(uptime_mod, "_UPTIME_PATH", path)


def test_parses_first_float_and_floors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "uptime"
    f.write_text("326305.91 1234567.00\n")
    _patch_path(monkeypatch, f)
    assert uptime_mod.read_uptime_s() == 326305


def test_missing_file_returns_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_path(monkeypatch, tmp_path / "does-not-exist")
    assert uptime_mod.read_uptime_s() == 0


def test_garbage_content_returns_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "uptime"
    f.write_text("not-a-number stuff\n")
    _patch_path(monkeypatch, f)
    assert uptime_mod.read_uptime_s() == 0


def test_empty_file_returns_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "uptime"
    f.write_text("")
    _patch_path(monkeypatch, f)
    assert uptime_mod.read_uptime_s() == 0
