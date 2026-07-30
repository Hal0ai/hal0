"""#12 regression: ``_reinstall_into_venv`` must install the release's full
dependency set — no ``--no-deps``.

A ``pip install --no-deps --force-reinstall`` re-pip means a release that
adds or bumps a ``[project.dependencies]`` entry is silently never
installed: pip reports success (``--no-deps`` skips resolution entirely)
and the gap only surfaces as a deferred ``ImportError`` the first time the
new code path runs. Applies to every caller of ``_reinstall_into_venv``:
``Updater.commit``, ``Updater.commit_git``, and ``Updater.rollback``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hal0.updater.updater import UpdateError, _reinstall_into_venv


class _FakeCompletedProcess:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_reinstall_into_venv_does_not_pass_no_deps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pip invocation must never carry ``--no-deps``."""
    captured: dict[str, list[str]] = {}

    def _fake_run(cmd: list[str], **kwargs: Any) -> _FakeCompletedProcess:
        captured["cmd"] = cmd
        return _FakeCompletedProcess(0)

    monkeypatch.setattr("hal0.updater.updater.subprocess.run", _fake_run)

    _reinstall_into_venv(tmp_path / "hal0-9.9.9")

    assert "cmd" in captured
    assert "--no-deps" not in captured["cmd"]


def test_reinstall_into_venv_still_force_reinstalls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Removing --no-deps must not also drop --force-reinstall."""
    captured: dict[str, list[str]] = {}

    def _fake_run(cmd: list[str], **kwargs: Any) -> _FakeCompletedProcess:
        captured["cmd"] = cmd
        return _FakeCompletedProcess(0)

    monkeypatch.setattr("hal0.updater.updater.subprocess.run", _fake_run)

    install_dir = tmp_path / "hal0-9.9.9"
    _reinstall_into_venv(install_dir)

    assert "--force-reinstall" in captured["cmd"]
    assert str(install_dir) in captured["cmd"]
    assert captured["cmd"][:3] == [captured["cmd"][0], "-m", "pip"]


def test_reinstall_into_venv_still_raises_on_pip_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dropping --no-deps must not change the failure contract."""
    monkeypatch.setattr(
        "hal0.updater.updater.subprocess.run",
        lambda cmd, **kwargs: _FakeCompletedProcess(1, stderr="boom"),
    )

    with pytest.raises(UpdateError):
        _reinstall_into_venv(tmp_path / "hal0-9.9.9")
