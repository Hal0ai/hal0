"""``hal0 agent approvals approve|deny`` decide over the root socket (ADR-0048)."""

from __future__ import annotations

import os
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from hal0.cli import agent_commands
from hal0.mcp import approval_socket

runner = CliRunner()


@pytest.fixture
def as_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.delenv("HAL0_API_URL", raising=False)


@pytest.fixture
def no_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any REST call fails the test: a key must never be how the CLI decides."""

    def _refuse(*_a: Any, **_k: Any) -> None:
        raise AssertionError("the decision went over HTTP")

    monkeypatch.setattr(httpx.Client, "send", _refuse)


def _stub_socket(monkeypatch: pytest.MonkeyPatch, reply: dict[str, Any]) -> list[tuple[str, str]]:
    sent: list[tuple[str, str]] = []

    def _send(action: str, approval_id: str, **_k: Any) -> dict[str, Any]:
        sent.append((action, approval_id))
        return reply

    monkeypatch.setattr(approval_socket, "send_decision", _send)
    return sent


@pytest.mark.parametrize("command", ["approve", "deny"])
def test_root_decides_over_the_socket(
    as_root: None, no_http: None, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    sent = _stub_socket(monkeypatch, {"status": 200, "approval": {"state": "executed"}})
    result = runner.invoke(agent_commands.approvals_app, [command, "abc"])
    assert result.exit_code == 0, result.output
    assert sent == [(command, "abc")]


@pytest.mark.parametrize("command", ["approve", "deny"])
def test_non_root_is_told_to_use_sudo_and_nothing_is_sent(
    no_http: None, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    sent = _stub_socket(monkeypatch, {"status": 200, "approval": {}})
    result = runner.invoke(agent_commands.approvals_app, [command, "abc"])
    assert result.exit_code == 1
    assert "sudo" in " ".join(result.output.split())
    assert sent == []


def test_a_remote_target_is_refused_rather_than_deciding_locally(
    as_root: None, no_http: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAL0_API_URL", "http://other-box.example:8080")
    sent = _stub_socket(monkeypatch, {"status": 200, "approval": {}})
    result = runner.invoke(agent_commands.approvals_app, ["approve", "abc"])
    assert result.exit_code == 1
    assert sent == []


def test_a_refusal_from_the_socket_is_an_error(
    as_root: None, no_http: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_socket(
        monkeypatch,
        {"status": 404, "error": {"code": "approvals.not_found", "message": "not found here"}},
    )
    result = runner.invoke(agent_commands.approvals_app, ["deny", "abc"])
    assert result.exit_code == 1
    assert "not found here" in result.output


def test_an_approved_call_that_failed_is_not_reported_as_success(
    as_root: None, no_http: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_socket(
        monkeypatch,
        {"status": 200, "approval": {"state": "failed", "error": "RuntimeError: boom"}},
    )
    result = runner.invoke(agent_commands.approvals_app, ["approve", "abc"])
    assert result.exit_code == 1
    assert "boom" in result.output


def test_a_missing_socket_names_the_problem(
    as_root: None, no_http: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _send(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise approval_socket.ApprovalSocketUnavailable("is hal0-api running on this box?")

    monkeypatch.setattr(approval_socket, "send_decision", _send)
    result = runner.invoke(agent_commands.approvals_app, ["approve", "abc"])
    assert result.exit_code == 1
    assert "hal0-api running" in " ".join(result.output.split())
