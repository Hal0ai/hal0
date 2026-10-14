"""``hal0 agent command-approvals [smart|manual]`` — show or set Hermes command approvals."""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from hal0.cli import agent_commands
from hal0.cli._shared import CliApiError

runner = CliRunner()

STATUS = {
    "agent_id": "hermes",
    "mode": "smart",
    "effective": "smart",
    "source": "default",
    "default": "smart",
    "allowed": ["smart", "manual"],
    "invalid_value": None,
    "overrides_path": "/etc/hal0/agents/hermes/overrides.yaml",
    "rendered": "smart",
    "in_sync": True,
}


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    calls: dict[str, Any] = {"get": [], "put": [], "status": dict(STATUS), "put_error": None}
    monkeypatch.setattr(agent_commands, "_api_unreachable", lambda url: False)

    def fake_get(path: str, **_kw: Any) -> Any:
        calls["get"].append(path)
        return calls["status"]

    def fake_put(path: str, *, json: Any = None, headers: Any = None, **kw: Any) -> Any:
        calls["put"].append((path, json, headers, kw.get("timeout")))
        if calls["put_error"]:
            raise CliApiError(calls["put_error"])
        return {
            **calls["status"],
            "mode": json["mode"],
            "effective": json["mode"],
            "source": "setting",
            "changed": True,
            "restart": "restarted",
            "message": f"Hermes command approvals set to {json['mode']}. Hermes restarted.",
        }

    monkeypatch.setattr(agent_commands, "api_get", fake_get)
    monkeypatch.setattr(agent_commands, "api_put", fake_put)
    return calls


def _invoke(*args: str) -> Any:
    return runner.invoke(agent_commands.app, ["command-approvals", *args])


def test_no_argument_shows_the_setting(api: dict[str, Any]) -> None:
    res = _invoke()
    assert res.exit_code == 0, res.output
    assert api["get"] == ["/api/agents/hermes/command-approvals"] and api["put"] == []
    assert "Hermes command approvals: smart" in res.output
    assert "(default)" in res.output
    assert "anything but APPROVE" in res.output and "asking you" in res.output


@pytest.mark.parametrize("mode", ["smart", "manual"])
def test_setting_a_mode_puts_it(api: dict[str, Any], mode: str) -> None:
    res = _invoke(mode)
    assert res.exit_code == 0, res.output
    path, body, headers, timeout = api["put"][0]
    assert path == "/api/agents/hermes/command-approvals"
    assert body == {"mode": mode}
    # Identifies as the operator CLI, not an agent (the route refuses agents).
    assert headers == {"X-hal0-Agent": "hal0-cli"}
    assert timeout and timeout >= 60
    assert f"set to {mode}" in res.output


def test_invalid_mode_is_refused_locally(api: dict[str, Any]) -> None:
    res = _invoke("off")
    assert res.exit_code != 0
    assert "smart or manual" in res.output
    assert api["put"] == [] and api["get"] == []


def test_override_is_shown_as_in_effect(api: dict[str, Any]) -> None:
    api["status"].update(mode="manual", effective="smart", source="overrides")
    res = _invoke()
    assert res.exit_code == 0
    assert "In effect: smart" in res.output
    assert "overrides.yaml" in res.output and "wins" in res.output


def test_drift_and_invalid_value_are_shown(api: dict[str, Any]) -> None:
    api["status"].update(rendered="off", in_sync=False, invalid_value="bogus")
    res = _invoke()
    assert "config.yaml says 'off'" in res.output
    assert "'bogus' is invalid" in res.output


def test_api_error_exits_nonzero(api: dict[str, Any]) -> None:
    api["put_error"] = "500 agent.command_approvals_apply_failed: Hermes restart failed"
    res = _invoke("manual")
    assert res.exit_code != 0
    assert "Hermes restart failed" in res.output


def test_json_output(api: dict[str, Any]) -> None:
    res = _invoke("--json")
    assert res.exit_code == 0
    assert '"effective": "smart"' in res.output
