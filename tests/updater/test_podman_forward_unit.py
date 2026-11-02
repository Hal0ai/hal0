"""#3211: ``hal0 update`` must deliver hal0-podman-forward.service.

A unit shipped only by install.sh never reaches a box that upgrades through
``hal0 update`` (the #1689 shape), and a box that gained Docker after install
never had it enabled.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

import hal0.updater.updater as updater_mod

REPO = Path(__file__).resolve().parents[2]
SHIPPED_UNIT = REPO / "packaging" / "systemd" / "hal0-podman-forward.service"


class FakeRun:
    """Records argv; fails the verbs named in ``fail``."""

    def __init__(self, *, fail: tuple[str, ...] = ()) -> None:
        self.calls: list[list[str]] = []
        self.fail = fail

    def __call__(self, argv: list[str], **_: Any) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(list(argv))
        rc = 1 if argv[1] in self.fail else 0
        return subprocess.CompletedProcess(argv, rc, stdout=b"", stderr=b"")


@pytest.fixture
def unit_dst(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    dst_dir = tmp_path / "etc-systemd-system"
    dst_dir.mkdir()
    dst = dst_dir / "hal0-podman-forward.service"
    monkeypatch.setattr(updater_mod, "PODMAN_FORWARD_UNIT_DST", dst)
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    return dst


@pytest.fixture
def release(tmp_path: Path) -> Path:
    target = tmp_path / "hal0-1.8.0"
    unit_dir = target / "packaging" / "systemd"
    unit_dir.mkdir(parents=True)
    (unit_dir / SHIPPED_UNIT.name).write_text(SHIPPED_UNIT.read_text(encoding="utf-8"))
    return target


def _patch_run(monkeypatch: pytest.MonkeyPatch, run: FakeRun) -> None:
    monkeypatch.setattr("hal0.updater.updater.subprocess.run", run)


def test_unprivileged_is_a_noop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The unprivileged daemon must never write /etc/systemd/system."""
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    run = FakeRun()
    _patch_run(monkeypatch, run)

    assert updater_mod.refresh_podman_forward_unit(tmp_path) == "skipped"
    assert run.calls == []


def test_release_without_the_unit_is_skipped(
    monkeypatch: pytest.MonkeyPatch, unit_dst: Path, tmp_path: Path
) -> None:
    run = FakeRun()
    _patch_run(monkeypatch, run)

    assert updater_mod.refresh_podman_forward_unit(tmp_path) == "skipped"
    assert not unit_dst.exists()


def test_installs_enables_and_converges_now(
    monkeypatch: pytest.MonkeyPatch, unit_dst: Path, release: Path
) -> None:
    run = FakeRun()
    _patch_run(monkeypatch, run)

    assert updater_mod.refresh_podman_forward_unit(release) == "installed"

    assert unit_dst.read_text(encoding="utf-8") == SHIPPED_UNIT.read_text(encoding="utf-8")
    assert run.calls == [
        ["systemctl", "daemon-reload"],
        ["systemctl", "reenable", "hal0-podman-forward.service"],
        ["systemctl", "restart", "--no-block", "hal0-podman-forward.service"],
    ]


def test_matching_content_still_reenables(
    monkeypatch: pytest.MonkeyPatch, unit_dst: Path, release: Path
) -> None:
    """A box that gained Docker after install has the file but no enable link."""
    unit_dst.write_text(SHIPPED_UNIT.read_text(encoding="utf-8"), encoding="utf-8")
    run = FakeRun()
    _patch_run(monkeypatch, run)

    assert updater_mod.refresh_podman_forward_unit(release) == "unchanged"
    assert ["systemctl", "reenable", "hal0-podman-forward.service"] in run.calls


def test_failed_enable_is_reported_and_not_raised(
    monkeypatch: pytest.MonkeyPatch, unit_dst: Path, release: Path
) -> None:
    _patch_run(monkeypatch, FakeRun(fail=("reenable",)))

    assert updater_mod.refresh_podman_forward_unit(release) == "failed"


def test_failed_restart_keeps_the_install(
    monkeypatch: pytest.MonkeyPatch, unit_dst: Path, release: Path
) -> None:
    _patch_run(monkeypatch, FakeRun(fail=("restart",)))

    assert updater_mod.refresh_podman_forward_unit(release) == "installed"
    assert unit_dst.exists()


def test_write_failure_is_reported_and_not_raised(
    monkeypatch: pytest.MonkeyPatch, unit_dst: Path, release: Path
) -> None:
    def _boom(*_: Any, **__: Any) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr(updater_mod, "_write_unit_atomic", _boom)
    _patch_run(monkeypatch, FakeRun())

    assert updater_mod.refresh_podman_forward_unit(release) == "failed"
