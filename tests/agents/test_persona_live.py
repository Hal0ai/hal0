"""persona_live: an edited or activated persona reaches Hermes's config.yaml (#2921, #2850)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from hal0.agents import hermes_provision as hp
from hal0.agents import persona_live
from hal0.agents import personas as P


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    root = tmp_path / "personas"
    root.mkdir()
    P.seed_default_personas(agent_id="hermes-agent", root=root)
    home = tmp_path / "hermes"
    home.mkdir()
    (home / "config.yaml").write_text(
        yaml.safe_dump({"model": {"default": "x"}, "agent": {"max_turns": 5}}), encoding="utf-8"
    )
    monkeypatch.setattr(hp, "OVERRIDES_PATH", tmp_path / "overrides.yaml")
    restarts: list[int] = []
    failed: list[str] = []

    def _restart() -> list[str]:
        restarts.append(1)
        return list(failed)

    monkeypatch.setattr(hp, "try_restart_hermes_consumers", _restart)
    return {"root": root, "home": home, "restarts": restarts, "failed": failed, "tmp": tmp_path}


def _agent(home: Path) -> dict[str, Any]:
    return yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))["agent"]


def test_activate_renders_new_prompt_and_restarts(env: dict[str, Any]) -> None:
    P.activate("hal0-brain", root=env["root"])
    out = persona_live.refresh_persona_prompt(env["root"], hermes_home=env["home"])
    assert out["status"] == persona_live.APPLIED
    assert out["changed"] is True and out["restarted"] is True
    agent = _agent(env["home"])
    assert "hal0 Brain" in agent["system_prompt"] or "steward" in agent["system_prompt"].lower()
    assert agent["max_turns"] == 5  # hermes-owned keys survive
    assert len(env["restarts"]) == 1


def test_edit_of_active_prompt_is_rendered(env: dict[str, Any]) -> None:
    persona = P.load_persona("hermes", root=env["root"])
    data = persona.to_dict()
    data["persona"]["prompt"]["system"] = "UNIQUE-PROMPT-MARKER"
    P.save_persona(P.Persona.from_dict(data), root=env["root"])
    out = persona_live.refresh_persona_prompt(env["root"], hermes_home=env["home"])
    assert out["status"] == persona_live.APPLIED
    assert "UNIQUE-PROMPT-MARKER" in _agent(env["home"])["system_prompt"]


def test_second_refresh_is_unchanged_and_does_not_restart(env: dict[str, Any]) -> None:
    persona_live.refresh_persona_prompt(env["root"], hermes_home=env["home"])
    env["restarts"].clear()
    out = persona_live.refresh_persona_prompt(env["root"], hermes_home=env["home"])
    assert out["status"] == persona_live.UNCHANGED
    assert env["restarts"] == []


def test_restart_failure_is_reported_not_hidden(env: dict[str, Any]) -> None:
    env["failed"].append("hermes-gateway.service")
    out = persona_live.refresh_persona_prompt(env["root"], hermes_home=env["home"])
    assert out["status"] == persona_live.RESTART_FAILED
    assert out["failed_units"] == ["hermes-gateway.service"]
    assert out["restarted"] is False
    assert "hermes-gateway.service" in out["message"]
    assert "old prompt" in out["message"]
    assert "system_prompt" in _agent(env["home"])  # the write itself landed


def test_unwritable_config_reports_failure_and_repair_hint(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*_a: Any, **_k: Any) -> bool:
        raise PermissionError("denied")

    monkeypatch.setattr(hp, "_merge_config_yaml_layers", _boom)
    out = persona_live.refresh_persona_prompt(env["root"], hermes_home=env["home"])
    assert out["status"] == persona_live.FAILED
    assert "denied" in out["message"]
    assert "reprovision hermes --repair" in out["message"]
    assert env["restarts"] == []


def test_overrides_yaml_system_prompt_wins_and_nothing_is_written(env: dict[str, Any]) -> None:
    (env["tmp"] / "overrides.yaml").write_text(
        yaml.safe_dump({"agent": {"system_prompt": "operator"}}), encoding="utf-8"
    )
    out = persona_live.refresh_persona_prompt(env["root"], hermes_home=env["home"])
    assert out["status"] == persona_live.OVERRIDDEN
    assert "system_prompt" not in _agent(env["home"])
    assert env["restarts"] == []


def test_no_config_yaml_means_not_installed(env: dict[str, Any]) -> None:
    (env["home"] / "config.yaml").unlink()
    out = persona_live.refresh_persona_prompt(env["root"], hermes_home=env["home"])
    assert out["status"] == persona_live.NOT_INSTALLED
    assert env["restarts"] == []


def test_restart_false_writes_but_does_not_restart(env: dict[str, Any]) -> None:
    out = persona_live.refresh_persona_prompt(env["root"], hermes_home=env["home"], restart=False)
    assert out["status"] == persona_live.APPLIED
    assert out["restarted"] is False
    assert env["restarts"] == []
    assert "Restart Hermes" in out["message"]
