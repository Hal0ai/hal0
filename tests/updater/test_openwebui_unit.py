"""#3526: ``hal0 update`` must refresh hal0-openwebui.service from the release.

``install.sh`` was the only writer of the unit, so a fix to the packaged file
(#3524 moved ``StartLimit*=`` into ``[Unit]``) never reached a box that updates
in place. The refresh takes the #3211 route: post-activation child, seam verb,
root side. It keeps the installed image pin, because moving the pin belongs to
the openwebui component arm, which pulls before it repins.
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
from hal0.openwebui import image_pin

REPO = Path(__file__).resolve().parents[2]
SHIPPED = (REPO / "packaging" / "systemd" / "hal0-openwebui.service").read_text(encoding="utf-8")
UNIT = "hal0-openwebui.service"
OVERRIDE = "sha256:" + "a" * 64

#: The pre-#3524 shape: the restart cap under [Service], where systemd drops it.
OLD_SHAPE = SHIPPED.replace("StartLimitIntervalSec=120\nStartLimitBurst=10\n", "").replace(
    "[Service]\nType=simple\n",
    "[Service]\nType=simple\nStartLimitIntervalSec=120\nStartLimitBurst=10\n",
)


class FakeRun:
    """Records argv; fails the verbs named in ``fail``, times out on ``hang``."""

    def __init__(self, *, fail: tuple[str, ...] = (), hang: tuple[str, ...] = ()) -> None:
        self.calls: list[list[str]] = []
        self.fail = fail
        self.hang = hang

    def __call__(self, argv: list[str], **kw: Any) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(list(argv))
        if argv[1] in self.hang:
            raise subprocess.TimeoutExpired(argv, kw.get("timeout") or 0)
        rc = 1 if argv[1] in self.fail else 0
        return subprocess.CompletedProcess(argv, rc, stdout=b"", stderr=b"")


@pytest.fixture
def unit_dst(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    dst_dir = tmp_path / "etc-systemd-system"
    dst_dir.mkdir()
    dst = dst_dir / UNIT
    monkeypatch.setattr(updater_mod, "OPENWEBUI_UNIT_DST", dst)
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


def test_old_shape_fixture_is_the_pre_3524_unit() -> None:
    assert OLD_SHAPE != SHIPPED
    assert OLD_SHAPE.index("StartLimitBurst") > OLD_SHAPE.index("[Service]")


# ── refresh_openwebui_unit ───────────────────────────────────────────────────


def test_unprivileged_is_a_noop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    run = FakeRun()
    _patch_run(monkeypatch, run)

    assert updater_mod.refresh_openwebui_unit(tmp_path) == "skipped"
    assert run.calls == []


def test_release_without_the_unit_is_skipped(
    monkeypatch: pytest.MonkeyPatch, unit_dst: Path, tmp_path: Path
) -> None:
    unit_dst.write_text(OLD_SHAPE, encoding="utf-8")
    _patch_run(monkeypatch, FakeRun())

    assert updater_mod.refresh_openwebui_unit(tmp_path) == "skipped"
    assert unit_dst.read_text(encoding="utf-8") == OLD_SHAPE


def test_box_without_open_webui_is_not_given_one(
    monkeypatch: pytest.MonkeyPatch, unit_dst: Path, release: Path
) -> None:
    """Installing Open WebUI is the installer's call, not the updater's."""
    run = FakeRun()
    _patch_run(monkeypatch, run)

    assert updater_mod.refresh_openwebui_unit(release) == "skipped"
    assert not unit_dst.exists()
    assert run.calls == []


def test_refreshes_a_stale_unit_and_try_restarts(
    monkeypatch: pytest.MonkeyPatch, unit_dst: Path, release: Path
) -> None:
    unit_dst.write_text(OLD_SHAPE, encoding="utf-8")
    run = FakeRun()
    _patch_run(monkeypatch, run)

    assert updater_mod.refresh_openwebui_unit(release) == "installed"

    assert unit_dst.read_text(encoding="utf-8") == SHIPPED
    assert run.calls == [
        ["systemctl", "daemon-reload"],
        ["systemctl", "try-restart", "--no-block", UNIT],
    ]


def test_matching_unit_is_left_alone_and_not_restarted(
    monkeypatch: pytest.MonkeyPatch, unit_dst: Path, release: Path
) -> None:
    unit_dst.write_text(SHIPPED, encoding="utf-8")
    run = FakeRun()
    _patch_run(monkeypatch, run)

    assert updater_mod.refresh_openwebui_unit(release) == "unchanged"
    assert run.calls == []


def test_refresh_keeps_an_operator_override_pin(
    monkeypatch: pytest.MonkeyPatch, unit_dst: Path, release: Path
) -> None:
    """``hal0 update component openwebui --target`` must survive the refresh."""
    unit_dst.write_text(image_pin.repin_unit_text(OLD_SHAPE, OVERRIDE)[0], encoding="utf-8")
    _patch_run(monkeypatch, FakeRun())

    assert updater_mod.refresh_openwebui_unit(release) == "installed"

    new = unit_dst.read_text(encoding="utf-8")
    assert image_pin.parse_pinned_digest(new) == OVERRIDE
    assert new == image_pin.repin_unit_text(SHIPPED, OVERRIDE)[0]


def test_refresh_does_not_move_the_pin_ahead_of_the_pull(
    monkeypatch: pytest.MonkeyPatch, unit_dst: Path, release: Path
) -> None:
    """An older release pin stays: the component arm pulls first, then repins."""
    old_pin = "sha256:" + "b" * 64
    unit_dst.write_text(image_pin.repin_unit_text(OLD_SHAPE, old_pin)[0], encoding="utf-8")
    _patch_run(monkeypatch, FakeRun())

    assert updater_mod.refresh_openwebui_unit(release) == "installed"
    assert image_pin.parse_pinned_digest(unit_dst.read_text(encoding="utf-8")) == old_pin


def test_unit_with_matching_body_but_other_pin_is_unchanged(
    monkeypatch: pytest.MonkeyPatch, unit_dst: Path, release: Path
) -> None:
    unit_dst.write_text(image_pin.repin_unit_text(SHIPPED, OVERRIDE)[0], encoding="utf-8")
    run = FakeRun()
    _patch_run(monkeypatch, run)

    assert updater_mod.refresh_openwebui_unit(release) == "unchanged"
    assert run.calls == []


def test_masked_unit_is_left_alone(
    monkeypatch: pytest.MonkeyPatch, unit_dst: Path, release: Path
) -> None:
    unit_dst.symlink_to(os.devnull)
    run = FakeRun()
    _patch_run(monkeypatch, run)

    assert updater_mod.refresh_openwebui_unit(release) == "skipped"
    assert os.readlink(unit_dst) == os.devnull
    assert run.calls == []


def test_an_operator_linked_unit_is_left_alone(
    monkeypatch: pytest.MonkeyPatch, unit_dst: Path, release: Path, tmp_path: Path
) -> None:
    mine = tmp_path / "my-openwebui.service"
    mine.write_text(OLD_SHAPE, encoding="utf-8")
    unit_dst.symlink_to(mine)
    _patch_run(monkeypatch, FakeRun())

    assert updater_mod.refresh_openwebui_unit(release) == "skipped"
    assert unit_dst.is_symlink()
    assert mine.read_text(encoding="utf-8") == OLD_SHAPE


def test_drop_ins_are_not_touched(
    monkeypatch: pytest.MonkeyPatch, unit_dst: Path, release: Path
) -> None:
    unit_dst.write_text(OLD_SHAPE, encoding="utf-8")
    dropin_dir = unit_dst.with_name(UNIT + ".d")
    dropin_dir.mkdir()
    (dropin_dir / "local.conf").write_text("[Service]\nRestartSec=30\n", encoding="utf-8")
    _patch_run(monkeypatch, FakeRun())

    assert updater_mod.refresh_openwebui_unit(release) == "installed"
    assert [p.name for p in dropin_dir.iterdir()] == ["local.conf"]
    assert (dropin_dir / "local.conf").read_text(encoding="utf-8") == "[Service]\nRestartSec=30\n"


@pytest.mark.parametrize("kwargs", [{"fail": ("daemon-reload",)}, {"hang": ("daemon-reload",)}])
def test_failed_reload_restores_and_reports_failed(
    monkeypatch: pytest.MonkeyPatch, unit_dst: Path, release: Path, kwargs: dict[str, Any]
) -> None:
    """A new file systemd never loaded would read as "unchanged" on the next run."""
    unit_dst.write_text(OLD_SHAPE, encoding="utf-8")
    run = FakeRun(**kwargs)
    _patch_run(monkeypatch, run)

    assert updater_mod.refresh_openwebui_unit(release) == "failed"
    assert unit_dst.read_text(encoding="utf-8") == OLD_SHAPE
    assert run.calls == [["systemctl", "daemon-reload"]]


@pytest.mark.parametrize("kwargs", [{"fail": ("try-restart",)}, {"hang": ("try-restart",)}])
def test_failed_restart_keeps_the_refresh(
    monkeypatch: pytest.MonkeyPatch, unit_dst: Path, release: Path, kwargs: dict[str, Any]
) -> None:
    unit_dst.write_text(OLD_SHAPE, encoding="utf-8")
    _patch_run(monkeypatch, FakeRun(**kwargs))

    assert updater_mod.refresh_openwebui_unit(release) == "installed"
    assert unit_dst.read_text(encoding="utf-8") == SHIPPED


def test_write_failure_is_reported_and_not_raised(
    monkeypatch: pytest.MonkeyPatch, unit_dst: Path, release: Path
) -> None:
    unit_dst.write_text(OLD_SHAPE, encoding="utf-8")

    def _boom(*_: Any, **__: Any) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr(updater_mod, "_write_unit_atomic", _boom)
    _patch_run(monkeypatch, FakeRun())

    assert updater_mod.refresh_openwebui_unit(release) == "failed"


def test_undecodable_installed_unit_is_reported_and_not_raised(
    monkeypatch: pytest.MonkeyPatch, unit_dst: Path, release: Path
) -> None:
    unit_dst.write_bytes(b"\xff\xfe not utf-8")
    _patch_run(monkeypatch, FakeRun())

    assert updater_mod.refresh_openwebui_unit(release) == "failed"


# ── delivery: the first update, through the outgoing release's wrapper ───────


def test_activate_release_does_not_refresh_the_unit(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """activate_release runs the OUTGOING release: a call there misses this upgrade."""
    monkeypatch.setattr(updater_mod, "_is_editable_install", lambda: False)
    monkeypatch.setattr(updater_mod, "_reinstall_into_venv", lambda *_a, **_k: None)

    def forbidden(*_a: Any, **_k: Any) -> None:
        raise AssertionError("activate_release must not refresh this unit")

    monkeypatch.setattr(updater_mod, "refresh_openwebui_unit", forbidden)
    root = updater_mod._usr_lib_root()
    (root / "hal0-1.0.0").mkdir(parents=True)

    result = updater_mod.activate_release("hal0-1.0.0")

    assert "openwebui_unit" not in result


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


def _stop_at_the_conversion(monkeypatch: pytest.MonkeyPatch) -> None:
    def conversion(**_kw: Any) -> None:
        raise RuntimeError("the fatal conversion stops the sequence here")

    monkeypatch.setattr("hal0.config.migrations.runner_system.migrate_runner_system", conversion)


def test_first_update_reaches_the_root_side_through_the_migration_child(
    monkeypatch: pytest.MonkeyPatch, unit_dst: Path, release: Path, tmp_path: Path
) -> None:
    unit_dst.write_text(OLD_SHAPE, encoding="utf-8")
    run = FakeRun()
    _patch_run(monkeypatch, run)
    link = tmp_path / "current"
    link.symlink_to(release)
    monkeypatch.setattr(privileged, "_current_symlink", lambda: link)
    trusted: list[Path] = []
    monkeypatch.setattr(privileged, "assert_trusted_release_dir", trusted.append)
    monkeypatch.setattr(updater_mod, "converge_api_unit", lambda *, job_id=None: "skipped")
    monkeypatch.setattr(
        updater_mod, "converge_podman_forward_unit", lambda *, job_id=None: "skipped"
    )
    argvs = _route_seam_to_root_side(monkeypatch)
    _stop_at_the_conversion(monkeypatch)

    with pytest.raises(RuntimeError):
        updater_mod.run_post_activation_migrations(1, job_id="job-1")

    assert argvs == [["sudo", "-n", privileged.SEAM_BIN, "refresh-openwebui-unit"]]
    assert trusted == [release.resolve()]
    assert unit_dst.read_text(encoding="utf-8") == SHIPPED


def test_install_sh_path_still_refreshes(monkeypatch: pytest.MonkeyPatch) -> None:
    """install.sh passes converge_podman_forward=False; that gate is podman's only."""
    seen: list[str | None] = []
    monkeypatch.setattr(updater_mod, "converge_api_unit", lambda *, job_id=None: "skipped")
    monkeypatch.setattr(
        updater_mod, "converge_openwebui_unit", lambda *, job_id=None: seen.append(job_id)
    )
    _stop_at_the_conversion(monkeypatch)

    with pytest.raises(RuntimeError):
        updater_mod.run_post_activation_migrations(1, job_id="job-2", converge_podman_forward=False)

    assert seen == ["job-2"]


def test_boot_net_does_not_touch_the_unit(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str | None] = []
    monkeypatch.setattr(
        updater_mod, "converge_openwebui_unit", lambda *, job_id=None: seen.append(job_id)
    )
    _stop_at_the_conversion(monkeypatch)

    with pytest.raises(RuntimeError):
        updater_mod.run_post_activation_migrations(1, converge_companions=False)

    assert seen == []


def test_verb_refuses_arguments_and_an_untrusted_tree(
    monkeypatch: pytest.MonkeyPatch, unit_dst: Path, release: Path, tmp_path: Path
) -> None:
    unit_dst.write_text(OLD_SHAPE, encoding="utf-8")
    _patch_run(monkeypatch, FakeRun())
    link = tmp_path / "current"
    link.symlink_to(release)
    monkeypatch.setattr(privileged, "_current_symlink", lambda: link)
    assert privileged.main(["refresh-openwebui-unit", "/etc/passwd"]) == 1

    def untrusted(_path: Path) -> None:
        raise updater_mod.UpdatePrivilegeError("not root-owned", details={})

    monkeypatch.setattr(privileged, "assert_trusted_release_dir", untrusted)
    assert privileged.main(["refresh-openwebui-unit"]) == 1
    assert unit_dst.read_text(encoding="utf-8") == OLD_SHAPE


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

    assert updater_mod.converge_openwebui_unit() == "failed"


def test_a_failed_refresh_is_named_in_the_update_summary() -> None:
    from hal0.cli.update_commands import _MIGRATION_PASS_LABELS

    event = "updater.openwebui_unit_refresh_failed"
    assert event in updater_mod._NON_FATAL_MIGRATION_FAILURE_EVENTS
    assert "refresh-openwebui-unit" in _MIGRATION_PASS_LABELS[event]
