"""The privilege-drop re-exec must run the RUNNING hal0, not PATH's (#2092).

``_provision_hermes``'s root branch drops to the hal0 user by re-exec'ing
``hal0 agent bootstrap hermes``. It resolved that binary with
``shutil.which("hal0")`` — i.e. whatever is first on PATH, which is not
necessarily the code doing the re-exec.

Measured on ct150: ``/usr/local/bin/hal0`` is a stale rc.3 wrapper (#1844) with
a ``#!/usr/bin/python3`` shebang that imports hal0 from system dist-packages.
Invoking ``/usr/lib/hal0/venv/bin/hal0`` (rc.11) as root therefore re-exec'd
**rc.3**, which resolved its installer root relative to its own package
location and failed with ``requirements.txt missing at
/usr/local/lib/python3.12/installer/agents/hermes/requirements.txt``.

The venv's console script sits next to the running interpreter, so
``Path(sys.executable).with_name("hal0")`` names the same tree that is
executing — and that is what must be preferred.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from hal0.cli import agent_commands


def test_prefers_the_console_script_next_to_the_running_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    running = venv_bin / "hal0"
    running.write_text("#!/bin/sh\n", encoding="utf-8")
    running.chmod(0o755)
    monkeypatch.setattr(sys, "executable", str(venv_bin / "python"))

    stale = tmp_path / "usr-local-bin" / "hal0"
    stale.parent.mkdir(parents=True)
    stale.write_text("#!/usr/bin/python3\n", encoding="utf-8")
    stale.chmod(0o755)
    monkeypatch.setattr(agent_commands.shutil, "which", lambda _n: str(stale))

    assert agent_commands._running_hal0_bin() == str(running)


def test_falls_back_to_path_when_no_sibling_script_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dev checkout run via ``python -m`` has no sibling console script; the
    old behaviour is still the best available answer there."""
    monkeypatch.setattr(sys, "executable", str(tmp_path / "nowhere" / "python"))
    monkeypatch.setattr(agent_commands.shutil, "which", lambda _n: "/usr/bin/hal0")

    assert agent_commands._running_hal0_bin() == "/usr/bin/hal0"


def test_falls_back_to_the_bare_name_when_nothing_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "executable", str(tmp_path / "nowhere" / "python"))
    monkeypatch.setattr(agent_commands.shutil, "which", lambda _n: None)

    assert agent_commands._running_hal0_bin() == "hal0"


def test_provision_reexec_uses_the_running_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End of the story: the argv the root branch hands to the privilege drop
    must start with the running hal0, not PATH's."""
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    running = venv_bin / "hal0"
    running.write_text("#!/bin/sh\n", encoding="utf-8")
    running.chmod(0o755)
    monkeypatch.setattr(sys, "executable", str(venv_bin / "python"))
    monkeypatch.setattr(agent_commands.shutil, "which", lambda _n: "/usr/local/bin/hal0")

    monkeypatch.setattr(agent_commands.os, "geteuid", lambda: 0)
    monkeypatch.setattr(agent_commands, "_hermes_root_prelude", lambda _env: None)

    seen: list[list[str]] = []

    def _fake_run_as_hal0(argv, *, stdin=None, extra_env=None):
        seen.append(argv)
        return 0

    monkeypatch.setattr(agent_commands, "_run_as_hal0", _fake_run_as_hal0)

    agent_commands._provision_hermes(repair=True)

    assert seen, "the root branch never re-exec'd"
    assert seen[0][0] == str(running)
    assert seen[0][1:4] == ["agent", "bootstrap", "hermes"]


def test_offline_and_ref_ride_the_reexec(monkeypatch: pytest.MonkeyPatch) -> None:
    """#2838 / ADR-0032 d6: --offline and the upgrade ref reach the hal0 child."""
    monkeypatch.setattr(agent_commands.os, "geteuid", lambda: 0)
    monkeypatch.setattr(agent_commands, "_hermes_root_prelude", lambda _env: None)
    monkeypatch.setattr(agent_commands, "_running_hal0_bin", lambda: "/x/hal0")
    seen: list[list[str]] = []
    monkeypatch.setattr(agent_commands, "_run_as_hal0", lambda argv, **_k: seen.append(argv) or 0)
    ref = "3ef6bbd201263d354fd83ec55b3c306ded2eb72a"
    agent_commands._provision_hermes(offline=True, ref=ref)
    assert seen == [["/x/hal0", "agent", "bootstrap", "hermes", "--offline", "--ref", ref]]


def test_in_process_offline_sets_the_variable_the_builder_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os

    monkeypatch.setattr(agent_commands.os, "geteuid", lambda: 1000)
    monkeypatch.delenv("HAL0_HERMES_OFFLINE", raising=False)
    seen: dict[str, object] = {}

    def _bootstrap(**kw: object) -> int:
        seen.update(kw, offline=os.environ.get("HAL0_HERMES_OFFLINE"))
        return 0

    monkeypatch.setattr("hal0.agents.hermes_provision.bootstrap_cli", _bootstrap)
    assert agent_commands._provision_hermes(offline=True, ref=None) == 0
    assert seen["offline"] == "1"
    assert "HAL0_HERMES_OFFLINE" not in os.environ  # restored afterwards


def test_root_prelude_owns_the_release_and_python_dirs_before_resolving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0032 decision 5: /var/lib/hal0/{hermes,python} exist before uv runs."""
    events: list[str] = []
    monkeypatch.setattr(
        agent_commands, "_ensure_hermes_prelude_dirs", lambda: events.append("dirs")
    )

    def _resolve() -> str:
        events.append("resolve")
        raise RuntimeError("stop here")

    monkeypatch.setattr(
        "hal0.agents.hermes_provision.sweep_stale_agent_dropins_as_root", lambda: None
    )
    monkeypatch.setattr("hal0.agents.hermes_provision.resolve_hermes_python", _resolve)
    with pytest.raises(RuntimeError):
        agent_commands._hermes_root_prelude({})
    assert events == ["dirs", "resolve"]
    assert "/var/lib/hal0/hermes" in agent_commands._HERMES_PRELUDE_DIRS
    assert "/var/lib/hal0/python" in agent_commands._HERMES_PRELUDE_DIRS
    assert {"/var/lib/hal0/hermes", "/var/lib/hal0/python"} <= set(
        agent_commands._HERMES_AGENT_TREES
    )


def test_prelude_dirs_leave_a_symlink_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pwd

    real = tmp_path / "real"
    real.mkdir()
    real.chmod(0o700)
    link = tmp_path / "link"
    link.symlink_to(real)
    monkeypatch.setattr(agent_commands, "_HERMES_PRELUDE_DIRS", (str(link),))
    me = pwd.getpwuid(__import__("os").getuid())
    monkeypatch.setattr(pwd, "getpwnam", lambda _n: me)
    agent_commands._ensure_hermes_prelude_dirs()
    assert oct(real.stat().st_mode & 0o7777) == "0o700"  # chmod never followed the link
