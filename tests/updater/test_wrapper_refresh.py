"""#1689 — self-update never refreshed the privileged sudo wrappers.

``install.sh`` was the ONLY installer of ``${LIB_DIR}/bin/hal0-*``. Self-update
swapped the ``current`` symlink and re-pipped the venv but never touched the
wrappers, so a box upgraded exclusively through ``hal0 update`` kept running
every new hal0 release against whatever wrapper its last ``install.sh`` run
left behind — any seam verb added since (``stop-agent``/#453, the
``svc-<verb>`` family/#1590, ``write-hindsight-dropin``/#1641) was rejected
with ``hal0-systemctl: bad cmd: <verb>`` on such a box.

These tests pin :func:`hal0.updater.updater.refresh_privileged_wrappers`:
privileged-side only (a no-op unless euid 0), best-effort per seam, and the
sudoers drop-in reinstalled only on genuine content change and only after an
independent ``visudo -cf`` pass.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from hal0.updater.updater import refresh_privileged_wrappers


class FakeRun:
    """Recording stand-in for ``subprocess.run`` — never touches the real OS."""

    def __init__(self, *, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.calls: list[list[str]] = []
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(argv))
        if kwargs.get("check") and self.returncode != 0:
            raise subprocess.CalledProcessError(self.returncode, argv, stderr=self.stderr)
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, self.stderr)


def _release_tree(tmp_path: Path, *, seams: tuple[str, ...], with_sudoers: bool = False) -> Path:
    target = tmp_path / "hal0-1.0.0"
    wrappers = target / "installer" / "wrappers"
    wrappers.mkdir(parents=True)
    for name in seams:
        (wrappers / name).write_text(f"#!/bin/sh\n# {name} v2\n", encoding="utf-8")
    if with_sudoers:
        sudoers = target / "packaging" / "sudoers"
        sudoers.mkdir(parents=True)
        for name in seams:
            (sudoers / name).write_text(
                f"hal0 ALL=(root) NOPASSWD: /usr/lib/hal0/bin/{name}\n", encoding="utf-8"
            )
    return target


@pytest.fixture
def seam_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Redirect the wrapper/sudoers destinations to a throwaway tree.

    ``refresh_privileged_wrappers`` locally imports ``SEAM_BIN_DIR`` /
    ``SUDOERS_DIR`` on every call, so patching the module attributes here is
    picked up fresh each time — no need to patch a cached binding.
    """
    bin_dir = tmp_path / "usr-lib-hal0-bin"
    sudoers_dir = tmp_path / "etc-sudoers.d"
    monkeypatch.setattr("hal0.system.seam_check.SEAM_BIN_DIR", bin_dir)
    monkeypatch.setattr("hal0.system.seam_check.SUDOERS_DIR", sudoers_dir)
    return bin_dir, sudoers_dir


def test_noop_when_not_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seam_dirs: tuple[Path, Path]
) -> None:
    """The unprivileged daemon must never write the wrapper tree itself."""
    monkeypatch.setattr("hal0.updater.updater.os.geteuid", lambda: 1000)
    fake_run = FakeRun()
    monkeypatch.setattr("hal0.updater.updater.subprocess.run", fake_run)
    target = _release_tree(tmp_path, seams=("hal0-systemctl",))

    result = refresh_privileged_wrappers(target)

    assert result == {"refreshed": [], "sudoers_refreshed": [], "errors": {}}
    assert fake_run.calls == []


def test_root_refreshes_every_wrapper_present_in_the_release_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seam_dirs: tuple[Path, Path]
) -> None:
    monkeypatch.setattr("hal0.updater.updater.os.geteuid", lambda: 0)
    fake_run = FakeRun()
    monkeypatch.setattr("hal0.updater.updater.subprocess.run", fake_run)
    target = _release_tree(
        tmp_path,
        seams=("hal0-systemctl", "hal0-update", "hal0-agentenv", "hal0-benchctl", "hal0-podman-ro"),
    )

    result = refresh_privileged_wrappers(target, job_id="job-1")

    assert set(result["refreshed"]) == {
        "hal0-systemctl",
        "hal0-update",
        "hal0-agentenv",
        "hal0-benchctl",
        "hal0-podman-ro",
    }
    assert result["errors"] == {}
    install_calls = [c for c in fake_run.calls if c[0] == "install"]
    assert len(install_calls) == 5
    for call in install_calls:
        assert call[:3] == ["install", "-m", "0755"]
        assert "-o" in call and "root" in call
        assert "-g" in call and "root" in call


def test_skips_a_seam_missing_from_the_release_tree_without_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seam_dirs: tuple[Path, Path]
) -> None:
    """A release tree that only ships some wrapper sources (or none at all,
    e.g. a stripped-down channel) must not fail the activate over it."""
    monkeypatch.setattr("hal0.updater.updater.os.geteuid", lambda: 0)
    fake_run = FakeRun()
    monkeypatch.setattr("hal0.updater.updater.subprocess.run", fake_run)
    target = _release_tree(tmp_path, seams=("hal0-systemctl",))

    result = refresh_privileged_wrappers(target)

    assert result["refreshed"] == ["hal0-systemctl"]
    assert result["errors"] == {}


def test_install_failure_is_recorded_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seam_dirs: tuple[Path, Path]
) -> None:
    """A wrapper refresh failure must be fail-soft — the OLD wrapper stays in
    place, same degradation class as before this fix, never a new one; it
    must not tear down an otherwise-successful activate."""
    monkeypatch.setattr("hal0.updater.updater.os.geteuid", lambda: 0)
    fake_run = FakeRun(returncode=1, stderr="install: permission denied")
    monkeypatch.setattr("hal0.updater.updater.subprocess.run", fake_run)
    target = _release_tree(tmp_path, seams=("hal0-systemctl",))

    result = refresh_privileged_wrappers(target)

    assert result["refreshed"] == []
    assert "hal0-systemctl" in result["errors"]
    assert "permission denied" in result["errors"]["hal0-systemctl"]


def test_sudoers_dropin_reinstalled_only_on_content_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seam_dirs: tuple[Path, Path]
) -> None:
    _bin_dir, sudoers_dir = seam_dirs
    sudoers_dir.mkdir(parents=True)
    monkeypatch.setattr("hal0.updater.updater.os.geteuid", lambda: 0)
    fake_run = FakeRun()  # visudo -cf and install both "succeed"
    monkeypatch.setattr("hal0.updater.updater.subprocess.run", fake_run)
    target = _release_tree(tmp_path, seams=("hal0-systemctl",), with_sudoers=True)

    # Already-identical on-disk drop-in — visudo/install must not run for it.
    (sudoers_dir / "hal0-systemctl").write_text(
        (target / "packaging" / "sudoers" / "hal0-systemctl").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    result = refresh_privileged_wrappers(target)

    assert result["sudoers_refreshed"] == []
    assert not any(c[0] == "visudo" for c in fake_run.calls)


def test_sudoers_dropin_reinstalled_when_content_differs_and_validates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seam_dirs: tuple[Path, Path]
) -> None:
    _bin_dir, sudoers_dir = seam_dirs
    sudoers_dir.mkdir(parents=True)
    monkeypatch.setattr("hal0.updater.updater.os.geteuid", lambda: 0)
    fake_run = FakeRun()
    monkeypatch.setattr("hal0.updater.updater.subprocess.run", fake_run)
    target = _release_tree(tmp_path, seams=("hal0-systemctl",), with_sudoers=True)
    (sudoers_dir / "hal0-systemctl").write_text("stale old grant\n", encoding="utf-8")

    result = refresh_privileged_wrappers(target)

    assert result["sudoers_refreshed"] == ["hal0-systemctl"]
    assert any(c[0] == "visudo" and c[1] == "-cf" for c in fake_run.calls)
    install_sudoers_calls = [c for c in fake_run.calls if c[0] == "install" and c[2] == "0440"]
    assert len(install_sudoers_calls) == 1


def test_sudoers_dropin_never_installed_when_visudo_rejects_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seam_dirs: tuple[Path, Path]
) -> None:
    """A malformed drop-in must never reach /etc/sudoers.d, corrupted
    release tree or not — visudo -cf is checked BEFORE install runs."""
    _bin_dir, sudoers_dir = seam_dirs
    sudoers_dir.mkdir(parents=True)
    monkeypatch.setattr("hal0.updater.updater.os.geteuid", lambda: 0)

    def selective_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if argv[0] == "visudo":
            return subprocess.CompletedProcess(argv, 1, "", "syntax error near line 1")
        return subprocess.CompletedProcess(argv, 0, "", "")

    calls: list[list[str]] = []

    def recording_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(list(argv))
        return selective_run(argv, **kwargs)

    monkeypatch.setattr("hal0.updater.updater.subprocess.run", recording_run)
    target = _release_tree(tmp_path, seams=("hal0-systemctl",), with_sudoers=True)
    (sudoers_dir / "hal0-systemctl").write_text("stale old grant\n", encoding="utf-8")

    result = refresh_privileged_wrappers(target)

    assert result["sudoers_refreshed"] == []
    assert any(c[0] == "visudo" for c in calls)
    assert not any(c[0] == "install" and "0440" in c for c in calls)


def test_activate_release_includes_wrappers_refreshed_key(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch, seam_dirs: tuple[Path, Path]
) -> None:
    """Integration: activate_release surfaces the refresh result. Under test
    (non-root) it must be an empty list, not a missing key or a crash."""
    from hal0.updater.updater import _usr_lib_root, activate_release

    monkeypatch.setattr("hal0.updater.updater._is_editable_install", lambda: True)
    root = _usr_lib_root()
    root.mkdir(parents=True, exist_ok=True)
    new = root / "hal0-1.0.0"
    new.mkdir()

    result = activate_release("hal0-1.0.0")

    assert result["wrappers_refreshed"] == []
