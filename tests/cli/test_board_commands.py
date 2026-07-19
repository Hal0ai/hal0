"""Tests for ``hal0 board {list,show,add,move}`` (§5.2 R5 sync assessment).

A thin operator-facing slice of ``/api/board/*`` — the R3/R4 board surface
the CLI previously had zero verbs for. Full CRUD stays dashboard-only;
these four cover the "look at the board, add a card, move a card" loop
from a terminal.
"""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from hal0.cli import board_commands

runner = CliRunner()


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_unreachable(_url: str) -> bool:
        return False

    def fake_get(path: str, **kw: Any) -> dict[str, Any]:
        captured["method"] = "GET"
        captured["path"] = path
        captured["kw"] = kw
        if path == "/api/board/board":
            return {"lanes": {"triage": [{"id": "t1", "title": "fix it", "assignee": "steward"}]}}
        if path.startswith("/api/board/tasks/"):
            return {"id": "t1", "title": "fix it", "status": "triage"}
        return {}

    def fake_post(path: str, *, json: dict[str, Any] | None = None, **kw: Any) -> dict[str, Any]:
        captured["method"] = "POST"
        captured["path"] = path
        captured["body"] = json or {}
        captured["kw"] = kw
        return {"task": {"id": "new-1", "title": (json or {}).get("title")}}

    def fake_patch(path: str, *, json: dict[str, Any] | None = None, **_kw: Any) -> dict[str, Any]:
        captured["method"] = "PATCH"
        captured["path"] = path
        captured["body"] = json or {}
        return {"id": "t1", "status": (json or {}).get("status")}

    monkeypatch.setattr(board_commands, "_api_unreachable", fake_unreachable)
    monkeypatch.setattr(board_commands, "api_get", fake_get)
    monkeypatch.setattr(board_commands, "api_post", fake_post)
    monkeypatch.setattr(board_commands, "api_patch", fake_patch)
    return captured


def test_board_list_hits_get_board(captured: dict[str, Any]) -> None:
    result = runner.invoke(board_commands.app, ["list"])
    assert result.exit_code == 0, result.output
    assert captured["path"] == "/api/board/board"
    assert "fix it" in result.output


def test_board_list_passes_board_and_archived_params(captured: dict[str, Any]) -> None:
    result = runner.invoke(board_commands.app, ["list", "--board", "ops", "--include-archived"])
    assert result.exit_code == 0, result.output
    params = captured["kw"]["params"]
    assert params["board"] == "ops"
    assert params["include_archived"] == "true"


def test_board_show_hits_get_task(captured: dict[str, Any]) -> None:
    result = runner.invoke(board_commands.app, ["show", "t1"])
    assert result.exit_code == 0, result.output
    assert captured["path"] == "/api/board/tasks/t1"


def test_board_add_hits_post_tasks(captured: dict[str, Any]) -> None:
    result = runner.invoke(board_commands.app, ["add", "fix the thing", "--assignee", "steward"])
    assert result.exit_code == 0, result.output
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/board/tasks"
    assert captured["body"]["title"] == "fix the thing"
    assert captured["body"]["assignee"] == "steward"
    assert captured["body"]["status"] == "triage"
    assert "new-1" in result.output


def test_board_move_hits_patch_tasks(captured: dict[str, Any]) -> None:
    result = runner.invoke(board_commands.app, ["move", "t1", "running"])
    assert result.exit_code == 0, result.output
    assert captured["method"] == "PATCH"
    assert captured["path"] == "/api/board/tasks/t1"
    assert captured["body"] == {"status": "running"}


def test_board_is_registered_on_main_app() -> None:
    from hal0.cli.main import app as main_app

    result = runner.invoke(main_app, ["board", "--help"])
    assert result.exit_code == 0, result.output
    assert "list" in result.output
    assert "show" in result.output
    assert "add" in result.output
    assert "move" in result.output
