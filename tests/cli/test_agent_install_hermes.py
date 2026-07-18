"""`hal0 agent install hermes` foreground-provision flow.

Regression for the clean-install 409 loop (upstream `hermes` not on the
daemon's PATH). Hermes now provisions into the hal0-managed venv via the
foreground CLI rather than gating on a pre-existing pipx install. This
test pins the call sequence: toolchain prereqs → bootstrap pipeline →
best-effort daemon register/switch.
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

import hal0.cli.agent_commands as ac


class _Rec:
    """Records calls in order so the test can assert sequencing."""

    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []


class _FakeAgentManager:
    """Stand-in for :class:`hal0.agents.manager.AgentManager` — records
    ``uninstall`` calls without touching disk, and reports a fixed
    ``installed_names()`` so tests control the single-pick check
    (:func:`hal0.cli.agent_commands._enforce_hermes_single_pick`)
    deterministically, independent of whatever's really on the host box.
    """

    def __init__(self, installed: list[str] | None = None) -> None:
        self._installed = list(installed or [])
        self.uninstalled: list[str] = []

    def installed_names(self) -> list[str]:
        return list(self._installed)

    def uninstall(self, name: str) -> bool:
        self.uninstalled.append(name)
        if name in self._installed:
            self._installed.remove(name)
            return True
        return False


@pytest.fixture(autouse=True)
def _fake_bundled_agent_manager(monkeypatch: pytest.MonkeyPatch) -> _FakeAgentManager:
    """Insulate every test in this module from real host `/etc/hal0` agent
    state and from single-pick enforcement (Finding 11) by default — no
    incumbent, so ``_install_hermes`` proceeds exactly like before this
    fixture existed. Tests exercising the single-pick check itself
    override this via their own ``monkeypatch.setattr(ac,
    "_bundled_agent_manager", ...)`` call (last setattr wins).
    """
    fake = _FakeAgentManager([])
    monkeypatch.setattr(ac, "_bundled_agent_manager", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def _euid_nonroot_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default the CLI install suite to a NON-root euid — the in-process
    provisioning path (§7.4 _provision_hermes). Keeps the suite hermetic even
    when tests/agents/conftest.py (which patches the global os.geteuid to 0)
    runs in the same session. The root-drop test overrides per-test.
    """
    monkeypatch.setattr("os.geteuid", lambda: 1000)


def test_install_hermes_runs_prereqs_then_bootstrap_then_register(
    monkeypatch,
) -> None:
    rec = _Rec()

    def _fake_subprocess_run(argv, *_a, **_k):  # type: ignore[no-untyped-def]
        rec.events.append(("subprocess", list(argv)))

        class _Done:
            returncode = 0

        return _Done()

    def _fake_bootstrap_cli(**kwargs):  # type: ignore[no-untyped-def]
        rec.events.append(("bootstrap_cli", kwargs))
        return 0

    def _fake_api_post(path, *, json=None, **_k):  # type: ignore[no-untyped-def]
        rec.events.append(("api_post", (path, json)))
        return {}

    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run)
    monkeypatch.setattr(
        "hal0.agents.hermes_provision.bootstrap_cli", _fake_bootstrap_cli, raising=True
    )
    monkeypatch.setattr(ac, "api_post", _fake_api_post)
    monkeypatch.setattr(ac, "_api_unreachable", lambda _url: False)
    # Isolate the core sequence: no systemctl (skip enable/start). chown is
    # geteuid-guarded so it no-ops under the test runner anyway. The
    # privilege/writability guard is its own concern (tested below) — neutralise
    # it here so this test exercises only the toolchain→bootstrap→register order.
    monkeypatch.setattr("shutil.which", lambda _n: None)
    monkeypatch.setattr(ac, "_ensure_hermes_writable_or_die", lambda: None)
    # (§7.4 F.7 removed the post-provision chown-back; nothing to neutralise —
    # provisioning drops to hal0 so the trees are born hal0:hal0.)

    # Gateway wiring is its own concern (tested in the "gateway" section
    # below) — disable it here so this test exercises only the
    # toolchain->bootstrap->register sequence.
    ac._install_hermes(switch=True, gateway=False)

    kinds = [e[0] for e in rec.events]
    # Toolchain prereqs run BEFORE provisioning; provisioning BEFORE register.
    assert kinds == ["subprocess", "bootstrap_cli", "api_post"], kinds

    # Step 1 shells the prereq script.
    assert rec.events[0][1][0] == "bash"
    assert rec.events[0][1][1].endswith("/installer/agents/hermes-prereqs.sh")

    # Step 3 registers via the API and forwards --switch.
    path, payload = rec.events[2][1]
    assert path == "/api/agents/install"
    assert payload == {"name": "hermes", "switch": True}


def test_install_hermes_forwards_adopt_to_bootstrap(monkeypatch) -> None:
    """`hal0 agent install hermes --adopt` threads --adopt into the bootstrap
    pipeline so the capture (backup + token import + claim) actually happens."""
    seen: dict[str, object] = {}

    def _fake_bootstrap_cli(**kwargs):  # type: ignore[no-untyped-def]
        seen.update(kwargs)
        return 0

    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: type("_D", (), {"returncode": 0})())
    monkeypatch.setattr(
        "hal0.agents.hermes_provision.bootstrap_cli", _fake_bootstrap_cli, raising=True
    )
    monkeypatch.setattr(ac, "api_post", lambda *_a, **_k: {})
    monkeypatch.setattr(ac, "_api_unreachable", lambda _url: False)
    monkeypatch.setattr(ac, "_ensure_hermes_writable_or_die", lambda: None)
    monkeypatch.setattr("shutil.which", lambda _n: None)

    ac._install_hermes(switch=False, gateway=False, adopt=True)
    assert seen.get("adopt") is True


def test_install_hermes_aborts_when_provisioning_fails(monkeypatch) -> None:
    """A non-zero bootstrap rc must stop the flow before the API register —
    we don't want to mark a half-provisioned agent installed."""
    rec = _Rec()

    def _fake_subprocess_run(argv, *_a, **_k):  # type: ignore[no-untyped-def]
        class _Done:
            returncode = 0

        return _Done()

    def _fail_bootstrap(**_k):  # type: ignore[no-untyped-def]
        return 3

    def _fake_api_post(path, *, json=None, **_k):  # type: ignore[no-untyped-def]
        rec.events.append(("api_post", (path, json)))
        return {}

    # die() raises SystemExit/typer.Exit — assert it stops us.
    import pytest
    import typer

    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run)
    monkeypatch.setattr("hal0.agents.hermes_provision.bootstrap_cli", _fail_bootstrap, raising=True)
    monkeypatch.setattr(ac, "api_post", _fake_api_post)
    monkeypatch.setattr(ac, "_api_unreachable", lambda _url: False)
    monkeypatch.setattr(ac, "_ensure_hermes_writable_or_die", lambda: None)

    with pytest.raises((SystemExit, typer.Exit)):
        ac._install_hermes(switch=False, gateway=False)

    assert rec.events == [], "must not register after a failed provision"


def test_enable_and_start_unit_invokes_systemctl_when_present(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr("shutil.which", lambda _n: "/usr/bin/systemctl")

    def _fake_run(argv, *_a, **_k):
        calls.append(list(argv))

        class _Done:
            returncode = 0

        return _Done()

    monkeypatch.setattr(subprocess, "run", _fake_run)
    ac._enable_and_start_hermes_unit()
    assert calls == [["systemctl", "enable", "--now", "hal0-agent@hermes"]]


def test_enable_and_start_unit_noops_without_systemd(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _n: None)
    called = {"ran": False}

    def _fake_run(*_a, **_k):
        called["ran"] = True

        class _Done:
            returncode = 0

        return _Done()

    monkeypatch.setattr(subprocess, "run", _fake_run)
    ac._enable_and_start_hermes_unit()
    assert called["ran"] is False


# ── privilege/writability guard (issue: Fedora non-root `agent install`) ─────
#
# `hal0 agent install hermes` provisions into root-owned /var/lib/hal0 and is
# built to run as root on a system install (it chowns the trees to the `hal0`
# agent user afterwards). Run as a normal login user it used to crash several
# phases deep with a raw PermissionError and leave half-owned trees behind.
# The guard must abort BEFORE the toolchain/bootstrap steps, with a sudo hint.


def test_install_hermes_guard_aborts_non_root_when_unwritable(monkeypatch) -> None:
    import os

    import pytest

    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    monkeypatch.setattr("hal0.agents.hermes_provision.path_is_writable", lambda _p: False)

    ran = {"toolchain": False, "bootstrap": False}
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: ran.__setitem__("toolchain", True))
    monkeypatch.setattr(
        "hal0.agents.hermes_provision.bootstrap_cli",
        lambda **_k: ran.__setitem__("bootstrap", True),
        raising=True,
    )

    with pytest.raises(SystemExit):
        ac._install_hermes(switch=False)

    # The guard fired first: no toolchain shell-out, no bootstrap, no half-state.
    assert ran == {"toolchain": False, "bootstrap": False}


def test_install_hermes_guard_noop_when_root(monkeypatch) -> None:
    """Root writes anywhere — the guard must not even probe the filesystem."""
    import os

    monkeypatch.setattr(os, "geteuid", lambda: 0)

    def _boom(_p):  # pragma: no cover - must never be called
        raise AssertionError("path_is_writable probed despite running as root")

    monkeypatch.setattr("hal0.agents.hermes_provision.path_is_writable", _boom)
    ac._ensure_hermes_writable_or_die()  # returns cleanly, no raise


def test_install_hermes_guard_noop_when_writable(monkeypatch) -> None:
    """Dev / rootless install already owns the trees — proceed silently."""
    import os

    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    monkeypatch.setattr("hal0.agents.hermes_provision.path_is_writable", lambda _p: True)
    ac._ensure_hermes_writable_or_die()  # no raise


# ── Gateway (Telegram/Discord) fold-in — issue #1102 / Q9 ────────────────────
#
# installer/install.sh only wires the gateway when THAT run just provisioned
# (or found already-provisioned) Hermes — a box that deferred Hermes at
# install time (HAL0_SKIP_HERMES=1) and installs it later via this CLI never
# hit that bash block. These tests pin the deferred path to the same wiring.


def test_install_hermes_runs_gateway_by_default(monkeypatch) -> None:
    """`hal0 agent install hermes` (no flags) enables the gateway — parity
    with install-time provisioning, no opt-in required."""
    rec = _Rec()

    def _fake_subprocess_run(argv, *_a, **_k):  # type: ignore[no-untyped-def]
        class _Done:
            returncode = 0

        return _Done()

    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run)
    monkeypatch.setattr("hal0.agents.hermes_provision.bootstrap_cli", lambda **_k: 0, raising=True)
    monkeypatch.setattr(ac, "api_post", lambda *_a, **_k: {})
    monkeypatch.setattr(ac, "_api_unreachable", lambda _url: False)
    monkeypatch.setattr(ac, "_ensure_hermes_writable_or_die", lambda: None)
    monkeypatch.setattr("shutil.which", lambda _n: None)
    monkeypatch.setattr(ac, "_install_hermes_gateway", lambda: rec.events.append(("gateway", None)))

    ac._install_hermes(switch=False)  # gateway defaults to True

    assert ("gateway", None) in rec.events


def test_install_hermes_no_gateway_flag_skips_it(monkeypatch) -> None:
    rec = _Rec()

    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: type("_D", (), {"returncode": 0})())
    monkeypatch.setattr("hal0.agents.hermes_provision.bootstrap_cli", lambda **_k: 0, raising=True)
    monkeypatch.setattr(ac, "api_post", lambda *_a, **_k: {})
    monkeypatch.setattr(ac, "_api_unreachable", lambda _url: False)
    monkeypatch.setattr(ac, "_ensure_hermes_writable_or_die", lambda: None)
    monkeypatch.setattr("shutil.which", lambda _n: None)
    monkeypatch.setattr(ac, "_install_hermes_gateway", lambda: rec.events.append(("gateway", None)))

    ac._install_hermes(switch=False, gateway=False)

    assert rec.events == []


def test_install_hermes_gateway_noop_when_venv_missing(monkeypatch) -> None:
    """No provisioned hermes binary → nothing to wire the gateway onto."""
    monkeypatch.setattr(ac, "_hermes_venv_ready", lambda: False)
    called = {"ran": False}
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: called.__setitem__("ran", True))

    ac._install_hermes_gateway()

    assert called["ran"] is False


def test_install_hermes_gateway_installs_and_enables_unit(monkeypatch, tmp_path) -> None:
    """Happy path: gateway install runs, the unit file lands, systemctl
    enables + confirms it active — mirrors installer/install.sh's block."""
    gateway_unit = tmp_path / "hermes-gateway.service"

    calls: list[list[str]] = []

    def _fake_run(argv, *_a, **_k):  # type: ignore[no-untyped-def]
        calls.append(list(argv))
        # The real `hermes gateway install` writes the unit file as a side
        # effect; simulate that here so the "unit landed" branch runs.
        if argv[0] == ac._HERMES_BIN:
            gateway_unit.write_text("[Unit]\n")

        class _Done:
            returncode = 0

        return _Done()

    monkeypatch.setattr(ac, "_hermes_venv_ready", lambda: True)
    monkeypatch.setattr(ac, "_HERMES_GATEWAY_UNIT", str(gateway_unit))
    monkeypatch.setattr(subprocess, "run", _fake_run)
    monkeypatch.setattr("shutil.which", lambda _n: "/usr/bin/systemctl")
    monkeypatch.setattr(ac, "_wait_active_unit", lambda _unit, timeout=15.0: True)
    # No foreign gateway on this box → the enable gate is a no-op. Stub it so
    # the test is hermetic regardless of the host's real systemd/drop-in state.
    monkeypatch.setattr("hal0.agents.hermes_provision._detect_foreign_gateways", lambda **_k: [])

    ac._install_hermes_gateway()

    assert [ac._HERMES_BIN, "gateway", "install", "--system", "--run-as-user", "hal0"] in calls
    assert ["systemctl", "daemon-reload"] in calls
    assert ["systemctl", "enable", "--now", "hermes-gateway.service"] in calls


def test_install_hermes_gateway_writes_dropin_before_gateway_install(monkeypatch, tmp_path) -> None:
    """The secrets drop-in must be laid down BEFORE `hermes gateway install`
    starts the (start-now) vanilla unit — otherwise hal0 flags its own active,
    drop-in-less unit as a foreign poller and never wires the bridge."""
    import hal0.agents.hermes_provision as hp

    gateway_unit = tmp_path / "hermes-gateway.service"
    events: list[str] = []

    def _fake_dropin(**_k):  # type: ignore[no-untyped-def]
        events.append("dropin")
        return hp.GatewayDropinResult(outcome="written", dropin_path=str(gateway_unit))

    def _fake_run(argv, *_a, **_k):  # type: ignore[no-untyped-def]
        if argv[0] == ac._HERMES_BIN:
            events.append("gateway-install")
            gateway_unit.write_text("[Unit]\n")
        else:
            events.append(" ".join(argv))

        class _Done:
            returncode = 0

        return _Done()

    monkeypatch.setattr(ac, "_hermes_venv_ready", lambda: True)
    monkeypatch.setattr(ac, "_HERMES_GATEWAY_UNIT", str(gateway_unit))
    monkeypatch.setattr(subprocess, "run", _fake_run)
    monkeypatch.setattr("shutil.which", lambda _n: "/usr/bin/systemctl")
    monkeypatch.setattr(ac, "_wait_active_unit", lambda _unit, timeout=15.0: True)
    monkeypatch.setattr("hal0.agents.hermes_provision._detect_foreign_gateways", lambda **_k: [])
    monkeypatch.setattr("hal0.agents.hermes_provision.write_gateway_secrets_dropin", _fake_dropin)

    ac._install_hermes_gateway()

    assert "dropin" in events and "gateway-install" in events
    assert events.index("dropin") < events.index("gateway-install")


def test_install_hermes_gateway_skips_enable_on_foreign_gateway(monkeypatch, tmp_path) -> None:
    """A live foreign hermes-gateway means enabling hal0's unit too would put a
    SECOND poller on the same Telegram token (HTTP 409). The gate must skip the
    `systemctl enable --now` and leave the operator a stop command."""
    gateway_unit = tmp_path / "hermes-gateway.service"
    calls: list[list[str]] = []

    def _fake_run(argv, *_a, **_k):  # type: ignore[no-untyped-def]
        calls.append(list(argv))
        if argv[0] == ac._HERMES_BIN:
            gateway_unit.write_text("[Unit]\n")

        class _Done:
            returncode = 0

        return _Done()

    monkeypatch.setattr(ac, "_hermes_venv_ready", lambda: True)
    monkeypatch.setattr(ac, "_HERMES_GATEWAY_UNIT", str(gateway_unit))
    monkeypatch.setattr(subprocess, "run", _fake_run)
    monkeypatch.setattr("shutil.which", lambda _n: "/usr/bin/systemctl")
    monkeypatch.setattr(
        "hal0.agents.hermes_provision._detect_foreign_gateways",
        lambda **_k: [
            {
                "scope": "user",
                "detail": "user-scope hermes-gateway.service at /root/.config/...",
                "stop_cmd": "systemctl --user disable --now hermes-gateway.service",
            }
        ],
    )

    ac._install_hermes_gateway()  # must not raise

    # The unit install still ran, but hal0 did NOT enable/start a second poller.
    assert [ac._HERMES_BIN, "gateway", "install", "--system", "--run-as-user", "hal0"] in calls
    assert ["systemctl", "enable", "--now", "hermes-gateway.service"] not in calls


def test_install_hermes_gateway_warns_without_raising_when_unit_missing(monkeypatch) -> None:
    """`hermes gateway install` failing to lay down the unit must not raise —
    best-effort, matches install.sh's `|| warn ... continuing` posture."""
    monkeypatch.setattr(ac, "_hermes_venv_ready", lambda: True)
    monkeypatch.setattr(ac, "_HERMES_GATEWAY_UNIT", "/nonexistent/hermes-gateway.service")
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: type("_D", (), {"returncode": 1})())

    ac._install_hermes_gateway()  # must not raise


# ── Single-pick enforcement (Finding 11) ─────────────────────────────────────
#
# `hal0 agent install hermes` provisions locally (toolchain + bootstrap_cli)
# and writes the manager's seed TOML itself, well before it ever calls the
# daemon's /api/agents/install to honour --switch. That ordering used to let
# hermes install ALONGSIDE an existing pi-coder/opencode: by the time the
# daemon saw the request, hermes was already on disk, so AgentManager.install()
# took its "already installed" idempotent no-op path instead of raising
# AgentAlreadyInstalledError. _enforce_hermes_single_pick() closes the gap by
# checking disk-truth installed_names() up front, before any provisioning
# side effect.


def test_enforce_single_pick_noop_when_nothing_installed(monkeypatch) -> None:
    fake = _FakeAgentManager([])
    monkeypatch.setattr(ac, "_bundled_agent_manager", lambda: fake)

    ac._enforce_hermes_single_pick(switch=False)  # must not raise

    assert fake.uninstalled == []


def test_enforce_single_pick_noop_when_only_hermes_installed(monkeypatch) -> None:
    """A bare re-install of hermes itself (no other incumbent) is not a
    single-pick conflict — the manager's own idempotent no-op handles it
    downstream."""
    fake = _FakeAgentManager(["hermes"])
    monkeypatch.setattr(ac, "_bundled_agent_manager", lambda: fake)

    ac._enforce_hermes_single_pick(switch=False)  # must not raise

    assert fake.uninstalled == []


def test_enforce_single_pick_dies_on_incumbent_without_switch(monkeypatch) -> None:
    fake = _FakeAgentManager(["pi-coder"])
    monkeypatch.setattr(ac, "_bundled_agent_manager", lambda: fake)

    with pytest.raises(SystemExit):
        ac._enforce_hermes_single_pick(switch=False)

    # Refused before touching the incumbent.
    assert fake.uninstalled == []


def test_enforce_single_pick_uninstalls_incumbent_with_switch(monkeypatch) -> None:
    fake = _FakeAgentManager(["opencode"])
    monkeypatch.setattr(ac, "_bundled_agent_manager", lambda: fake)

    ac._enforce_hermes_single_pick(switch=True)  # must not raise

    assert fake.uninstalled == ["opencode"]


def test_install_hermes_refuses_when_incumbent_present_without_switch(monkeypatch) -> None:
    """End-to-end: `hal0 agent install hermes` (no --switch) against an
    existing pi-coder install must abort BEFORE the toolchain/bootstrap run
    — the actual regression this finding covers."""
    fake = _FakeAgentManager(["pi-coder"])
    monkeypatch.setattr(ac, "_bundled_agent_manager", lambda: fake)

    ran = {"toolchain": False, "bootstrap": False}
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: ran.__setitem__("toolchain", True))
    monkeypatch.setattr(
        "hal0.agents.hermes_provision.bootstrap_cli",
        lambda **_k: ran.__setitem__("bootstrap", True),
        raising=True,
    )

    with pytest.raises(SystemExit):
        ac._install_hermes(switch=False, gateway=False)

    assert ran == {"toolchain": False, "bootstrap": False}
    assert fake.uninstalled == []


def test_install_hermes_switch_uninstalls_incumbent_before_provisioning(monkeypatch) -> None:
    """`--switch` clears the incumbent first, THEN provisioning proceeds —
    same atomic-swap contract as AgentManager.install(switch=True)."""
    fake = _FakeAgentManager(["pi-coder"])
    monkeypatch.setattr(ac, "_bundled_agent_manager", lambda: fake)

    events: list[str] = []
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: type("_D", (), {"returncode": 0})())

    def _fake_bootstrap_cli(**_k):  # type: ignore[no-untyped-def]
        events.append("bootstrap_cli")
        return 0

    monkeypatch.setattr(
        "hal0.agents.hermes_provision.bootstrap_cli", _fake_bootstrap_cli, raising=True
    )
    monkeypatch.setattr(ac, "api_post", lambda *_a, **_k: {})
    monkeypatch.setattr(ac, "_api_unreachable", lambda _url: False)
    monkeypatch.setattr(ac, "_ensure_hermes_writable_or_die", lambda: None)
    monkeypatch.setattr("shutil.which", lambda _n: None)

    ac._install_hermes(switch=True, gateway=False)

    assert fake.uninstalled == ["pi-coder"]
    assert events == ["bootstrap_cli"]


# ── §7.4 privilege drop: _provision_hermes ───────────────────────────────────


def test_provision_hermes_non_root_runs_in_process(monkeypatch) -> None:
    """euid != 0 (dev / already-hal0): call bootstrap_cli in-process, no re-exec."""
    monkeypatch.setattr("os.geteuid", lambda: 1000)

    seen: dict[str, Any] = {}

    def _fake_bootstrap_cli(**kwargs):  # type: ignore[no-untyped-def]
        seen.update(kwargs)
        return 0

    monkeypatch.setattr(
        "hal0.agents.hermes_provision.bootstrap_cli", _fake_bootstrap_cli, raising=True
    )

    def _boom() -> None:
        raise AssertionError("root prelude must not run when non-root")

    monkeypatch.setattr(ac, "_hermes_root_prelude", _boom)
    monkeypatch.setattr(
        ac, "_run_as_hal0", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError())
    )

    rc = ac._provision_hermes(adopt=True, repair=True)

    assert rc == 0
    assert seen["adopt"] is True and seen["repair"] is True


def test_provision_hermes_root_drops_to_hal0(monkeypatch) -> None:
    """euid == 0: run the root prelude then re-exec `agent bootstrap hermes` as hal0."""
    monkeypatch.setattr("os.geteuid", lambda: 0)

    events: list[str] = []
    monkeypatch.setattr(ac, "_hermes_root_prelude", lambda: events.append("prelude"))
    monkeypatch.setattr("shutil.which", lambda _n: "/usr/local/bin/hal0")

    captured: dict[str, Any] = {}

    def _fake_run_as_hal0(argv: list[str]) -> int:
        captured["argv"] = argv
        events.append("run_as_hal0")
        return 0

    monkeypatch.setattr(ac, "_run_as_hal0", _fake_run_as_hal0)

    def _boom(**_k):  # type: ignore[no-untyped-def]
        raise AssertionError("root path must re-exec, not call bootstrap_cli in-process")

    monkeypatch.setattr("hal0.agents.hermes_provision.bootstrap_cli", _boom, raising=True)

    rc = ac._provision_hermes(repair=True, adopt=True, skip_phases=("mcp_wire",), verbose=True)

    assert rc == 0
    # Prelude runs BEFORE the drop.
    assert events == ["prelude", "run_as_hal0"]
    argv = captured["argv"]
    assert argv[:4] == ["/usr/local/bin/hal0", "agent", "bootstrap", "hermes"]
    assert "--repair" in argv and "--adopt" in argv and "--verbose" in argv
    assert argv[argv.index("--skip-phase") + 1] == "mcp_wire"


def test_run_as_hal0_builds_runuser_argv(monkeypatch) -> None:
    """_run_as_hal0 sanitizes the env (strip HERMES_HOME, set HOME) via runuser."""
    monkeypatch.setattr("shutil.which", lambda n: "/usr/sbin/runuser" if n == "runuser" else None)

    captured: dict[str, Any] = {}

    def _fake_run(cmd: list[str], **_k):  # type: ignore[no-untyped-def]
        captured["cmd"] = cmd
        return type("_D", (), {"returncode": 0})()

    # _run_as_hal0 imports subprocess locally; patch the module-level run.
    monkeypatch.setattr("subprocess.run", _fake_run)

    rc = ac._run_as_hal0(["/usr/local/bin/hal0", "agent", "bootstrap", "hermes"])

    assert rc == 0
    cmd = captured["cmd"]
    assert cmd[:4] == ["runuser", "-u", "hal0", "--"]
    assert "env" in cmd and "-u" in cmd and "HERMES_HOME" in cmd
    # The actual command is preserved at the tail.
    assert cmd[-4:] == ["/usr/local/bin/hal0", "agent", "bootstrap", "hermes"]
