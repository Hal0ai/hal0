"""#3211: ``hal0 update`` must deliver hal0-podman-forward.service.

A unit shipped only by install.sh never reaches a box that upgrades through
``hal0 update`` (the #1689 shape), and a box that gained Docker after install
never had it enabled. ``activate_release`` cannot fix that: it runs the OUTGOING
release, so the delivery goes through the post-activation child and a
``hal0-update`` verb, the #3239 route.
"""

from __future__ import annotations

import io
import os
import subprocess
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

import pytest

import hal0.updater.privileged as privileged
import hal0.updater.updater as updater_mod

REPO = Path(__file__).resolve().parents[2]
SHIPPED_UNIT = REPO / "packaging" / "systemd" / "hal0-podman-forward.service"
SHIPPED = SHIPPED_UNIT.read_text(encoding="utf-8")
UNIT = "hal0-podman-forward.service"


class FakeRun:
    """Records argv; fails the verbs named in ``fail``, times out on ``hang``."""

    def __init__(self, *, fail: tuple[str, ...] = (), hang: tuple[str, ...] = ()) -> None:
        self.calls: list[list[str]] = []
        self.timeouts: list[Any] = []
        self.fail = fail
        self.hang = hang

    def __call__(self, argv: list[str], **kw: Any) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(list(argv))
        self.timeouts.append(kw.get("timeout"))
        if argv[1] in self.hang:
            raise subprocess.TimeoutExpired(argv, kw.get("timeout") or 0)
        rc = 1 if argv[1] in self.fail else 0
        return subprocess.CompletedProcess(argv, rc, stdout=b"", stderr=b"")


@pytest.fixture
def unit_dst(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    dst_dir = tmp_path / "etc-systemd-system"
    dst_dir.mkdir()
    dst = dst_dir / UNIT
    monkeypatch.setattr(updater_mod, "PODMAN_FORWARD_UNIT_DST", dst)
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    return dst


@pytest.fixture
def release(tmp_path: Path) -> Path:
    target = tmp_path / "hal0-1.8.0"
    unit_dir = target / "packaging" / "systemd"
    unit_dir.mkdir(parents=True)
    (unit_dir / UNIT).write_text(SHIPPED, encoding="utf-8")
    return target


def _patch_run(monkeypatch: pytest.MonkeyPatch, run: FakeRun) -> None:
    monkeypatch.setattr("hal0.updater.updater.subprocess.run", run)


# ── refresh_podman_forward_unit ──────────────────────────────────────────────


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

    assert unit_dst.read_text(encoding="utf-8") == SHIPPED
    assert run.calls == [
        ["systemctl", "daemon-reload"],
        ["systemctl", "reenable", UNIT],
        ["systemctl", "restart", "--no-block", UNIT],
    ]
    assert run.timeouts == [30, 30, 30]


def test_matching_content_still_reenables(
    monkeypatch: pytest.MonkeyPatch, unit_dst: Path, release: Path
) -> None:
    """A box that gained Docker after install has the file but no enable link."""
    unit_dst.write_text(SHIPPED, encoding="utf-8")
    run = FakeRun()
    _patch_run(monkeypatch, run)

    assert updater_mod.refresh_podman_forward_unit(release) == "unchanged"
    assert ["systemctl", "reenable", UNIT] in run.calls


def test_masked_unit_is_left_alone(
    monkeypatch: pytest.MonkeyPatch, unit_dst: Path, release: Path
) -> None:
    """Masking is the opt-out: the rules go above operator rules in DOCKER-USER."""
    unit_dst.symlink_to(os.devnull)
    run = FakeRun()
    _patch_run(monkeypatch, run)

    assert updater_mod.refresh_podman_forward_unit(release) == "skipped"

    assert unit_dst.is_symlink()
    assert os.readlink(unit_dst) == os.devnull
    assert run.calls == []


def test_a_symlink_elsewhere_is_not_a_mask(
    monkeypatch: pytest.MonkeyPatch, unit_dst: Path, release: Path, tmp_path: Path
) -> None:
    elsewhere = tmp_path / "elsewhere.service"
    elsewhere.write_text("[Service]\n", encoding="utf-8")
    unit_dst.symlink_to(elsewhere)
    _patch_run(monkeypatch, FakeRun())

    assert updater_mod.refresh_podman_forward_unit(release) == "installed"
    assert not unit_dst.is_symlink()


@pytest.mark.parametrize("kwargs", [{"fail": ("daemon-reload",)}, {"hang": ("daemon-reload",)}])
def test_failed_reload_restores_and_reports_failed(
    monkeypatch: pytest.MonkeyPatch, unit_dst: Path, release: Path, kwargs: dict[str, Any]
) -> None:
    """A new file systemd never loaded would read as "unchanged" on the next run."""
    unit_dst.write_text("[Service]\nRemainAfterExit=yes\n", encoding="utf-8")
    run = FakeRun(**kwargs)
    _patch_run(monkeypatch, run)

    assert updater_mod.refresh_podman_forward_unit(release) == "failed"

    assert unit_dst.read_text(encoding="utf-8") == "[Service]\nRemainAfterExit=yes\n"
    assert run.calls == [["systemctl", "daemon-reload"]]


def test_failed_reload_removes_a_file_that_was_not_there(
    monkeypatch: pytest.MonkeyPatch, unit_dst: Path, release: Path
) -> None:
    _patch_run(monkeypatch, FakeRun(fail=("daemon-reload",)))

    assert updater_mod.refresh_podman_forward_unit(release) == "failed"
    assert not unit_dst.exists()


@pytest.mark.parametrize("kwargs", [{"fail": ("reenable",)}, {"hang": ("reenable",)}])
def test_failed_enable_is_reported_and_not_raised(
    monkeypatch: pytest.MonkeyPatch, unit_dst: Path, release: Path, kwargs: dict[str, Any]
) -> None:
    _patch_run(monkeypatch, FakeRun(**kwargs))

    assert updater_mod.refresh_podman_forward_unit(release) == "failed"


@pytest.mark.parametrize("kwargs", [{"fail": ("restart",)}, {"hang": ("restart",)}])
def test_failed_restart_keeps_the_install(
    monkeypatch: pytest.MonkeyPatch, unit_dst: Path, release: Path, kwargs: dict[str, Any]
) -> None:
    _patch_run(monkeypatch, FakeRun(**kwargs))

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


def test_undecodable_installed_unit_is_reported_and_not_raised(
    monkeypatch: pytest.MonkeyPatch, unit_dst: Path, release: Path
) -> None:
    unit_dst.write_bytes(b"\xff\xfe not utf-8")
    _patch_run(monkeypatch, FakeRun())

    assert updater_mod.refresh_podman_forward_unit(release) == "failed"


# ── delivery: the first update, through the outgoing release's wrapper ───────


def test_activate_release_does_not_refresh_the_unit(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """activate_release runs the OUTGOING release: a call there misses this upgrade."""
    monkeypatch.setattr(updater_mod, "_is_editable_install", lambda: False)
    monkeypatch.setattr(updater_mod, "_reinstall_into_venv", lambda *_a, **_k: None)

    def forbidden(*_a: Any, **_k: Any) -> None:
        raise AssertionError("activate_release must not refresh this unit")

    monkeypatch.setattr(updater_mod, "refresh_podman_forward_unit", forbidden)
    root = updater_mod._usr_lib_root()
    (root / "hal0-1.0.0").mkdir(parents=True)

    result = updater_mod.activate_release("hal0-1.0.0")

    assert result["target"].endswith("hal0-1.0.0")
    assert "podman_forward_unit" not in result


def _route_seam_to_root_side(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Make UpdateSeam behave as hal0: its sudo call runs the root entry point."""
    argvs: list[list[str]] = []

    def fake_sudo(argv: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        argvs.append(list(argv))
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = privileged.main(argv[3:])
        return subprocess.CompletedProcess(argv, rc, buf.getvalue(), "")

    monkeypatch.setattr(privileged.UpdateSeam, "routed", property(lambda _s: True))
    real_init = privileged.UpdateSeam.__init__
    monkeypatch.setattr(
        privileged.UpdateSeam,
        "__init__",
        lambda self, **kw: real_init(self, **{**kw, "run": fake_sudo}),
    )
    return argvs


def test_first_update_reaches_the_root_side_through_the_migration_child(
    monkeypatch: pytest.MonkeyPatch, unit_dst: Path, release: Path, tmp_path: Path
) -> None:
    """The route that survives the trap: post-activation, seam, wrapper verb, root."""
    unit_dst.write_text("[Service]\nRemainAfterExit=yes\n", encoding="utf-8")
    run = FakeRun()
    _patch_run(monkeypatch, run)
    link = tmp_path / "current"
    link.symlink_to(release)
    monkeypatch.setattr(privileged, "_current_symlink", lambda: link)
    trusted: list[Path] = []
    monkeypatch.setattr(privileged, "assert_trusted_release_dir", trusted.append)
    monkeypatch.setattr(updater_mod, "converge_api_unit", lambda *, job_id=None: "skipped")
    argvs = _route_seam_to_root_side(monkeypatch)

    def conversion(**_kw: Any) -> None:
        raise RuntimeError("the fatal conversion stops the sequence here")

    monkeypatch.setattr("hal0.config.migrations.runner_system.migrate_runner_system", conversion)
    with pytest.raises(RuntimeError):
        updater_mod.run_post_activation_migrations(1, job_id="job-1")

    assert argvs == [["sudo", "-n", privileged.SEAM_BIN, "refresh-podman-forward"]]
    assert trusted == [release.resolve()]
    assert unit_dst.read_text(encoding="utf-8") == SHIPPED
    assert ["systemctl", "reenable", UNIT] in run.calls


def test_boot_net_does_not_touch_the_unit(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str | None] = []
    monkeypatch.setattr(updater_mod, "converge_api_unit", lambda *, job_id=None: "skipped")
    monkeypatch.setattr(
        updater_mod, "converge_podman_forward_unit", lambda *, job_id=None: seen.append(job_id)
    )

    def conversion(**_kw: Any) -> None:
        raise RuntimeError("stop")

    monkeypatch.setattr("hal0.config.migrations.runner_system.migrate_runner_system", conversion)
    with pytest.raises(RuntimeError):
        updater_mod.run_post_activation_migrations(1, converge_companions=False)

    assert seen == []


def test_verb_refuses_arguments_and_an_untrusted_tree(
    monkeypatch: pytest.MonkeyPatch, unit_dst: Path, release: Path, tmp_path: Path
) -> None:
    unit_dst.write_text("[Service]\n", encoding="utf-8")
    _patch_run(monkeypatch, FakeRun())
    link = tmp_path / "current"
    link.symlink_to(release)
    monkeypatch.setattr(privileged, "_current_symlink", lambda: link)
    assert privileged.main(["refresh-podman-forward", "/etc/passwd"]) == 1

    def untrusted(_path: Path) -> None:
        raise updater_mod.UpdatePrivilegeError("not root-owned", details={})

    monkeypatch.setattr(privileged, "assert_trusted_release_dir", untrusted)
    assert privileged.main(["refresh-podman-forward"]) == 1
    assert unit_dst.read_text(encoding="utf-8") == "[Service]\n"


def test_converge_contains_a_wrapper_without_the_verb(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed wrapper refresh leaves the old one, which exits 64 on the verb."""

    def stale_wrapper(argv: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 64, "", "hal0-update: bad cmd\n")

    monkeypatch.setattr(privileged.UpdateSeam, "routed", property(lambda _s: True))
    real_init = privileged.UpdateSeam.__init__
    monkeypatch.setattr(
        privileged.UpdateSeam,
        "__init__",
        lambda self, **kw: real_init(self, **{**kw, "run": stale_wrapper}),
    )

    assert updater_mod.converge_podman_forward_unit() == "failed"
