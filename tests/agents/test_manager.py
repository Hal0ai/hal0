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
    return AgentManager(
        etc_root=tmp_path / "etc",
        var_root=tmp_path / "var",
        state_root=tmp_path / "state",
    )


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


# ── #346: registry coherence + state-dir cleanup ─────────────────────────────


def _seed_state_dir(manager: AgentManager, name: str) -> Path:
    """Helper: simulate a hermes_provision.py write into the manager's
    state root. Mirrors what the real bootstrap state machine does at
    runtime (writes ``provision.json`` + ``provision-logs/``)."""
    state_dir = manager._state_dir(name)
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "provision.json").write_text('{"phases":{}}\n')
    logs = state_dir / "provision-logs"
    logs.mkdir(exist_ok=True)
    (logs / "preflight.log").write_text("ok\n")
    return state_dir


def test_uninstall_removes_state_dir(
    manager: AgentManager,
    stub_drivers: dict[str, _StubDriver],
) -> None:
    """#346 (acceptance #1): ``mgr.uninstall(name)`` removes
    ``/var/lib/hal0/state/agents/<name>/`` in addition to the seed TOML
    + data dir."""
    manager.install("hermes")
    state_dir = _seed_state_dir(manager, "hermes")
    assert state_dir.exists()
    assert (state_dir / "provision.json").exists()

    removed = manager.uninstall("hermes")
    assert removed is True
    assert not state_dir.exists()
    # And the other two paths also gone.
    assert not manager._config_path("hermes").exists()
    assert not manager._data_dir("hermes").exists()


def test_uninstall_with_missing_seed_still_reports_uninstalled(
    manager: AgentManager,
    stub_drivers: dict[str, _StubDriver],
) -> None:
    """#346 (acceptance #2 + root cause): the API status string lied
    because ``installed_names()`` only saw the seed TOML. After install,
    delete the seed by hand (simulating a partial-uninstall recovery
    case); the next uninstall MUST still report
    ``removed=True`` because the data + state dirs were torn down."""
    manager.install("hermes")
    _seed_state_dir(manager, "hermes")

    # Corrupt the registry: remove the seed TOML out from under us, but
    # leave the data_dir + state dir in place. This is the exact shape
    # the issue traces in the wild.
    manager._config_path("hermes").unlink()
    assert not manager._config_path("hermes").exists()
    assert manager._data_dir("hermes").exists()
    assert manager._state_dir("hermes").exists()

    removed = manager.uninstall("hermes")
    assert removed is True, (
        "uninstall reported 'not_installed' even though data + state "
        "dirs were on disk — this is the #346 lying-status regression"
    )
    assert not manager._data_dir("hermes").exists()
    assert not manager._state_dir("hermes").exists()


def test_uninstall_with_no_artifacts_returns_false(
    manager: AgentManager,
    stub_drivers: dict[str, _StubDriver],
) -> None:
    """The honest ``not_installed`` case: no seed, no data, no state
    dir. ``uninstall()`` returns False so the API maps to
    ``status='not_installed'``."""
    removed = manager.uninstall("hermes")
    assert removed is False


def test_installed_names_includes_orphan_data_dir(
    manager: AgentManager,
    stub_drivers: dict[str, _StubDriver],
) -> None:
    """#346 (acceptance #3): ``installed_names()`` derives from disk
    truth — seed OR data_dir OR state dir. A data dir alone is enough
    to count as installed."""
    # No install — synthesise just the data dir.
    manager._data_dir("hermes").mkdir(parents=True)
    assert manager.installed_names() == ["hermes"]


def test_installed_names_includes_orphan_state_dir(
    manager: AgentManager,
    stub_drivers: dict[str, _StubDriver],
) -> None:
    """A bootstrap state dir alone is enough to count as installed.
    Pre-#346 this returned [] because only the seed was consulted."""
    _seed_state_dir(manager, "hermes")
    assert manager.installed_names() == ["hermes"]


def test_install_uninstall_install_uninstall_round_trip(
    manager: AgentManager,
    stub_drivers: dict[str, _StubDriver],
) -> None:
    """#346 (acceptance δ-harness #2 mirrored in unit-tier): no orphans
    after each round. Mirrors the δ-harness scenario at the unit
    level — install, uninstall (with a synthesised state dir from a
    bootstrap that the driver stub doesn't itself produce), install
    again, uninstall again. After each uninstall every witness is gone."""
    for _ in range(2):
        manager.install("hermes")
        _seed_state_dir(manager, "hermes")
        assert manager.installed_names() == ["hermes"]

        removed = manager.uninstall("hermes")
        assert removed is True
        assert not manager._config_path("hermes").exists()
        assert not manager._data_dir("hermes").exists()
        assert not manager._state_dir("hermes").exists()
        assert manager.installed_names() == []


def test_list_synthesises_broken_record_for_orphan(
    manager: AgentManager,
    stub_drivers: dict[str, _StubDriver],
) -> None:
    """An orphaned data/state dir without a seed should surface in
    ``list()`` as a ``broken`` record so the dashboard can offer the
    repair affordance — half-state must be visible, not invisible."""
    manager._data_dir("hermes").mkdir(parents=True)
    listing = manager.list()
    assert len(listing) == 1
    assert listing[0].name == "hermes"
    assert listing[0].status == "broken"
    assert listing[0].installed_at == ""


def test_is_present_on_disk_predicate(
    manager: AgentManager,
    stub_drivers: dict[str, _StubDriver],
) -> None:
    """Exhaustive disk-truth predicate: any one of the three witnesses
    is sufficient; all three absent is the only False case."""
    assert manager.is_present_on_disk("hermes") is False

    # Seed alone.
    manager._etc_root.mkdir(parents=True, exist_ok=True)
    manager._config_path("hermes").write_text("")
    assert manager.is_present_on_disk("hermes") is True
    manager._config_path("hermes").unlink()

    # Data dir alone.
    manager._data_dir("hermes").mkdir(parents=True)
    assert manager.is_present_on_disk("hermes") is True
    manager._data_dir("hermes").rmdir()

    # State dir alone.
    manager._state_dir("hermes").mkdir(parents=True)
    assert manager.is_present_on_disk("hermes") is True
    manager._state_dir("hermes").rmdir()

    # Unknown name is never present.
    assert manager.is_present_on_disk("not-a-real-agent") is False


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
