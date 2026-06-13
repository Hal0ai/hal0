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
    # geteuid-guarded so it no-ops under the test runner anyway.
    monkeypatch.setattr("shutil.which", lambda _n: None)

    ac._install_hermes(switch=True)

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

    with pytest.raises((SystemExit, typer.Exit)):
        ac._install_hermes(switch=False)

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
