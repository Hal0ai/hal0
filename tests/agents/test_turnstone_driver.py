"""Unit tests for the turnstone bundled-agent driver.

Covers the thin API-path install gate, the status priority order
(systemd → loopback → env-file), and uninstall's out-of-triad cleanup.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.agents.manager import AgentError
from hal0.agents.turnstone import driver as drv
from hal0.agents.turnstone.driver import TurnstoneDriver


@pytest.fixture
def hal0_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point _paths at a tmp HAL0_HOME so the driver writes under tmp."""
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    return tmp_path


def test_install_refuses_when_not_provisioned(hal0_home: Path) -> None:
    # prober=False → binary not on disk → install must refuse (point at CLI).
    d = TurnstoneDriver(prober=lambda: False)
    with pytest.raises(AgentError) as exc:
        d.install()
    assert "hal0 agent install turnstone" in str(exc.value)


def test_install_writes_env_file_when_provisioned(hal0_home: Path) -> None:
    d = TurnstoneDriver(prober=lambda: True)
    d.install(bearer_token="hal0_tok_xyz")
    env = d._env_file_path()
    assert env.exists()
    body = env.read_text()
    assert "HAL0_API_URL=" in body
    assert "HAL0_BEARER_TOKEN=hal0_tok_xyz" in body
    assert "/mcp/memory" in body and "/mcp/admin" in body


def test_status_priority(monkeypatch: pytest.MonkeyPatch, hal0_home: Path) -> None:
    d = TurnstoneDriver(prober=lambda: True)

    # 1) systemd active → installed (no need to probe further).
    monkeypatch.setattr(drv, "_probe_systemd_unit_active", lambda unit: True)
    monkeypatch.setattr(drv, "_probe_tcp_port", lambda *a, **k: False)
    assert d.status() == "installed"

    # 2) systemd down, loopback up → installed.
    monkeypatch.setattr(drv, "_probe_systemd_unit_active", lambda unit: False)
    monkeypatch.setattr(drv, "_probe_tcp_port", lambda *a, **k: True)
    assert d.status() == "installed"

    # 3) both down, env-file present → installed.
    monkeypatch.setattr(drv, "_probe_tcp_port", lambda *a, **k: False)
    d.install()  # writes the env file
    assert d.status() == "installed"

    # 4) both down, no env-file → broken.
    d._env_file_path().unlink()
    assert d.status() == "broken"


def test_uninstall_removes_external_artifacts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, hal0_home: Path
) -> None:
    d = TurnstoneDriver(prober=lambda: True)

    # Redirect the module-default artifact paths into tmp so we don't touch
    # the real /usr/local/bin or /var/lib.
    bin_path = tmp_path / "bin" / "turnstone"
    shim = tmp_path / "shim" / "turnstone"
    db = tmp_path / "db" / "turnstone.db"
    home = tmp_path / "home"
    for p in (bin_path, shim, db):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
    home.mkdir()
    (home / "config.toml").write_text("x")
    monkeypatch.setattr(drv, "_MANAGED_BIN", bin_path)
    monkeypatch.setattr(drv, "_CLI_SHIM", shim)

    # provision.json records the real paths → uninstall reads them.
    prov = {"binary_path": str(bin_path), "db_path": str(db), "turnstone_home": str(home)}
    monkeypatch.setattr(d, "_load_provision", lambda: prov)

    d.install()  # create the env file so uninstall has something to remove
    assert d._env_file_path().exists()

    d.uninstall()
    assert not bin_path.exists()
    assert not shim.exists()
    assert not db.exists()
    assert not home.exists()
    assert not d._env_file_path().exists()


def test_uninstall_is_idempotent_without_provision(
    monkeypatch: pytest.MonkeyPatch, hal0_home: Path
) -> None:
    d = TurnstoneDriver(prober=lambda: True)
    monkeypatch.setattr(d, "_load_provision", lambda: None)
    # Nothing on disk — must not raise.
    d.uninstall()
