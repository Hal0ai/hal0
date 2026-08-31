"""PiDriver shim (spec D2/D3/D4): minimal profile writes, idempotency,
operator-state preservation, and the two memory wires."""

import json
import os
import stat
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
    # status() also probes for the pi binary; the fake runner never
    # installs one, so pretend PATH has it (the marker/file checks stay
    # real). Tests for the binary-missing branch override this.
    monkeypatch.setattr(driver_mod, "_pi_binary_on_path", lambda: True)
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
    # Extensions are NOT listed: pi auto-discovers ~/.pi/agent/extensions/,
    # and an explicit entry double-loads them (registerTool conflicts).
    assert settings["packages"] == ["npm:pi-mcp-adapter@2.31.0"]
    assert (home / ".pi" / "agent" / "themes" / "hal0.json").exists()
    assert (home / ".pi" / "agent" / "extensions" / "hal0-provider" / "index.ts").exists()
    assert (home / ".pi" / "agent" / "extensions" / "hindsight" / "index.ts").exists()


def test_install_writes_memory_mcp_config(home: Path) -> None:
    drv, _ = _driver()
    drv.install(bearer_token="tok123")

    mcp_path = home / ".pi" / "agent" / "mcp.json"
    mcp = json.loads(mcp_path.read_text())
    server = mcp["mcpServers"]["hal0-memory"]
    assert server["url"] == "http://127.0.0.1:8080/mcp/memory/mcp"
    assert server["headers"]["Authorization"] == "Bearer tok123"
    assert server["headers"]["X-hal0-Agent"] == "pi"


def test_mcp_config_written_mode_0600(home: Path) -> None:
    drv, _ = _driver()
    drv.install(bearer_token="tok123")

    mcp_path = home / ".pi" / "agent" / "mcp.json"
    assert oct(stat.S_IMODE(os.stat(mcp_path).st_mode)) == "0o600"


def test_mcp_config_self_resolves_token_when_none_passed(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No bearer_token from the caller (the production /api/agents/install
    path never passes one) — the driver best-effort self-resolves the box
    service key the same way hermes_provision.py does."""
    from hal0 import service_identity

    monkeypatch.setattr(service_identity, "service_key", lambda prefer="admin": "resolved-tok")

    drv, _ = _driver()
    drv.install()

    mcp = json.loads((home / ".pi" / "agent" / "mcp.json").read_text())
    server = mcp["mcpServers"]["hal0-memory"]
    assert server["headers"]["Authorization"] == "Bearer resolved-tok"
    assert server["headers"]["X-hal0-Agent"] == "pi"


def test_mcp_config_tolerates_resolver_failure(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing self-resolver (e.g. auth disabled, key file unreadable)
    must not block the install — the entry is written without an
    Authorization header, and X-hal0-Agent is always present."""

    def _boom(prefer: str = "admin") -> str | None:
        raise RuntimeError("no key on this box")

    monkeypatch.setattr("hal0.service_identity.service_key", _boom)

    drv, _ = _driver()
    drv.install()

    mcp = json.loads((home / ".pi" / "agent" / "mcp.json").read_text())
    server = mcp["mcpServers"]["hal0-memory"]
    assert "Authorization" not in server["headers"]
    assert server["headers"]["X-hal0-Agent"] == "pi"


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
    # Operator package preserved; legacy double-loading extension entries
    # stripped (pi auto-discovers the extensions dir); adapter pin added.
    assert "npm:their-own-thing" in settings["packages"]
    assert "extensions/hal0-provider" not in settings["packages"]
    assert "extensions/hindsight" not in settings["packages"]
    assert "npm:pi-mcp-adapter@2.31.0" in settings["packages"]


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
    # Install stamps the daemon-readable marker in the data dir.
    marker = driver_mod._paths.var_lib() / "agents" / "pi" / "profile.json"
    assert json.loads(marker.read_text())["home"] == str(home)
    assert drv.status() == "installed"
    drv.uninstall()
    assert not marker.exists()
    assert not (home / ".pi" / "agent" / "extensions" / "hal0-provider").exists()
    assert not (home / ".pi" / "agent" / "extensions" / "hindsight").exists()
    assert not (home / ".pi" / "agent" / "themes" / "hal0.json").exists()
    settings = json.loads((home / ".pi" / "agent" / "settings.json").read_text())
    assert settings.get("defaultProvider") != "hal0"
    mcp = json.loads((home / ".pi" / "agent" / "mcp.json").read_text())
    assert "hal0-memory" not in mcp.get("mcpServers", {})
    assert drv.status() == "broken"


def test_uninstall_runs_companion_script(home: Path) -> None:
    """The manager rmtree's the data dir (destroying uninstall.sh) right
    after uninstall() returns — this is the only chance to run the npm
    uninstall companion installer/agents/pi.sh wrote at install time."""
    drv, runner = _driver()
    drv.install()
    data_dir = driver_mod._paths.var_lib() / "agents" / "pi"
    companion = data_dir / "uninstall.sh"
    companion.parent.mkdir(parents=True, exist_ok=True)
    companion.write_text("#!/bin/sh\nexit 0\n")

    runner.calls.clear()
    drv.uninstall()

    assert ["sh", str(companion)] in runner.calls


def test_uninstall_tolerates_missing_companion(home: Path) -> None:
    """No uninstall.sh on disk (e.g. install failed before the script
    wrote it) must not raise — uninstall() still tears down config."""
    drv, _ = _driver()
    drv.install()
    drv.uninstall()  # must not raise
    assert drv.status() == "broken"


def test_status_from_daemon_perspective(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The hal0-api daemon (User=hal0) cannot read the operator's 0700
    home — status() must answer "installed" from the data-dir marker +
    PATH probe alone when the profiled home is unreadable."""
    drv, _ = _driver()
    drv.install()

    real_access = driver_mod.os.access

    def _deny_home(path, mode):  # noqa: ANN001 — mirrors os.access
        if Path(path) == home:
            return False
        return real_access(path, mode)

    monkeypatch.setattr(driver_mod.os, "access", _deny_home)
    assert drv.status() == "installed"


def test_status_broken_without_pi_binary(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Marker present but the pi binary gone from PATH (npm uninstalled
    behind our back) is broken — the profile points at nothing runnable."""
    drv, _ = _driver()
    drv.install()
    monkeypatch.setattr(driver_mod, "_pi_binary_on_path", lambda: False)
    assert drv.status() == "broken"
