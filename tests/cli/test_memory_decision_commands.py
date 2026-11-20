"""#3757 — ``hal0 memory decision install|status|enable|disable|remove``."""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from hal0.cli import memory_decision_commands as mod

runner = CliRunner()


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {
        "decision": {"enabled": True, "provider": "off", "installed": False},
        "puts": [],
        "converges": [],
    }
    monkeypatch.setattr(mod, "_require_api", lambda: None)
    monkeypatch.setattr(mod.os, "geteuid", lambda: 1000)

    def api_get(path: str, **_kw: Any) -> Any:
        if path == "/api/memory/models":
            return {"decision": state["decision"]}
        return {"components": [{"id": "memory-decision", "status": "not-installed"}]}

    def api_put(path: str, *, json: Any = None, **_kw: Any) -> Any:
        state["puts"].append((path, json))
        state["decision"]["provider"] = json["memory"]["decision"]["provider"]
        return {}

    def converge(component_id: str, body: dict[str, Any]) -> dict[str, Any]:
        state["converges"].append((component_id, body, state["decision"]["provider"]))
        state["decision"]["installed"] = body.get("action") != "remove"
        return {"status": "installed"}

    monkeypatch.setattr(mod, "api_get", api_get)
    monkeypatch.setattr(mod, "api_put", api_put)
    monkeypatch.setattr("hal0.cli.update_commands._converge_component", converge)
    return state


def test_install_is_one_converge_that_turns_the_lane_on_itself(api: dict[str, Any]) -> None:
    result = runner.invoke(mod.app, ["install", "--yes"])
    assert result.exit_code == 0, result.output
    assert api["converges"] == [("memory-decision", {"action": "install"}, "off")]
    # The converge writes the provider (#3757 review item 7), not the waiting CLI.
    assert api["puts"] == []


def test_install_reports_a_busy_converge(
    api: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "hal0.cli.update_commands._converge_component",
        lambda *_a: {"status": "busy", "error": "another memory-decision converge is running"},
    )
    result = runner.invoke(mod.app, ["install", "--yes"])
    assert result.exit_code == 1 and "another" in result.output


def test_enable_works_for_a_hand_install(api: dict[str, Any]) -> None:
    api["decision"].update(installed=True, install_state="unmanaged")
    assert runner.invoke(mod.app, ["enable"]).exit_code == 0
    assert api["puts"][-1][1]["memory"]["decision"]["provider"] == "local"


def test_enable_refuses_when_not_installed(api: dict[str, Any]) -> None:
    result = runner.invoke(mod.app, ["enable"])
    assert result.exit_code == 1
    assert "hal0 memory decision install" in result.output
    assert api["puts"] == []


def test_enable_and_disable_flip_the_provider(api: dict[str, Any]) -> None:
    api["decision"]["installed"] = True
    assert runner.invoke(mod.app, ["enable"]).exit_code == 0
    assert runner.invoke(mod.app, ["disable"]).exit_code == 0
    assert [p[1]["memory"]["decision"]["provider"] for p in api["puts"]] == ["local", "off"]


@pytest.mark.parametrize("purge", [False, True])
def test_remove_is_one_converge(api: dict[str, Any], purge: bool) -> None:
    api["decision"].update(installed=True, provider="local")
    args = ["remove", "--yes", *(["--purge-model"] if purge else [])]
    result = runner.invoke(mod.app, args)
    assert result.exit_code == 0, result.output
    # The arm turns the lane off and re-points the engine before the teardown.
    assert api["converges"] == [
        ("memory-decision", {"action": "remove", "purge_model": purge}, "local")
    ]
    assert api["puts"] == []


def test_status_says_what_to_do(api: dict[str, Any]) -> None:
    result = runner.invoke(mod.app, ["status"])
    assert result.exit_code == 0, result.output
    assert "hal0 memory decision install" in result.output
