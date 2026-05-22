"""Unit tests for hal0.agents.manager.AgentManager.

Covers ADR-0004 §2 (single-pick + atomic --switch) and the seed-toml
disk layout. Drivers are stubbed so the manager can be exercised
without bash / npm / Hermes on the host.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from hal0.agents import manager as mgr_mod
from hal0.agents.manager import (
    BUNDLED_AGENTS,
    AgentAlreadyInstalledError,
    AgentManager,
    AgentNotFoundError,
)

# ── Driver stub ──────────────────────────────────────────────────────────────


class _StubDriver:
    """Records install/uninstall calls without touching disk or processes."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.installs: list[str | None] = []
        self.uninstalls: int = 0
        self._installed = False

    def install(self, *, bearer_token: str | None = None) -> None:
        self.installs.append(bearer_token)
        self._installed = True

    def uninstall(self) -> None:
        self.uninstalls += 1
        self._installed = False

    def status(self) -> str:
        return "installed" if self._installed else "broken"


@pytest.fixture
def stub_drivers(monkeypatch: pytest.MonkeyPatch) -> dict[str, _StubDriver]:
    """Patch :func:`hal0.agents.manager._driver_for` to return stubs.

    One stub per bundled agent name. Tests can assert on
    ``stubs["pi-coder"].installs`` etc.
    """
    stubs: dict[str, _StubDriver] = {name: _StubDriver(name) for name in BUNDLED_AGENTS}

    def _fake_driver_for(name: str) -> _StubDriver:
        if name not in stubs:
            raise AgentNotFoundError(name)
        return stubs[name]

    monkeypatch.setattr(mgr_mod, "_driver_for", _fake_driver_for)
    return stubs


@pytest.fixture
def manager(tmp_path: Path) -> AgentManager:
    return AgentManager(etc_root=tmp_path / "etc", var_root=tmp_path / "var")


# ── list ─────────────────────────────────────────────────────────────────────


def test_list_empty_when_no_agents_installed(manager: AgentManager) -> None:
    assert manager.list() == []
    assert manager.installed_names() == []


# ── install: happy path ──────────────────────────────────────────────────────


def test_install_pi_coder_writes_seed_and_data_dir(
    manager: AgentManager,
    stub_drivers: dict[str, _StubDriver],
) -> None:
    rec = manager.install("pi-coder", bearer_token="hal0_tok_abc")
    assert rec.name == "pi-coder"
    assert rec.status == "installed"
    # Driver got the token verbatim — confirms wiring from manager →
    # driver is straight through.
    assert stub_drivers["pi-coder"].installs == ["hal0_tok_abc"]

    # Seed TOML present + parseable.
    seed = Path(rec.config_path)
    assert seed.exists()
    parsed = tomllib.loads(seed.read_text())
    assert parsed["agent"]["name"] == "pi-coder"
    assert parsed["agent"]["version_pin"] is False  # ADR-0004 §3

    # Per-agent data dir provisioned.
    assert Path(rec.data_dir).is_dir()


def test_list_after_install_returns_one_record(
    manager: AgentManager,
    stub_drivers: dict[str, _StubDriver],
) -> None:
    manager.install("pi-coder")
    listing = manager.list()
    assert len(listing) == 1
    assert listing[0].name == "pi-coder"
    assert manager.installed_names() == ["pi-coder"]


# ── install: idempotent re-install ───────────────────────────────────────────


def test_install_same_agent_twice_is_noop(
    manager: AgentManager,
    stub_drivers: dict[str, _StubDriver],
) -> None:
    rec1 = manager.install("pi-coder")
    rec2 = manager.install("pi-coder")
    assert rec1.name == rec2.name == "pi-coder"
    # Driver invoked exactly once — second call hit the
    # already-installed short-circuit.
    assert len(stub_drivers["pi-coder"].installs) == 1


# ── install: single-pick enforcement ─────────────────────────────────────────


def test_install_second_agent_without_switch_raises(
    manager: AgentManager,
    stub_drivers: dict[str, _StubDriver],
) -> None:
    manager.install("pi-coder")
    with pytest.raises(AgentAlreadyInstalledError) as exc:
        manager.install("hermes")
    # Error message should name BOTH agents so the operator sees why.
    msg = str(exc.value)
    assert "pi-coder" in msg
    assert "hermes" in msg
    # Hermes driver was NOT invoked.
    assert stub_drivers["hermes"].installs == []
    # pi-coder still the installed one.
    assert manager.installed_names() == ["pi-coder"]


def test_install_with_switch_swaps_atomically(
    manager: AgentManager,
    stub_drivers: dict[str, _StubDriver],
) -> None:
    manager.install("pi-coder")
    rec = manager.install("hermes", switch=True)
    assert rec.name == "hermes"
    # pi-coder uninstall fired exactly once.
    assert stub_drivers["pi-coder"].uninstalls == 1
    # Only hermes is now installed.
    assert manager.installed_names() == ["hermes"]


def test_switch_helper_equivalent_to_install_with_switch_true(
    manager: AgentManager,
    stub_drivers: dict[str, _StubDriver],
) -> None:
    manager.install("pi-coder")
    rec = manager.switch("hermes")
    assert rec.name == "hermes"
    assert manager.installed_names() == ["hermes"]


# ── install: unknown name ────────────────────────────────────────────────────


def test_install_unknown_agent_raises(manager: AgentManager) -> None:
    with pytest.raises(AgentNotFoundError):
        manager.install("not-real")


# ── uninstall ────────────────────────────────────────────────────────────────


def test_uninstall_removes_seed_and_data_dir(
    manager: AgentManager,
    stub_drivers: dict[str, _StubDriver],
) -> None:
    rec = manager.install("pi-coder")
    seed = Path(rec.config_path)
    data = Path(rec.data_dir)
    assert seed.exists() and data.exists()

    manager.uninstall("pi-coder")
    assert not seed.exists()
    assert not data.exists()
    assert manager.installed_names() == []
    assert stub_drivers["pi-coder"].uninstalls == 1


def test_uninstall_when_not_installed_is_noop(
    manager: AgentManager,
    stub_drivers: dict[str, _StubDriver],
) -> None:
    # Should not raise — idempotent posture mirrors slot-delete and
    # the /api/agents DELETE route's "not_installed" return.
    manager.uninstall("pi-coder")
    # Driver's uninstall still runs (best-effort cleanup) — but no
    # disk state to remove.
    assert stub_drivers["pi-coder"].uninstalls == 1


def test_uninstall_unknown_agent_raises(manager: AgentManager) -> None:
    with pytest.raises(AgentNotFoundError):
        manager.uninstall("not-real")


# ── atomic --switch: failure rollback ────────────────────────────────────────


def test_switch_failed_install_leaves_no_installed_agent(
    manager: AgentManager,
    stub_drivers: dict[str, _StubDriver],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the new agent's install fails mid-swap, neither agent is
    installed — the old one is gone (uninstalled atomically first), the
    new one rolled back via the seed not being written.

    This is the explicit ADR-0004 §2 promise: "Operator never end up
    with two bundled agents partially installed."
    """
    manager.install("pi-coder")

    # Make hermes' install raise after pi-coder is uninstalled.
    stubs = stub_drivers

    def _boom(*, bearer_token: str | None = None) -> None:
        raise RuntimeError("simulated upstream-broke")

    stubs["hermes"].install = _boom  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="simulated upstream-broke"):
        manager.install("hermes", switch=True)

    # pi-coder was torn down; hermes never got a seed written.
    assert manager.installed_names() == []
    assert stubs["pi-coder"].uninstalls == 1
