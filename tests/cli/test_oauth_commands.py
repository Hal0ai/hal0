"""Tests for ``hal0 oauth`` command request shapes and output."""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from hal0.cli import oauth_commands

runner = CliRunner()


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    calls: dict[str, Any] = {"gets": [], "posts": [], "deletes": []}
    monkeypatch.setattr(oauth_commands, "_api_unreachable", lambda _url: False)

    def fake_get(path: str, **_kw: Any) -> Any:
        calls["gets"].append(path)
        return calls.get("get_response", {})

    def fake_post(path: str, *, json: dict[str, Any] | None = None, **_kw: Any) -> Any:
        calls["posts"].append((path, json))
        return calls.get("post_response", {})

    def fake_delete(path: str, **_kw: Any) -> Any:
        calls["deletes"].append(path)
        return {}

    monkeypatch.setattr(oauth_commands, "api_get", fake_get)
    monkeypatch.setattr(oauth_commands, "api_post", fake_post)
    monkeypatch.setattr(oauth_commands, "api_delete", fake_delete)
    return calls


def test_list_hits_providers_endpoint(api: dict[str, Any]) -> None:
    api["get_response"] = {
        "providers": [
            {
                "id": "google",
                "skill_id": "google-workspace",
                "configured": True,
                "connected": False,
                "expires_at": None,
                "expired": None,
            }
        ]
    }
    result = runner.invoke(oauth_commands.app, ["list"])
    assert result.exit_code == 0, result.output
    assert api["gets"] == ["/api/oauth/providers"]
    assert "google" in result.output


def test_list_json_outputs_raw_providers(api: dict[str, Any]) -> None:
    api["get_response"] = {"providers": [{"id": "spotify"}]}
    result = runner.invoke(oauth_commands.app, ["list", "--json"])
    assert result.exit_code == 0, result.output
    assert '"id": "spotify"' in result.output


def test_connect_prints_authorize_url(api: dict[str, Any]) -> None:
    api["post_response"] = {"authorize_url": "https://accounts.google.com/auth?state=abc", "state": "abc"}
    result = runner.invoke(oauth_commands.app, ["connect", "google"])
    assert result.exit_code == 0, result.output
    assert api["posts"] == [("/api/oauth/google/start", None)]
    assert "https://accounts.google.com/auth?state=abc" in result.output


def test_disconnect_requires_confirmation_without_force(api: dict[str, Any]) -> None:
    result = runner.invoke(oauth_commands.app, ["disconnect", "google"], input="n\n")
    assert result.exit_code == 0
    assert api["deletes"] == []


def test_disconnect_force_skips_confirmation(api: dict[str, Any]) -> None:
    result = runner.invoke(oauth_commands.app, ["disconnect", "google", "--force"])
    assert result.exit_code == 0, result.output
    assert api["deletes"] == ["/api/oauth/google"]


def test_status_reports_connected(api: dict[str, Any]) -> None:
    api["get_response"] = {"connected": True, "expired": False}
    result = runner.invoke(oauth_commands.app, ["status", "google"])
    assert result.exit_code == 0, result.output
    assert "connected" in result.output.lower()


def test_status_reports_not_connected(api: dict[str, Any]) -> None:
    api["get_response"] = {"connected": False}
    result = runner.invoke(oauth_commands.app, ["status", "google"])
    assert result.exit_code == 0, result.output
    assert "not connected" in result.output.lower()


def test_set_client_secret_sends_value_field(api: dict[str, Any]) -> None:
    result = runner.invoke(oauth_commands.app, ["set-client-secret", "github", "--value", "s3cr3t"])
    assert result.exit_code == 0, result.output
    assert api["posts"] == [("/api/oauth/github/client-secret", {"value": "s3cr3t"})]
    assert "s3cr3t" not in result.output
