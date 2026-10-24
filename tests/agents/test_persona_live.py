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
        yaml.safe_dump(
            {
                "model": {"default": "x"},
                "agent": {"max_turns": 5},
                "terminal": {"backend": hp.TERMINAL_OFF_BACKEND},
                "toolsets": {"disabled": list(hp.TERMINAL_OFF_TOOLSETS)},
                "mcp_servers": {
                    "hal0-admin": {"url": "http://127.0.0.1:8080/mcp/admin/mcp"},
                    "github": {"url": "https://example.invalid/mcp"},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(hp, "OVERRIDES_PATH", tmp_path / "overrides.yaml")
    restarts: list[int] = []

    def _restart() -> list[str]:
        restarts.append(1)
        return []

    # The refresh must never restart anything: Hermes re-reads config.yaml.
    monkeypatch.setattr(hp, "try_restart_hermes_consumers", _restart)
    return {"root": root, "home": home, "restarts": restarts, "tmp": tmp_path}


def _agent(home: Path) -> dict[str, Any]:
    return yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))["agent"]


def test_activate_renders_new_prompt_without_restart(env: dict[str, Any]) -> None:
    P.activate("hal0-brain", root=env["root"])
    out = persona_live.refresh_persona_prompt(env["root"], hermes_home=env["home"])
    assert out["status"] == persona_live.APPLIED
    assert out["changed"] is True
    assert "new sessions" in out["message"] and "old prompt" in out["message"]
    agent = _agent(env["home"])
    assert "hal0 Brain" in agent["system_prompt"] or "steward" in agent["system_prompt"].lower()
    assert agent["max_turns"] == 5  # hermes-owned keys survive
    cfg = yaml.safe_load((env["home"] / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["terminal"]["backend"] == hp.TERMINAL_OFF_BACKEND
    assert cfg["toolsets"]["disabled"] == list(hp.TERMINAL_OFF_TOOLSETS)
    assert set(cfg["mcp_servers"]) == {"hal0-admin", "github"}
    assert env["restarts"] == []


def test_edit_of_active_prompt_is_rendered(env: dict[str, Any]) -> None:
    persona = P.load_persona("hermes", root=env["root"])
    data = persona.to_dict()
    data["persona"]["prompt"]["system"] = "UNIQUE-PROMPT-MARKER"
    P.save_persona(P.Persona.from_dict(data), root=env["root"])
    out = persona_live.refresh_persona_prompt(env["root"], hermes_home=env["home"])
    assert out["status"] == persona_live.APPLIED
    assert "UNIQUE-PROMPT-MARKER" in _agent(env["home"])["system_prompt"]


def test_second_refresh_is_unchanged(env: dict[str, Any]) -> None:
    persona_live.refresh_persona_prompt(env["root"], hermes_home=env["home"])
    out = persona_live.refresh_persona_prompt(env["root"], hermes_home=env["home"])
    assert out["status"] == persona_live.UNCHANGED
    assert env["restarts"] == []


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


def test_operator_personality_is_flagged(env: dict[str, Any]) -> None:
    cfg_path = env["home"] / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    cfg["display"] = {"personality": "Concise"}
    cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    out = persona_live.refresh_persona_prompt(env["root"], hermes_home=env["home"])
    assert out["status"] in (persona_live.APPLIED, persona_live.UNCHANGED)
    assert "display.personality is 'Concise'" in out["message"]
    assert (
        yaml.safe_load(cfg_path.read_text(encoding="utf-8"))["display"]["personality"] == "Concise"
    )


def test_concurrent_refreshes_end_on_latest_active(env: dict[str, Any]) -> None:
    import threading

    P.activate("hal0-brain", root=env["root"])
    threads = [
        threading.Thread(
            target=persona_live.refresh_persona_prompt,
            args=(env["root"],),
            kwargs={"hermes_home": env["home"]},
        )
        for _ in range(6)
    ]
    for t in threads:
        t.start()
    P.activate("hermes", root=env["root"])
    for t in threads:
        t.join()
    persona_live.refresh_persona_prompt(env["root"], hermes_home=env["home"])
    expect = hp._active_persona_render(
        hp.BootstrapState(hermes_home=str(env["home"])), personas_root=env["root"]
    )[0]
    assert _agent(env["home"])["system_prompt"] == expect
