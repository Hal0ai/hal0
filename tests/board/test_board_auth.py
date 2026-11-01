"""Tests for the Hermes dashboard token resolver — src/hal0/board/auth.py.

Run targeted:
    PYTHONPATH=src .venv/bin/python -m pytest tests/board/test_board_auth.py -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.board import BoardNotInstalled, BoardNoToken
from hal0.board.auth import hermes_installed, read_dashboard_token


class _Manager:
    def __init__(self, names: list[str]) -> None:
        self._names = names

    def installed_names(self) -> list[str]:
        return self._names


def test_error_classes_codes() -> None:
    assert (BoardNoToken.code, BoardNoToken.status) == ("board.no_token", 503)
    assert (BoardNotInstalled.code, BoardNotInstalled.status) == ("board.not_installed", 503)


def test_read_token_strips_newline(tmp_path: Path) -> None:
    f = tmp_path / "tok"
    f.write_text("abc\n")
    assert read_dashboard_token(f) == "abc"


def test_read_token_missing_raises_no_token(tmp_path: Path) -> None:
    f = tmp_path / "absent"
    with pytest.raises(BoardNoToken) as exc:
        read_dashboard_token(f)
    assert exc.value.code == "board.no_token"
    assert exc.value.details["path"] == str(f)


def test_read_token_blank_raises_no_token(tmp_path: Path) -> None:
    f = tmp_path / "tok"
    f.write_text("  \n")
    with pytest.raises(BoardNoToken):
        read_dashboard_token(f)


def test_read_token_unreadable_raises_no_token(tmp_path: Path) -> None:
    # A directory at the path makes read_text raise OSError.
    with pytest.raises(BoardNoToken) as exc:
        read_dashboard_token(tmp_path)
    assert exc.value.details["path"] == str(tmp_path)


def test_read_token_binary_raises_no_token(tmp_path: Path) -> None:
    f = tmp_path / "tok"
    f.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(BoardNoToken) as exc:
        read_dashboard_token(f)
    assert exc.value.details["path"] == str(f)


def test_hermes_installed_env_override() -> None:
    env = {"HERMES_DASHBOARD_BASE_URL": "http://127.0.0.1:9119"}
    assert hermes_installed(env=env, manager_factory=lambda: _Manager([])) is True


def test_hermes_installed_blank_env_ignored() -> None:
    env = {"HERMES_DASHBOARD_BASE_URL": "  "}
    assert hermes_installed(env=env, manager_factory=lambda: _Manager([])) is False


def test_hermes_installed_from_manager() -> None:
    assert hermes_installed(env={}, manager_factory=lambda: _Manager(["hermes"])) is True
    assert hermes_installed(env={}, manager_factory=lambda: _Manager(["pi-coder"])) is False
