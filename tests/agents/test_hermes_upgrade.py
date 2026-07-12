"""Tests for hal0.agents.hermes_provision.upgrade_hermes_runtime.

The runtime half of ``hal0 agent upgrade hermes``: pip-upgrade the unpinned
``hermes-agent`` (floor/cap or an exact ``--to``) + ``hermes config migrate`` so
the schema matches the new build. Verifies the argv, the version-pin branch, the
non-fatal migrate, and the real-stop pip failure — all via an injected runner so
no network or real venv is touched.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from hal0.agents.hermes_provision import upgrade_hermes_runtime


class FakeRunner:
    def __init__(self, fail_on: Any = None) -> None:
        self.calls: list[list[str]] = []
        self.fail_on = fail_on  # Callable[[list[str]], bool] | None

    def run(self, argv: list[str], **_kwargs: Any) -> Any:
        self.calls.append(list(argv))
        if self.fail_on is not None and self.fail_on(list(argv)):
            raise subprocess.CalledProcessError(1, argv)
        return None


def _is_pip(argv: list[str]) -> bool:
    return "install" in argv and "--upgrade" in argv


def _is_migrate(argv: list[str]) -> bool:
    return argv[-2:] == ["config", "migrate"]


def _fake_venv(tmp_path: Path) -> Path:
    """A venv whose bin/python exists so the missing-venv guard passes."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    py = bindir / "python"
    py.write_text("#!/bin/sh\n")
    py.chmod(0o755)
    return tmp_path


def _pip_call(calls: list[list[str]]) -> list[str] | None:
    return next((c for c in calls if "pip" in c and "install" in c and "--upgrade" in c), None)


def _migrate_call(calls: list[list[str]]) -> list[str] | None:
    return next((c for c in calls if c[-2:] == ["config", "migrate"]), None)


def test_missing_venv_is_actionable_stop(tmp_path: Path) -> None:
    ok, msg = upgrade_hermes_runtime(venv=tmp_path / "nope", runner=FakeRunner())
    assert ok is False
    assert "venv missing" in msg


def test_happy_path_upgrades_then_migrates(tmp_path: Path) -> None:
    venv = _fake_venv(tmp_path)
    r = FakeRunner()
    ok, msg = upgrade_hermes_runtime(
        venv=venv, requirements=Path("/req.txt"), hermes_home=tmp_path, runner=r
    )
    assert ok is True
    pip = _pip_call(r.calls)
    assert pip is not None
    # no --to → installs from the requirements file (floor/cap)
    assert pip[-2:] == ["-r", "/req.txt"]
    assert _migrate_call(r.calls) is not None
    assert "config migrated" in msg


def test_version_pin_installs_exact_spec(tmp_path: Path) -> None:
    venv = _fake_venv(tmp_path)
    r = FakeRunner()
    ok, _ = upgrade_hermes_runtime(venv=venv, version="0.15.2", runner=r)
    assert ok is True
    pip = _pip_call(r.calls)
    assert pip is not None
    assert pip[-1] == "hermes-agent[web]==0.15.2"
    assert "-r" not in pip


def test_migrate_failure_is_non_fatal(tmp_path: Path) -> None:
    venv = _fake_venv(tmp_path)
    r = FakeRunner(fail_on=_is_migrate)  # pip ok, migrate raises
    ok, msg = upgrade_hermes_runtime(venv=venv, runner=r)
    assert ok is True  # upgrade still succeeds
    assert "migrate skipped" in msg


def test_pip_failure_is_a_real_stop(tmp_path: Path) -> None:
    venv = _fake_venv(tmp_path)
    r = FakeRunner(fail_on=_is_pip)  # pip upgrade fails
    ok, msg = upgrade_hermes_runtime(venv=venv, runner=r)
    assert ok is False
    assert "pip upgrade failed" in msg
    assert _migrate_call(r.calls) is None  # never reached migrate


def test_requirements_floor_not_hard_pinned() -> None:
    """The shipped requirements no longer hard-pin (the update-blocker)."""
    from hal0.agents.hermes_provision import HERMES_REQUIREMENTS

    text = HERMES_REQUIREMENTS.read_text(encoding="utf-8")
    reqs = [
        ln.strip() for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")
    ]
    # The single requirement line is the floored+capped spec, not a hard pin.
    assert reqs == ["hermes-agent[web]>=0.14.0,<1.0"]
