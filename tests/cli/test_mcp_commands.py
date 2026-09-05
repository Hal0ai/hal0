"""Tests for the ADR-0015 ``hal0 mcp`` verbs: test/allow/gate/block/expose/add/remove.

Pins each command's request shape (path, method, body) against the API
surface added in :mod:`hal0.api.routes.mcp` — a mismatched field name
would 400/422 against the live server and only be caught here.
"""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from hal0.cli import mcp_commands

runner = CliRunner()


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub the API client surface and capture calls."""
    calls: dict[str, Any] = {"gets": [], "posts": [], "patches": [], "deletes": []}

    monkeypatch.setattr(mcp_commands, "_api_unreachable", lambda _url: False)

    def fake_get(path: str, **_kw: Any) -> Any:
        calls["gets"].append(path)
        return calls.get("get_response", {"servers": []})

    def fake_post(path: str, *, json: dict[str, Any] | None = None, **_kw: Any) -> Any:
        calls["posts"].append((path, json))
        return calls.get("post_response", {"ok": True})

    def fake_patch(path: str, *, json: dict[str, Any] | None = None, **_kw: Any) -> Any:
        calls["patches"].append((path, json))
        return calls.get("patch_response", {"server": {}, "hermes_sync": {"errors": []}})

    def fake_delete(path: str, **_kw: Any) -> Any:
        calls["deletes"].append(path)
        return calls.get("delete_response", {"uninstalled": "x"})

    monkeypatch.setattr(mcp_commands, "api_get", fake_get)
    monkeypatch.setattr(mcp_commands, "api_post", fake_post)
    monkeypatch.setattr(mcp_commands, "api_patch", fake_patch)
    monkeypatch.setattr(mcp_commands, "api_delete", fake_delete)
    return calls


# ── `hal0 mcp test` ───────────────────────────────────────────────────────────


def test_test_cmd_posts_to_test_endpoint(api: dict[str, Any]) -> None:
    api["post_response"] = {
        "server_id": "github",
        "probe": {"ok": True, "tools": ["search_repositories", "delete_repository"], "error": None},
        "verdicts": {"search_repositories": "allow", "delete_repository": "blocked"},
    }
    result = runner.invoke(mcp_commands.app, ["test", "github"])
    assert result.exit_code == 0, result.output
    assert api["posts"] == [("/api/mcp/github/test", None)]
    assert "search_repositories" in result.output
    assert "delete_repository" in result.output


def test_test_cmd_reports_unreachable(api: dict[str, Any]) -> None:
    api["post_response"] = {"probe": {"ok": False, "error": "connection refused"}}
    result = runner.invoke(mcp_commands.app, ["test", "github"])
    assert result.exit_code == 0, result.output
    assert "unreachable" in result.output
    assert "connection refused" in result.output


def test_test_cmd_json_out(api: dict[str, Any]) -> None:
    api["post_response"] = {"probe": {"ok": True, "tools": []}, "verdicts": {}}
    result = runner.invoke(mcp_commands.app, ["test", "github", "--json"])
    assert result.exit_code == 0, result.output
    assert '"probe"' in result.output


# ── `hal0 mcp allow|gate|block` ───────────────────────────────────────────────


def test_allow_moves_tool_and_patches_tools(api: dict[str, Any]) -> None:
    api["get_response"] = {
        "servers": [
            {
                "id": "github",
                "tools_policy": {
                    "allow": [],
                    "gated": ["create_pull_request"],
                    "blocked": ["delete_repository"],
                },
            }
        ]
    }
    result = runner.invoke(mcp_commands.app, ["allow", "github", "create_pull_request"])
    assert result.exit_code == 0, result.output
    assert len(api["patches"]) == 1
    path, body = api["patches"][0]
    assert path == "/api/mcp/github/tools"
    assert body == {"allow": ["create_pull_request"], "gated": [], "blocked": ["delete_repository"]}


def test_gate_moves_tool(api: dict[str, Any]) -> None:
    api["get_response"] = {
        "servers": [
            {"id": "github", "tools_policy": {"allow": ["search"], "gated": [], "blocked": []}}
        ]
    }
    result = runner.invoke(mcp_commands.app, ["gate", "github", "search"])
    assert result.exit_code == 0, result.output
    _path, body = api["patches"][0]
    assert body == {"allow": [], "gated": ["search"], "blocked": []}


def test_block_moves_tool(api: dict[str, Any]) -> None:
    api["get_response"] = {
        "servers": [
            {"id": "github", "tools_policy": {"allow": ["danger"], "gated": [], "blocked": []}}
        ]
    }
    result = runner.invoke(mcp_commands.app, ["block", "github", "danger"])
    assert result.exit_code == 0, result.output
    _path, body = api["patches"][0]
    assert body == {"allow": [], "gated": [], "blocked": ["danger"]}


def test_allow_unknown_server_dies(api: dict[str, Any]) -> None:
    api["get_response"] = {"servers": []}
    result = runner.invoke(mcp_commands.app, ["allow", "nope", "tool"])
    assert result.exit_code != 0
    assert not api["patches"]


# ── `hal0 mcp expose` ─────────────────────────────────────────────────────────


def test_expose_hermes_patches_exposure(api: dict[str, Any]) -> None:
    api["patch_response"] = {
        "server": {"exposure": {"hermes": True, "brain": False}},
        "hermes_sync": {"errors": []},
    }
    result = runner.invoke(mcp_commands.app, ["expose", "github", "--hermes"])
    assert result.exit_code == 0, result.output
    path, body = api["patches"][0]
    assert path == "/api/mcp/github/exposure"
    assert body == {"hermes": True}


def test_expose_both_flags(api: dict[str, Any]) -> None:
    api["patch_response"] = {
        "server": {"exposure": {"hermes": True, "brain": True}},
        "hermes_sync": {"errors": []},
    }
    result = runner.invoke(mcp_commands.app, ["expose", "github", "--hermes", "--brain"])
    assert result.exit_code == 0, result.output
    _path, body = api["patches"][0]
    assert body == {"hermes": True, "brain": True}


def test_expose_no_flags_dies(api: dict[str, Any]) -> None:
    result = runner.invoke(mcp_commands.app, ["expose", "github"])
    assert result.exit_code != 0
    assert not api["patches"]


# ── `hal0 mcp add` / `hal0 mcp remove` (aliases) ─────────────────────────────


def test_add_is_install_alias(api: dict[str, Any]) -> None:
    api["post_response"] = {"installed": {"id": "github", "name": "GitHub", "tools": 3}}
    result = runner.invoke(mcp_commands.app, ["add", "npm:@modelcontextprotocol/server-github"])
    assert result.exit_code == 0, result.output
    assert api["posts"] == [
        ("/api/mcp/install", {"url": "npm:@modelcontextprotocol/server-github"})
    ]
    assert "installed" in result.output


def test_remove_is_uninstall_alias(api: dict[str, Any]) -> None:
    api["delete_response"] = {"uninstalled": "github"}
    result = runner.invoke(mcp_commands.app, ["remove", "github", "--force"])
    assert result.exit_code == 0, result.output
    assert api["deletes"] == ["/api/mcp/github"]
