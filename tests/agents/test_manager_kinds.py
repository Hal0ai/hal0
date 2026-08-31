"""Kind-aware single-pick (spec D1): the single-pick guard protects the
daemon/persona/gateway surface, so it applies between daemon-kind agents
only. cli-kind agents (pi) install alongside anything."""

from pathlib import Path

import pytest

from hal0.agents import manager as manager_mod
from hal0.agents.manager import AgentManager, agent_kind


class _FakeDriver:
    def __init__(self, name: str) -> None:
        self.name = name

    def install(self, *, bearer_token: str | None = None) -> None:
        pass

    def uninstall(self) -> None:
        pass

    def status(self) -> str:
        return "installed"


@pytest.fixture
def mgr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AgentManager:
    monkeypatch.setattr(manager_mod, "_driver_for", lambda name: _FakeDriver(name))
    return AgentManager(
        etc_root=tmp_path / "etc",
        var_root=tmp_path / "var",
        state_root=tmp_path / "state",
    )


def test_kinds_registry() -> None:
    assert agent_kind("hermes") == "daemon"
    assert agent_kind("pi") == "cli"
    # Unknown names default daemon — fail closed toward the stricter rule.
    assert agent_kind("something-future") == "daemon"


def test_pi_installs_alongside_hermes(mgr: AgentManager) -> None:
    mgr.install("hermes")
    rec = mgr.install("pi")  # must NOT raise AgentAlreadyInstalledError
    assert rec.name == "pi"
    assert sorted(r.name for r in mgr.list()) == ["hermes", "pi"]


def test_hermes_installs_alongside_pi(mgr: AgentManager) -> None:
    mgr.install("pi")
    rec = mgr.install("hermes")  # cli incumbent must not block a daemon
    assert rec.name == "hermes"


def test_pi_install_is_idempotent(mgr: AgentManager) -> None:
    first = mgr.install("pi")
    second = mgr.install("pi")
    assert second.name == first.name == "pi"


def test_daemon_single_pick_still_enforced(
    mgr: AgentManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Simulate a second daemon-kind agent to prove the guard survives.
    monkeypatch.setattr(manager_mod, "BUNDLED_AGENTS", ("hermes", "pi", "otherd"))
    monkeypatch.setattr(
        manager_mod, "AGENT_KINDS", {"hermes": "daemon", "pi": "cli", "otherd": "daemon"}
    )
    mgr.install("hermes")
    with pytest.raises(manager_mod.AgentAlreadyInstalledError):
        mgr.install("otherd")
