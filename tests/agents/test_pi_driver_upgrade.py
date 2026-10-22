"""``PiDriver.upgrade``: re-pin pi, redeploy hal0's own extensions, seed the
Hindsight bank alias — without touching what the operator added.

A box with an older pi used to have only uninstall + reinstall. ``upgrade``
re-runs the installer, redeploys the two extensions and the theme, replaces
the managed ``pi-mcp-adapter`` pin, refreshes the MCP entry and the marker,
and refuses to run as a different user than the one the marker names."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from hal0.agents.manager import AgentError
from hal0.agents.pi_coder import PiDriver
from hal0.agents.pi_coder import driver as driver_mod


class _FakeRunner:
    """Stands in for subprocess: records (argv, kwargs), never spawns."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def run(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))

        class _Done:
            returncode = 0

        return _Done()


def _no_key(prefer: str = "admin") -> str:
    raise OSError("no service key on this box")


@pytest.fixture
def fake_runner() -> _FakeRunner:
    return _FakeRunner()


def _driver_with_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner: _FakeRunner,
    *,
    marker_home: str | None = None,
) -> PiDriver:
    """A PiDriver whose HOME and data dir live under ``tmp_path`` and whose
    marker names ``marker_home`` (default: this HOME)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    script = tmp_path / "installer" / "agents" / "pi.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("#!/bin/sh\nexit 0\n")
    monkeypatch.setattr(driver_mod, "installer_script_path", lambda name: script)
    # Best-effort token self-resolve must not reach for a real box key.
    monkeypatch.setattr("hal0.service_identity.service_key", _no_key)

    data_dir = tmp_path / "var_lib" / "agents" / "pi"
    driver = PiDriver(runner=runner)
    monkeypatch.setattr(driver, "_data_dir", lambda: data_dir)
    data_dir.mkdir(parents=True)
    marker = {
        "version": 1,
        "home": marker_home if marker_home is not None else str(tmp_path),
        "profiled_at": "t",
    }
    driver._profile_marker_path().write_text(json.dumps(marker), encoding="utf-8")
    return driver


def test_upgrade_reruns_installer_and_redeploys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner
) -> None:
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    d._settings_file().parent.mkdir(parents=True, exist_ok=True)
    d._settings_file().write_text(
        json.dumps({"packages": ["npm:pi-mcp-adapter@2.31.0", "npm:other@1"]})
    )

    d.upgrade()

    assert fake_runner.calls[0][0][:2] == ["bash", str(driver_mod.installer_script_path("pi"))]
    # Same env contract as install(): the data dir and API URL reach the script.
    env = fake_runner.calls[0][1]["env"]
    assert env["HAL0_AGENT_DATA_DIR"] == str(d._data_dir())
    assert "HAL0_BEARER_TOKEN" not in env
    assert any(argv == ["npm", "install", "--omit=dev"] for argv, _ in fake_runner.calls)
    assert json.loads(d._settings_file().read_text())["packages"] == [
        "npm:other@1",
        "npm:pi-mcp-adapter@5.3.0",
    ]
    assert d._provider_ext_dst().exists() and d._theme_dst().exists()
    assert d._hindsight_ext_dst().exists()
    mcp = json.loads(d._mcp_config_path().read_text())
    assert mcp["mcpServers"]["hal0-memory"]["url"].endswith("/mcp/memory/mcp")
    assert json.loads(d._profile_marker_path().read_text())["home"] == str(tmp_path)
    assert json.loads(d._profile_marker_path().read_text())["profiled_at"] != "t"


def test_upgrade_refuses_wrong_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner
) -> None:
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner, marker_home="/somewhere/else")
    with pytest.raises(AgentError, match="run this as the user who installed pi"):
        d.upgrade()
    assert fake_runner.calls == []
    assert not d._settings_file().exists()


def test_upgrade_refuses_when_pi_was_never_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner
) -> None:
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    d._profile_marker_path().unlink()
    with pytest.raises(AgentError, match="not installed"):
        d.upgrade()
    assert fake_runner.calls == []


def test_upgrade_leaves_operator_extensions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner
) -> None:
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    extra = d._pi_agent_dir() / "extensions" / "shepherd"
    extra.mkdir(parents=True)
    (extra / "index.ts").write_text("x")

    d.upgrade()

    assert (extra / "index.ts").read_text() == "x"


def test_ensure_hindsight_alias_adds_only_missing_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner
) -> None:
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    cfg = d._hindsight_client_config()
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        json.dumps(
            {
                "apiUrl": "http://127.0.0.1:9177",
                "mapPathToBank": {"/old": "coding-agent::hal0-mono"},
            }
        )
    )

    assert d._ensure_hindsight_alias() is True

    data = json.loads(cfg.read_text())
    assert data["banks"] == {"coding-agent::hal0": {"bank": "coding-agent::hal0-mono"}}
    assert data["mapPathToBank"] == {"/old": "coding-agent::hal0-mono"}
    assert data["apiUrl"] == "http://127.0.0.1:9177"
    assert d._ensure_hindsight_alias() is False


def test_ensure_hindsight_alias_leaves_operator_banks_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner
) -> None:
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    cfg = d._hindsight_client_config()
    cfg.parent.mkdir(parents=True)
    banks = {
        "coding-agent::secret": {"disabled": True},
        # The operator already routes hal0 somewhere else: not ours to rewrite.
        "coding-agent::hal0": {"bank": "team::shared"},
    }
    cfg.write_text(json.dumps({"banks": banks}))

    assert d._ensure_hindsight_alias() is False

    assert json.loads(cfg.read_text()) == {"banks": banks}


def test_ensure_hindsight_alias_is_a_noop_without_a_usable_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner
) -> None:
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    cfg = d._hindsight_client_config()
    assert d._ensure_hindsight_alias() is False
    assert not cfg.exists()  # never seeds: that is install()'s job

    cfg.parent.mkdir(parents=True)
    cfg.write_text("{not json")
    assert d._ensure_hindsight_alias() is False
    assert cfg.read_text() == "{not json"


def test_ensure_hindsight_alias_keeps_the_file_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner
) -> None:
    """The file is shared operator state and may carry a token: a rewrite must
    not widen its permissions."""
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    cfg = d._hindsight_client_config()
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"apiUrl": "http://127.0.0.1:9177"}))
    os.chmod(cfg, 0o640)

    assert d._ensure_hindsight_alias() is True

    assert stat.S_IMODE(os.stat(cfg).st_mode) == 0o640


def test_upgrade_seeds_alias_into_existing_hindsight_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_runner: _FakeRunner
) -> None:
    d = _driver_with_marker(tmp_path, monkeypatch, fake_runner)
    cfg = d._hindsight_client_config()
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"apiUrl": "http://other:9177"}))

    d.upgrade()

    data = json.loads(cfg.read_text())
    assert data["apiUrl"] == "http://other:9177"
    assert data["banks"] == {"coding-agent::hal0": {"bank": "coding-agent::hal0-mono"}}
