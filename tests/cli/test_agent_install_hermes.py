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

import hal0.cli.agent_commands as ac


class _Rec:
    """Records calls in order so the test can assert sequencing."""

    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []


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

    ac._install_hermes_gateway()

    assert [ac._HERMES_BIN, "gateway", "install", "--system", "--run-as-user", "hal0"] in calls
    assert ["systemctl", "daemon-reload"] in calls
    assert ["systemctl", "enable", "--now", "hermes-gateway.service"] in calls


def test_install_hermes_gateway_warns_without_raising_when_unit_missing(monkeypatch) -> None:
    """`hermes gateway install` failing to lay down the unit must not raise —
    best-effort, matches install.sh's `|| warn ... continuing` posture."""
    monkeypatch.setattr(ac, "_hermes_venv_ready", lambda: True)
    monkeypatch.setattr(ac, "_HERMES_GATEWAY_UNIT", "/nonexistent/hermes-gateway.service")
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: type("_D", (), {"returncode": 1})())

    ac._install_hermes_gateway()  # must not raise
