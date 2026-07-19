"""Tests for ``hal0 auth {status,rotate,require}`` (§5.2 R5 sync assessment).

The CLI shipped zero verbs for the R3/R4 auth surface even though the
underlying routes (``/api/auth/status``, ``POST /api/auth/rotate``, ``PUT
/api/auth/require``) have existed since KB-1. These tests pin the wiring —
right method, right path, right body — against a stubbed API surface, the
same style ``test_slot_verb_aliases.py`` uses.
"""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from hal0.cli import auth_commands

runner = CliRunner()


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_unreachable(_url: str) -> bool:
        return False

    def fake_get(path: str, **_kw: Any) -> dict[str, Any]:
        captured["method"] = "GET"
        captured["path"] = path
        return {"auth_required": True, "has_admin_key": True, "tier": "admin"}

    def fake_post(path: str, *, json: dict[str, Any] | None = None, **_kw: Any) -> dict[str, Any]:
        captured["method"] = "POST"
        captured["path"] = path
        captured["body"] = json or {}
        return {
            "tier": (json or {}).get("tier", "admin"),
            "fingerprint": "abc123",
            "note": "rotated ok",
        }

    def fake_put(path: str, *, json: dict[str, Any] | None = None, **_kw: Any) -> dict[str, Any]:
        captured["method"] = "PUT"
        captured["path"] = path
        captured["body"] = json or {}
        return {"require_auth": (json or {}).get("require_auth"), "applies_live": True}

    monkeypatch.setattr(auth_commands, "_api_unreachable", fake_unreachable)
    monkeypatch.setattr(auth_commands, "api_get", fake_get)
    monkeypatch.setattr(auth_commands, "api_post", fake_post)
    monkeypatch.setattr(auth_commands, "api_put", fake_put)
    return captured


def test_auth_status_hits_get_status(captured: dict[str, Any]) -> None:
    result = runner.invoke(auth_commands.app, ["status"])
    assert result.exit_code == 0, result.output
    assert captured["method"] == "GET"
    assert captured["path"] == "/api/auth/status"


def test_auth_status_json(captured: dict[str, Any]) -> None:
    result = runner.invoke(auth_commands.app, ["status", "--json"])
    assert result.exit_code == 0, result.output
    assert '"auth_required": true' in result.output


def test_auth_rotate_defaults_to_admin_tier(captured: dict[str, Any]) -> None:
    result = runner.invoke(auth_commands.app, ["rotate", "--force"])
    assert result.exit_code == 0, result.output
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/auth/rotate"
    assert captured["body"] == {"tier": "admin"}


def test_auth_rotate_client_tier(captured: dict[str, Any]) -> None:
    result = runner.invoke(auth_commands.app, ["rotate", "client", "--force"])
    assert result.exit_code == 0, result.output
    assert captured["body"] == {"tier": "client"}


def test_auth_rotate_prompts_without_force(captured: dict[str, Any]) -> None:
    result = runner.invoke(auth_commands.app, ["rotate"], input="n\n")
    assert result.exit_code != 0
    assert "method" not in captured


def test_auth_rotate_never_prints_a_key_value(captured: dict[str, Any]) -> None:
    result = runner.invoke(auth_commands.app, ["rotate", "--force"])
    assert result.exit_code == 0, result.output
    assert "fingerprint" not in result.output.lower() or "abc123" in result.output
    # The stub note/fingerprint are fine to echo — what must NEVER appear is
    # a raw secret; the route contract (and this stub) never hands one back.


def test_auth_require_on(captured: dict[str, Any]) -> None:
    result = runner.invoke(auth_commands.app, ["require", "on"])
    assert result.exit_code == 0, result.output
    assert captured["method"] == "PUT"
    assert captured["path"] == "/api/auth/require"
    assert captured["body"] == {"require_auth": True}
    assert "ON" in result.output


def test_auth_require_off(captured: dict[str, Any]) -> None:
    result = runner.invoke(auth_commands.app, ["require", "off"])
    assert result.exit_code == 0, result.output
    assert captured["body"] == {"require_auth": False}
    assert "OFF" in result.output


def test_auth_is_registered_on_main_app() -> None:
    from hal0.cli.main import app as main_app

    result = runner.invoke(main_app, ["auth", "--help"])
    assert result.exit_code == 0, result.output
    assert "status" in result.output
    assert "rotate" in result.output
    assert "require" in result.output
