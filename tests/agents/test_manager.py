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


# hal0 v0.3 ships exactly two bundled agents (hermes, turnstone) and BOTH
# are in COEXISTING_AGENTS (ADR-0004 §2 amendment) — neither blocks the
# other under single-pick. That leaves no REAL non-coexisting agent pair
# to exercise AgentManager's single-pick BLOCKING / atomic --switch /
# rollback-on-failure paths against, now that the two agents that used to
# fill that role (pi-coder, opencode) are gone (refs P1-drivers). "widget"
# is a synthetic, test-only bundled-agent name standing in for "some other
# non-coexisting agent" purely so that manager logic stays under test; it
# is never a real driver and BUNDLED_AGENTS is only ever patched inside
# this fixture, never in production.
_FAKE_BUNDLED_AGENTS: tuple[str, ...] = (*BUNDLED_AGENTS, "widget")


@pytest.fixture
def stub_drivers(monkeypatch: pytest.MonkeyPatch) -> dict[str, _StubDriver]:
    """Patch :func:`hal0.agents.manager._driver_for` to return stubs.

    One stub per bundled agent name (the real ones plus the synthetic
    "widget"). Tests can assert on ``stubs["hermes"].installs`` etc.
    """
    monkeypatch.setattr(mgr_mod, "BUNDLED_AGENTS", _FAKE_BUNDLED_AGENTS)
    stubs: dict[str, _StubDriver] = {name: _StubDriver(name) for name in _FAKE_BUNDLED_AGENTS}

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


def test_install_generic_agent_writes_seed_and_data_dir(
    manager: AgentManager,
    stub_drivers: dict[str, _StubDriver],
) -> None:
    rec = manager.install("widget", bearer_token="hal0_tok_abc")
    assert rec.name == "widget"
    assert rec.status == "installed"
    # Driver got the token verbatim — confirms wiring from manager →
    # driver is straight through.
    assert stub_drivers["widget"].installs == ["hal0_tok_abc"]

    # Seed TOML present + parseable.
    seed = Path(rec.config_path)
    assert seed.exists()
    parsed = tomllib.loads(seed.read_text())
    assert parsed["agent"]["name"] == "widget"
    assert parsed["agent"]["version_pin"] is False  # ADR-0004 §3

    # Per-agent data dir provisioned.
    assert Path(rec.data_dir).is_dir()


def test_list_after_install_returns_one_record(
    manager: AgentManager,
    stub_drivers: dict[str, _StubDriver],
) -> None:
    manager.install("widget")
    listing = manager.list()
    assert len(listing) == 1
    assert listing[0].name == "widget"
    assert manager.installed_names() == ["widget"]


# ── install: idempotent re-install ───────────────────────────────────────────


def test_install_same_agent_twice_is_noop(
    manager: AgentManager,
    stub_drivers: dict[str, _StubDriver],
) -> None:
    rec1 = manager.install("widget")
    rec2 = manager.install("widget")
    assert rec1.name == rec2.name == "widget"
    # Driver invoked exactly once — second call hit the
    # already-installed short-circuit.
    assert len(stub_drivers["widget"].installs) == 1


# ── install: single-pick enforcement ─────────────────────────────────────────


def test_install_second_agent_without_switch_raises(
    manager: AgentManager,
    stub_drivers: dict[str, _StubDriver],
) -> None:
    manager.install("widget")
    with pytest.raises(AgentAlreadyInstalledError) as exc:
        manager.install("hermes")
    # Error message should name BOTH agents so the operator sees why.
    msg = str(exc.value)
    assert "widget" in msg
    assert "hermes" in msg
    # Hermes driver was NOT invoked.
    assert stub_drivers["hermes"].installs == []
    # widget still the installed one.
    assert manager.installed_names() == ["widget"]


def test_install_with_switch_swaps_atomically(
    manager: AgentManager,
    stub_drivers: dict[str, _StubDriver],
) -> None:
    manager.install("widget")
    rec = manager.install("hermes", switch=True)
    assert rec.name == "hermes"
    # widget uninstall fired exactly once.
    assert stub_drivers["widget"].uninstalls == 1
    # Only hermes is now installed.
    assert manager.installed_names() == ["hermes"]


def test_switch_helper_equivalent_to_install_with_switch_true(
    manager: AgentManager,
    stub_drivers: dict[str, _StubDriver],
) -> None:
    manager.install("widget")
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
    rec = manager.install("widget")
    seed = Path(rec.config_path)
    data = Path(rec.data_dir)
    assert seed.exists() and data.exists()

    manager.uninstall("widget")
    assert not seed.exists()
    assert not data.exists()
    assert manager.installed_names() == []
    assert stub_drivers["widget"].uninstalls == 1


def test_uninstall_when_not_installed_is_noop(
    manager: AgentManager,
    stub_drivers: dict[str, _StubDriver],
) -> None:
    # Should not raise — idempotent posture mirrors slot-delete and
    # the /api/agents DELETE route's "not_installed" return.
    manager.uninstall("widget")
    # Driver's uninstall still runs (best-effort cleanup) — but no
    # disk state to remove.
    assert stub_drivers["widget"].uninstalls == 1


# ── install: coexistence (ADR-0004 §2 amendment — hermes + turnstone) ────────


def test_hermes_and_turnstone_coexist_without_switch(
    manager: AgentManager,
    stub_drivers: dict[str, _StubDriver],
) -> None:
    # Both are in COEXISTING_AGENTS — installing turnstone alongside hermes
    # needs no --switch and must NOT tear hermes down.
    manager.install("hermes")
    rec = manager.install("turnstone")
    assert rec.name == "turnstone"
    assert stub_drivers["hermes"].uninstalls == 0
    assert set(manager.installed_names()) == {"hermes", "turnstone"}


def test_turnstone_over_noncoexisting_agent_requires_switch(
    manager: AgentManager,
    stub_drivers: dict[str, _StubDriver],
) -> None:
    # widget is NOT coexisting, so single-pick still bites.
    manager.install("widget")
    with pytest.raises(AgentAlreadyInstalledError) as exc:
        manager.install("turnstone")
    assert "widget" in str(exc.value)
    assert "turnstone" in str(exc.value)
    assert stub_drivers["turnstone"].installs == []
    assert manager.installed_names() == ["widget"]


def test_turnstone_over_noncoexisting_agent_with_switch_clears_it(
    manager: AgentManager,
    stub_drivers: dict[str, _StubDriver],
) -> None:
    manager.install("widget")
    rec = manager.install("turnstone", switch=True)
    assert rec.name == "turnstone"
    assert stub_drivers["widget"].uninstalls == 1
    assert manager.installed_names() == ["turnstone"]


def test_noncoexisting_agent_over_coexisting_pair_clears_both_with_switch(
    manager: AgentManager,
    stub_drivers: dict[str, _StubDriver],
) -> None:
    # A non-coexisting agent installed over the hermes+turnstone pair must
    # tear DOWN both (they both block it) under --switch.
    manager.install("hermes")
    manager.install("turnstone")
    rec = manager.install("widget", switch=True)
    assert rec.name == "widget"
    assert stub_drivers["hermes"].uninstalls == 1
    assert stub_drivers["turnstone"].uninstalls == 1
    assert manager.installed_names() == ["widget"]


def test_noncoexisting_agent_over_coexisting_pair_without_switch_raises(
    manager: AgentManager,
    stub_drivers: dict[str, _StubDriver],
) -> None:
    manager.install("hermes")
    manager.install("turnstone")
    with pytest.raises(AgentAlreadyInstalledError):
        manager.install("widget")
    # Neither incumbent torn down.
    assert stub_drivers["hermes"].uninstalls == 0
    assert stub_drivers["turnstone"].uninstalls == 0


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


def _seed_managed_home(manager: AgentManager, name: str) -> Path:
    """Helper: simulate the agent provisioner claiming a converged home.

    For agents whose data dir is a canonical home (hermes → HERMES_HOME),
    the manager itself no longer mkdir's the tree — the provisioner does,
    stamping the ``.hal0-managed`` marker (#453). The stub driver used in
    these tests does neither, so tests that need a removable data dir
    must create the marked home the way the real provisioner would."""
    home = manager._data_dir(name)
    home.mkdir(parents=True, exist_ok=True)
    (home / ".hal0-managed").write_text("hal0\n")
    return home


def test_uninstall_removes_state_dir(
    manager: AgentManager,
    stub_drivers: dict[str, _StubDriver],
) -> None:
    """#346 (acceptance #1): ``mgr.uninstall(name)`` removes
    ``/var/lib/hal0/state/agents/<name>/`` in addition to the seed TOML
    + data dir."""
    manager.install("hermes")
    _seed_managed_home(manager, "hermes")
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
    _seed_managed_home(manager, "hermes")
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
        _seed_managed_home(manager, "hermes")
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


def test_read_record_consults_driver_when_seed_missing(
    manager: AgentManager,
    stub_drivers: dict[str, _StubDriver],
) -> None:
    """#432: a running agent with on-disk artifacts but NO /etc seed must
    report ``installed``, not ``broken``.

    The ``hal0 agent bootstrap hermes`` path used to leave the data +
    state dirs on disk without writing the seed TOML, so ``_read_record``
    short-circuited to ``broken`` before ever consulting driver health —
    even though the agent was up and reachable. Now the missing-seed
    branch defers to ``driver.status()``."""
    # Synthesise the half-state: data dir present, seed absent.
    manager._data_dir("hermes").mkdir(parents=True)
    assert not manager._config_path("hermes").exists()
    # Driver reports the live agent as reachable.
    stub_drivers["hermes"]._installed = True

    listing = manager.list()
    assert len(listing) == 1
    assert listing[0].name == "hermes"
    assert listing[0].status == "installed", (
        "missing-seed + running agent reported 'broken' — #432 regression"
    )
    # No seed → no recorded install timestamp.
    assert listing[0].installed_at == ""


def test_read_record_stays_broken_when_driver_unreachable(
    manager: AgentManager,
    stub_drivers: dict[str, _StubDriver],
) -> None:
    """#432: the missing-seed branch only upgrades to ``installed`` when
    the driver actually reports reachable. A dead agent with orphan
    artifacts must stay ``broken`` so the repair affordance is reachable
    (#346)."""
    manager._state_dir("hermes").mkdir(parents=True)
    stub_drivers["hermes"]._installed = False

    listing = manager.list()
    assert len(listing) == 1
    assert listing[0].status == "broken"


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


def test_switch_failed_install_rolls_back_incumbent(
    manager: AgentManager,
    stub_drivers: dict[str, _StubDriver],
) -> None:
    """If the target's install fails AFTER the incumbent was torn down
    (precondition passed, but the driver blew up deeper in), the
    manager makes a best-effort attempt to reinstall the incumbent
    rather than leaving the operator with NO agent installed.

    Regression test: switching a non-coexisting incumbent to hermes used
    to uninstall the incumbent unconditionally, then leave the box with
    nothing installed (and a crash-looping systemd unit) when hermes'
    install blew up. ADR-0004 §2 promises the operator never ends up
    with two bundled agents partially installed — it does NOT say they
    should end up with zero.
    """
    manager.install("widget")

    # Make hermes' install raise after widget is uninstalled. Hermes
    # has no installer script (_SCRIPT_INSTALLED_AGENTS excludes it),
    # so the pre-uninstall precondition check is a no-op here and the
    # failure surfaces from the driver itself, same as upstream really
    # blowing up mid-install.
    stubs = stub_drivers

    def _boom(*, bearer_token: str | None = None) -> None:
        raise RuntimeError("simulated upstream-broke")

    stubs["hermes"].install = _boom  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="simulated upstream-broke"):
        manager.install("hermes", switch=True)

    # widget was uninstalled then rolled back; hermes never got a
    # seed written.
    assert manager.installed_names() == ["widget"]
    assert stubs["widget"].uninstalls == 1
    assert stubs["widget"].installs == [None, None]  # initial install + rollback


def test_switch_aborts_without_uninstalling_when_target_script_missing(
    manager: AgentManager,
    stub_drivers: dict[str, _StubDriver],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#<bug2>: when the target's installer script doesn't exist on
    disk, ``install(switch=True)`` must refuse the swap WITHOUT
    uninstalling the incumbent — the precondition check runs before
    any teardown. Regression for the "switching to a wheel install
    missing its bundled-agent scripts bricked the incumbent" bug.

    hal0 v0.3's real bundled agents (hermes, turnstone) don't install via
    shell script, so :data:`_SCRIPT_INSTALLED_AGENTS` is empty in
    production (the two agents that used to populate it, pi-coder and
    opencode, are gone — refs P1-drivers). "gizmo" stands in for a
    future script-installed bundled agent so this precondition stays
    under test.
    """
    stub_drivers["gizmo"] = _StubDriver("gizmo")
    monkeypatch.setattr(mgr_mod, "BUNDLED_AGENTS", (*mgr_mod.BUNDLED_AGENTS, "gizmo"))
    monkeypatch.setattr(mgr_mod, "_SCRIPT_INSTALLED_AGENTS", frozenset({"gizmo"}))

    manager.install("hermes")

    monkeypatch.setattr(
        mgr_mod,
        "installer_script_path",
        lambda name: Path("/nonexistent/installer/agents") / f"{name}.sh",
    )

    with pytest.raises(mgr_mod.AgentError, match="installer script missing"):
        manager.install("gizmo", switch=True)

    # Incumbent untouched — precondition failed before any teardown.
    assert manager.installed_names() == ["hermes"]
    assert stub_drivers["hermes"].uninstalls == 0
    assert stub_drivers["gizmo"].installs == []


# ── #453: converge hermes data_dir onto HERMES_HOME (.hermes) ─────────────────


def test_hermes_data_dir_is_hermes_home(manager: AgentManager, tmp_path: Path) -> None:
    """#453: the manager's data_dir for hermes must be the canonical
    HERMES_HOME (``<var_lib>/.hermes``), NOT the legacy
    ``<var_lib>/agents/hermes`` tree. The provisioner + systemd units
    use ``/var/lib/hal0/.hermes``; the registry must agree or
    status/list/uninstall act on a dead path."""
    # var_root is tmp_path/"var" → var_lib is tmp_path → home is tmp_path/.hermes.
    assert manager._data_dir("hermes") == tmp_path / ".hermes"
    # Non-converged agents keep the legacy per-name layout.
    assert manager._data_dir("widget") == tmp_path / "var" / "widget"


def test_hermes_install_records_hermes_home_as_data_dir(
    manager: AgentManager,
    stub_drivers: dict[str, _StubDriver],
    tmp_path: Path,
) -> None:
    """#453: the seed TOML + AgentRecord must carry the converged home so
    ``hal0 agent status hermes`` reports ``data: <var_lib>/.hermes``."""
    rec = manager.install("hermes")
    assert rec.data_dir == str(tmp_path / ".hermes")
    parsed = tomllib.loads(manager._config_path("hermes").read_text())
    assert parsed["data_dir"] == str(tmp_path / ".hermes")
    # Re-seed must NOT recreate the legacy /agents/hermes tree.
    assert not (tmp_path / "var" / "hermes").exists()


def test_hermes_uninstall_removes_managed_home(
    manager: AgentManager,
    stub_drivers: dict[str, _StubDriver],
    tmp_path: Path,
) -> None:
    """#453: uninstall removes the live HERMES_HOME when it carries the
    ``.hal0-managed`` marker."""
    manager.install("hermes")
    home = _seed_managed_home(manager, "hermes")
    (home / "plugins").mkdir()
    assert home == tmp_path / ".hermes"
    assert home.exists()

    removed = manager.uninstall("hermes")
    assert removed is True
    assert not home.exists()


def test_hermes_uninstall_refuses_unmanaged_home(
    manager: AgentManager,
    stub_drivers: dict[str, _StubDriver],
    tmp_path: Path,
) -> None:
    """#453 (safety guard): uninstall must NOT rmtree a HERMES_HOME that
    lacks the ``.hal0-managed`` marker — a user's pre-existing ~/.hermes
    or a shared tree. Refuse rather than nuke someone else's data."""
    home = tmp_path / ".hermes"
    home.mkdir(parents=True)
    (home / "user-data.txt").write_text("precious\n")  # no marker
    # Seed exists so the agent counts as installed.
    manager._etc_root.mkdir(parents=True, exist_ok=True)
    manager._config_path("hermes").write_text("")

    manager.uninstall("hermes")
    # Home + its contents survive — only the seed was removed.
    assert home.exists()
    assert (home / "user-data.txt").exists()
    assert not manager._config_path("hermes").exists()


# ── installer_script_path: FHS-aware resolution for wheel installs ───────────


def test_installer_script_path_resolves_editable_when_present() -> None:
    """Editable / dev checkout: this test runs against the real
    checkout, which has ``installer/agents/turnstone.sh`` three parents
    up from ``src/hal0/agents/manager.py`` — no monkeypatching needed,
    this exercises the real resolution path. (turnstone's own driver
    doesn't actually call this function — see :data:`_SCRIPT_INSTALLED_AGENTS`
    — but the function itself is name-agnostic and turnstone.sh is the
    only real installer script left in the tree post P1-drivers.)"""
    resolved = mgr_mod.installer_script_path("turnstone")
    assert resolved.is_file()
    assert resolved == (
        Path(mgr_mod.__file__).resolve().parents[3] / "installer" / "agents" / "turnstone.sh"
    )


def test_installer_script_path_falls_back_to_fhs_for_wheel_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-editable wheel install has ``manager.py`` under
    ``<venv>/lib/pythonX/site-packages/hal0/agents/manager.py`` — three
    parents up from there is a venv dir, not a repo root, so it has no
    ``installer/`` sibling. The function must fall back to the FHS code
    root (:func:`hal0.config.paths.usr_lib`, ``/usr/lib/hal0/current``
    in production) and find the script there — this was the root cause
    of ``hal0 agent install <name>`` 500ing on a real FHS install."""
    fake_module_path = (
        tmp_path
        / "venv"
        / "lib"
        / "python3.12"
        / "site-packages"
        / "hal0"
        / "agents"
        / "manager.py"
    )
    fake_module_path.parent.mkdir(parents=True)
    monkeypatch.setattr(mgr_mod, "__file__", str(fake_module_path))

    fhs_root = tmp_path / "usr-lib-hal0-current"
    script_dir = fhs_root / "installer" / "agents"
    script_dir.mkdir(parents=True)
    fhs_script = script_dir / "widget.sh"
    fhs_script.write_text("#!/bin/sh\n")
    monkeypatch.setattr(mgr_mod._paths, "usr_lib", lambda: fhs_root)

    resolved = mgr_mod.installer_script_path("widget")
    assert resolved == fhs_script
    assert resolved.is_file()


def test_installer_script_path_prefers_editable_when_both_exist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When an editable-shaped repo root has the script, it wins over
    the FHS candidate (resolution order: editable first, FHS fallback)."""
    fake_module_path = tmp_path / "repo" / "src" / "hal0" / "agents" / "manager.py"
    fake_module_path.parent.mkdir(parents=True)
    monkeypatch.setattr(mgr_mod, "__file__", str(fake_module_path))

    editable_script_dir = tmp_path / "repo" / "installer" / "agents"
    editable_script_dir.mkdir(parents=True)
    editable_script = editable_script_dir / "widget.sh"
    editable_script.write_text("#!/bin/sh\n")

    fhs_root = tmp_path / "fhs"
    fhs_script_dir = fhs_root / "installer" / "agents"
    fhs_script_dir.mkdir(parents=True)
    (fhs_script_dir / "widget.sh").write_text("#!/bin/sh\n")
    monkeypatch.setattr(mgr_mod._paths, "usr_lib", lambda: fhs_root)

    resolved = mgr_mod.installer_script_path("widget")
    assert resolved == editable_script


def test_installer_script_path_missing_everywhere_returns_fhs_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the script exists in neither location, the function still
    returns a path rather than raising/None — the FHS candidate — so
    the caller's "installer script missing" error points at the real
    production path, not a venv path nobody would recognise."""
    fake_module_path = (
        tmp_path
        / "venv"
        / "lib"
        / "python3.12"
        / "site-packages"
        / "hal0"
        / "agents"
        / "manager.py"
    )
    fake_module_path.parent.mkdir(parents=True)
    monkeypatch.setattr(mgr_mod, "__file__", str(fake_module_path))

    fhs_root = tmp_path / "usr-lib-hal0-current"
    monkeypatch.setattr(mgr_mod._paths, "usr_lib", lambda: fhs_root)

    resolved = mgr_mod.installer_script_path("widget")
    assert resolved == fhs_root / "installer" / "agents" / "widget.sh"
    assert not resolved.is_file()
