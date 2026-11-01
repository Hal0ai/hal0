"""Agent uninstall must report HTTP 207 residuals and fail (#1519)."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from rich.console import Console
from typer.testing import CliRunner

from hal0.cli import _shared, agent_commands

runner = CliRunner()


@pytest.fixture
def uninstall_response(monkeypatch: pytest.MonkeyPatch) -> Callable[[int, dict[str, Any]], None]:
    """Exercise the real api_delete decoder with an in-memory HTTP response."""
    client = httpx.Client
    monkeypatch.setattr(agent_commands, "_api_unreachable", lambda _url: False)
    monkeypatch.setattr(agent_commands, "_disable_hal0_linger", lambda: None)
    monkeypatch.setattr(agent_commands, "console", Console(width=1000))
    monkeypatch.setattr(_shared, "_auth_headers", lambda _url: {})

    def respond(status: int, body: dict[str, Any]) -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            assert request.method == "DELETE"
            assert request.url.path == "/api/agents/hermes"
            return httpx.Response(status, json=body)

        def factory(*args: Any, **kwargs: Any) -> httpx.Client:
            return client(*args, transport=httpx.MockTransport(handle), **kwargs)

        monkeypatch.setattr(httpx, "Client", factory)

    return respond


@pytest.mark.parametrize("removed", [True, False])
def test_partial_uninstall_reports_residuals_and_fails(
    uninstall_response: Callable[[int, dict[str, Any]], None], tmp_path: Path, removed: bool
) -> None:
    residual = [
        {"path": str(tmp_path / "config[old].toml"), "reason": "PermissionError: denied"},
        {"path": str(tmp_path / "credentials"), "reason": "OSError: read-only filesystem"},
    ]
    uninstall_response(
        207,
        {
            "error": {
                "code": "agent.uninstall_incomplete",
                "message": "uninstall incomplete",
                "details": {
                    "name": "hermes",
                    "status": "partially_uninstalled",
                    "removed": removed,
                    "residual": residual,
                },
            }
        },
    )

    result = runner.invoke(agent_commands.app, ["uninstall", "hermes", "--keep-memory", "-f"])

    assert result.exit_code == 1, result.output
    assert "Partially uninstalled hermes." in result.output
    assert "Uninstalled hermes." not in result.output
    assert "memory preserved" not in result.output
    for entry in residual:
        assert entry["path"] in result.output
        assert entry["reason"] in result.output
    assert "root privileges may be required" in result.output
    assert "hal0 agent uninstall hermes" in result.output


@pytest.mark.parametrize(
    ("status", "message"),
    [("uninstalled", "Uninstalled hermes."), ("not_installed", "hermes was not installed.")],
)
def test_successful_uninstall_preserves_output_and_exit_code(
    uninstall_response: Callable[[int, dict[str, Any]], None], status: str, message: str
) -> None:
    uninstall_response(200, {"name": "hermes", "status": status})

    result = runner.invoke(agent_commands.app, ["uninstall", "hermes", "--keep-memory", "-f"])

    assert result.exit_code == 0, result.output
    assert message in result.output
    assert "Files left behind" not in result.output
    assert ("memory preserved" in result.output) == (status == "uninstalled")
