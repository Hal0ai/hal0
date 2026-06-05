"""Tests for the deprecated ``slot add`` / ``slot remove`` verb aliases (#503).

Docs (and older muscle memory) used ``hal0 slot add`` / ``hal0 slot
remove`` while the real commands are ``slot create`` / ``slot delete``.
To resolve the verb drift without a breaking rename we keep the canonical
verbs and add thin deprecated aliases that:

  1. print a one-line deprecation notice to stderr, and
  2. delegate to the same underlying implementation (no duplicated logic).

These tests assert both halves: the notice lands on stderr (so stdout
stays parseable) and the underlying API call is identical to the
canonical verb's.
"""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from hal0.cli import slot_commands

runner = CliRunner()


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub the API surface and capture the method + path + body."""
    captured: dict[str, Any] = {}

    def fake_unreachable(_url: str) -> bool:
        return False

    def fake_get(_path: str, **_kw: Any) -> list[dict[str, Any]]:
        return []

    def fake_post(path: str, *, json: dict[str, Any] | None = None, **_kw: Any) -> dict[str, Any]:
        captured["method"] = "POST"
        captured["path"] = path
        captured["body"] = json or {}
        return {"port": (json or {}).get("port", 8081)}

    def fake_delete(path: str, **_kw: Any) -> dict[str, Any]:
        captured["method"] = "DELETE"
        captured["path"] = path
        return {}

    monkeypatch.setattr(slot_commands, "_api_unreachable", fake_unreachable)
    monkeypatch.setattr(slot_commands, "api_get", fake_get)
    monkeypatch.setattr(slot_commands, "api_post", fake_post)
    monkeypatch.setattr(slot_commands, "api_delete", fake_delete)
    monkeypatch.setattr(slot_commands, "_detect_default_hardware", lambda: "vulkan")
    return captured


def test_slot_add_is_registered_as_deprecated_alias() -> None:
    """``slot add`` exists and its help flags it as deprecated for ``slot create``."""
    import re

    result = runner.invoke(slot_commands.app, ["add", "--help"])
    assert result.exit_code == 0, result.output
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output).lower()
    assert "deprecat" in plain
    assert "create" in plain


def test_slot_remove_is_registered_as_deprecated_alias() -> None:
    """``slot remove`` exists and its help flags it as deprecated for ``slot delete``."""
    import re

    result = runner.invoke(slot_commands.app, ["remove", "--help"])
    assert result.exit_code == 0, result.output
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output).lower()
    assert "deprecat" in plain
    assert "delete" in plain


def test_slot_add_delegates_to_create(captured: dict[str, Any]) -> None:
    """``slot add`` posts the same body ``slot create`` would (delegation)."""
    result = runner.invoke(
        slot_commands.app,
        ["add", "primary", "--provider", "llama-server", "--model", "demo"],
    )
    assert result.exit_code == 0, result.output
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/slots"
    assert captured["body"]["name"] == "primary"
    assert captured["body"]["provider"] == "llama-server"
    # The deprecation notice goes to stderr so stdout stays parseable.
    assert "deprecat" in result.stderr.lower()
    assert "slot create" in result.stderr.lower()


def test_slot_remove_delegates_to_delete(captured: dict[str, Any]) -> None:
    """``slot remove`` issues the same DELETE ``slot delete`` would (delegation)."""
    result = runner.invoke(slot_commands.app, ["remove", "primary", "--force"])
    assert result.exit_code == 0, result.output
    assert captured["method"] == "DELETE"
    assert captured["path"] == "/api/slots/primary"
    assert "deprecat" in result.stderr.lower()
    assert "slot delete" in result.stderr.lower()
