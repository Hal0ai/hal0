"""PiDriver shim (spec D2/D3/D4): minimal profile writes, idempotency,
operator-state preservation, and the two memory wires."""

import json
from pathlib import Path

import pytest

from hal0.agents.pi_coder import PiDriver
from hal0.agents.pi_coder import driver as driver_mod


class _FakeRunner:
    """Stands in for subprocess: records argv, never spawns."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def run(self, argv, **kwargs):  # noqa: ANN001 — mirrors subprocess.run
        self.calls.append(list(argv))

        class _Done:
            returncode = 0

        return _Done()


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    # Script path must exist for install() to proceed.
    monkeypatch.setattr(
        driver_mod, "installer_script_path", lambda name: _touch_script(tmp_path, name)
    )
    monkeypatch.setattr(driver_mod._paths, "var_lib", lambda: tmp_path / "var_lib")
    return tmp_path


def _touch_script(tmp_path: Path, name: str) -> Path:
    script = tmp_path / "installer" / "agents" / f"{name}.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("#!/bin/sh\nexit 0\n")
    return script


def _driver() -> tuple[PiDriver, _FakeRunner]:
    runner = _FakeRunner()
    return PiDriver(runner=runner), runner


def test_install_writes_minimal_profile(home: Path) -> None:
    drv, _ = _driver()
    drv.install(bearer_token="tok123")

    settings = json.loads((home / ".pi" / "agent" / "settings.json").read_text())
    assert settings["theme"] == "hal0"
    assert settings["defaultProvider"] == "hal0"
    assert settings["defaultModel"] == "agent"
    assert settings["packages"] == [
        "extensions/hal0-provider",
        "extensions/hindsight",
        "npm:pi-mcp-adapter@2.31.0",
    ]
    assert (home / ".pi" / "agent" / "themes" / "hal0.json").exists()
    assert (home / ".pi" / "agent" / "extensions" / "hal0-provider" / "index.ts").exists()
    assert (home / ".pi" / "agent" / "extensions" / "hindsight" / "index.ts").exists()


def test_install_writes_memory_mcp_config(home: Path) -> None:
    drv, _ = _driver()
    drv.install(bearer_token="tok123")

    mcp = json.loads((home / ".pi" / "agent" / "mcp.json").read_text())
    server = mcp["mcpServers"]["hal0-memory"]
    assert server["url"] == "http://127.0.0.1:8080/mcp/memory"
    assert server["headers"]["Authorization"] == "Bearer tok123"


def test_install_writes_hindsight_config_only_if_absent(home: Path) -> None:
    drv, _ = _driver()
    drv.install()
    cfg_path = home / ".hindsight" / "coding-agent.json"
    cfg = json.loads(cfg_path.read_text())
    assert cfg["serverMode"] == "self-hosted"
    assert cfg["apiUrl"] == "http://127.0.0.1:9177"
    assert cfg["retainTags"] == ["project:{gitProject}"]

    # Operator config wins on re-install.
    cfg_path.write_text(json.dumps({"serverMode": "self-hosted", "apiUrl": "http://other:9177"}))
    drv.install()
    assert json.loads(cfg_path.read_text())["apiUrl"] == "http://other:9177"


def test_install_preserves_operator_settings(home: Path) -> None:
    settings_file = home / ".pi" / "agent" / "settings.json"
    settings_file.parent.mkdir(parents=True)
    settings_file.write_text(
        json.dumps(
            {
                "editorPaddingX": 3,
                "packages": ["npm:their-own-thing", "extensions/hal0-provider"],
            }
        )
    )
    drv, _ = _driver()
    drv.install()
    settings = json.loads(settings_file.read_text())
    assert settings["editorPaddingX"] == 3  # untouched operator key
    # Managed packages present exactly once, operator package preserved.
    assert settings["packages"].count("extensions/hal0-provider") == 1
    assert "npm:their-own-thing" in settings["packages"]
    assert "extensions/hindsight" in settings["packages"]


def test_install_runs_script_and_npm_install_for_hindsight_ext(home: Path) -> None:
    drv, runner = _driver()
    drv.install()
    assert runner.calls[0][0] == "bash"  # installer script first
    assert any(
        c[:3] == ["npm", "install", "--omit=dev"] for c in runner.calls
    ), f"expected npm install in deployed hindsight extension, got {runner.calls}"


def test_status_and_uninstall(home: Path) -> None:
    drv, _ = _driver()
    drv.install()
    assert drv.status() == "installed"
    drv.uninstall()
    assert not (home / ".pi" / "agent" / "extensions" / "hal0-provider").exists()
    assert not (home / ".pi" / "agent" / "extensions" / "hindsight").exists()
    assert not (home / ".pi" / "agent" / "themes" / "hal0.json").exists()
    settings = json.loads((home / ".pi" / "agent" / "settings.json").read_text())
    assert settings.get("defaultProvider") != "hal0"
    mcp = json.loads((home / ".pi" / "agent" / "mcp.json").read_text())
    assert "hal0-memory" not in mcp.get("mcpServers", {})
    assert drv.status() == "broken"
