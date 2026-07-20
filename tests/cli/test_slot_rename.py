"""Tests for ``hal0 slot rename`` (§5.2 R5 sync assessment).

Zero CLI verb existed for the rename route added in rework §11.1
(``POST /api/slots/{name}/rename``) even though the id-stability rework
made rename a supported, everyday operation.
"""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from hal0.cli import slot_commands

runner = CliRunner()


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_unreachable(_url: str) -> bool:
        return False

    def fake_post(path: str, *, json: dict[str, Any] | None = None, **_kw: Any) -> dict[str, Any]:
        captured["method"] = "POST"
        captured["path"] = path
        captured["body"] = json or {}
        return {"name": (json or {}).get("new_name"), "state": "offline"}

    monkeypatch.setattr(slot_commands, "_api_unreachable", fake_unreachable)
    monkeypatch.setattr(slot_commands, "api_post", fake_post)
    return captured


def test_slot_rename_hits_post_rename(captured: dict[str, Any]) -> None:
    result = runner.invoke(slot_commands.app, ["rename", "primary", "chat-main"])
    assert result.exit_code == 0, result.output
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/slots/primary/rename"
    assert captured["body"] == {"new_name": "chat-main"}
    assert "chat-main" in result.output


def test_slot_rename_is_registered() -> None:
    result = runner.invoke(slot_commands.app, ["--help"])
    assert result.exit_code == 0, result.output
    assert "rename" in result.output
