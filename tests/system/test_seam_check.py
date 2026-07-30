"""#1465 — the sudo seams every slot op depends on must be *verified*, not assumed.

``install.sh`` installs each wrapper + ``/etc/sudoers.d`` grant best-effort: a
``visudo -cf`` failure or a missing source file produced only a mid-log ``warn``
and the install still printed its success box. Nothing checked the result
afterwards — ``preflight_all`` never touched sudoers, ``doctor verify`` composes
only live-API rows, and ``doctor all``'s extras were auth/models/migrations/
ports/hal0.target. A box where that warn fired therefore reported **all green**
while every slot start, unit write and daemon-reload failed undiagnosably.

These tests pin the predicate: presence, ownership, mode, and — the fact that
actually matters — whether ``sudo -n`` works *as the hal0 user*.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from hal0.system.seam_check import (
    SEAMS,
    SeamSpec,
    grant_probe_argv,
    probe_seam,
    probe_seams,
)

_SYSTEMCTL = next(s for s in SEAMS if s.name == "hal0-systemctl")
_UPDATE = next(s for s in SEAMS if s.name == "hal0-update")
_OPTIONAL = next(s for s in SEAMS if not s.required)


def _install(tmp_path: Path, name: str, *, bin_mode: int = 0o755, grant_mode: int = 0o440) -> Any:
    """Lay down a fake seam pair and return (bin_dir, sudoers_dir)."""
    bin_dir = tmp_path / "bin"
    sudoers_dir = tmp_path / "sudoers.d"
    bin_dir.mkdir(exist_ok=True)
    sudoers_dir.mkdir(exist_ok=True)
    (bin_dir / name).write_text("#!/bin/bash\n")
    (bin_dir / name).chmod(bin_mode)
    (sudoers_dir / name).write_text(f"hal0 ALL=(root) NOPASSWD: /usr/lib/hal0/bin/{name}\n")
    (sudoers_dir / name).chmod(grant_mode)
    return bin_dir, sudoers_dir


def _stat_as_root(real_stat_path: Path) -> os.stat_result:
    """lstat that reports uid/gid 0 so only the *mode* drives the assertion."""
    st = os.lstat(real_stat_path)
    fields = list(st)
    fields[4] = 0
    fields[5] = 0
    return os.stat_result(fields)


class FakeRun:
    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.calls: list[list[str]] = []
        self.returncode = returncode
        self.stderr = stderr

    def __call__(self, argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, self.returncode, "", self.stderr)


# ── the inventory itself ───────────────────────────────────────────────────────


def test_inventory_covers_every_wrapper_install_sh_ships() -> None:
    """Adding a wrapper without adding it here is the exact #1465 regression."""
    repo = Path(__file__).resolve().parents[2]
    shipped = {p.name for p in (repo / "installer" / "wrappers").iterdir() if p.is_file()}
    granted = {p.name for p in (repo / "packaging" / "sudoers").iterdir() if p.is_file()}
    # Every wrapper that has a sudoers grant must be verified by doctor.
    assert granted <= {s.name for s in SEAMS}
    assert granted <= shipped


def test_required_seams_are_the_two_hal0_cannot_run_without() -> None:
    assert {s.name for s in SEAMS if s.required} == {"hal0-systemctl", "hal0-update"}


# ── file-level facts ───────────────────────────────────────────────────────────


def test_missing_wrapper_is_reported(tmp_path: Path) -> None:
    _, sudoers = _install(tmp_path, "hal0-systemctl")
    (tmp_path / "bin" / "hal0-systemctl").unlink()

    status = probe_seam(
        _SYSTEMCTL, bin_dir=tmp_path / "bin", sudoers_dir=sudoers, euid=1000, run=FakeRun()
    )

    assert status.binary_ok is False
    assert "is missing" in status.binary_detail
    assert status.ok is False


def test_missing_sudoers_grant_is_reported(tmp_path: Path) -> None:
    bin_dir, sudoers = _install(tmp_path, "hal0-systemctl")
    (sudoers / "hal0-systemctl").unlink()

    status = probe_seam(_SYSTEMCTL, bin_dir=bin_dir, sudoers_dir=sudoers, euid=1000, run=FakeRun())

    assert status.sudoers_ok is False
    assert "is missing" in status.sudoers_detail
    assert status.ok is False


def test_group_writable_sudoers_drop_in_is_reported(tmp_path: Path) -> None:
    """sudo silently ignores a drop-in with the wrong mode — a total failure."""
    bin_dir, sudoers = _install(tmp_path, "hal0-systemctl", grant_mode=0o644)

    status = probe_seam(
        _SYSTEMCTL,
        bin_dir=bin_dir,
        sudoers_dir=sudoers,
        euid=1000,
        run=FakeRun(),
        stat=_stat_as_root,
    )

    assert status.sudoers_ok is False
    assert "0644" in status.sudoers_detail


def test_non_root_owned_wrapper_is_reported(tmp_path: Path) -> None:
    bin_dir, sudoers = _install(tmp_path, "hal0-systemctl")

    status = probe_seam(_SYSTEMCTL, bin_dir=bin_dir, sudoers_dir=sudoers, euid=1000, run=FakeRun())

    # Files here are owned by the test user, never uid 0.
    assert status.binary_ok is False
    assert "owned by uid" in status.binary_detail


# ── the grant probe ────────────────────────────────────────────────────────────


def test_grant_probe_drops_to_the_service_account_when_root() -> None:
    argv = grant_probe_argv("hal0-systemctl", ("help",), euid=0, bin_dir=Path("/usr/lib/hal0/bin"))
    assert argv == [
        "sudo",
        "-n",
        "-u",
        "hal0",
        "sudo",
        "-n",
        "/usr/lib/hal0/bin/hal0-systemctl",
        "help",
    ]


def test_grant_probe_is_skipped_for_an_unrelated_unprivileged_user() -> None:
    """A grant written for `hal0` correctly fails for anyone else — not a defect."""
    assert grant_probe_argv("hal0-systemctl", ("help",), euid=4242) is None


def test_grant_failure_is_surfaced_with_the_sudo_error(tmp_path: Path) -> None:
    bin_dir, sudoers = _install(tmp_path, "hal0-update")
    run = FakeRun(returncode=1, stderr="sudo: a password is required")

    status = probe_seam(
        _UPDATE,
        bin_dir=bin_dir,
        sudoers_dir=sudoers,
        euid=0,
        run=run,
        stat=_stat_as_root,
    )

    assert status.grant_ok is False
    assert "a password is required" in status.grant_detail
    assert run.calls[0][:4] == ["sudo", "-n", "-u", "hal0"]
    assert status.ok is False


def test_working_grant_passes_every_fact(tmp_path: Path) -> None:
    bin_dir, sudoers = _install(tmp_path, "hal0-update")

    status = probe_seam(
        _UPDATE, bin_dir=bin_dir, sudoers_dir=sudoers, euid=0, run=FakeRun(), stat=_stat_as_root
    )

    assert (status.binary_ok, status.sudoers_ok, status.grant_ok) == (True, True, True)
    assert status.ok is True
    assert status.problems == []


def test_seam_without_a_probe_verb_is_presence_checked_only(tmp_path: Path) -> None:
    bin_dir, sudoers = _install(tmp_path, _OPTIONAL.name)
    run = FakeRun()

    status = probe_seam(
        _OPTIONAL, bin_dir=bin_dir, sudoers_dir=sudoers, euid=0, run=run, stat=_stat_as_root
    )

    assert status.grant_ok is None
    assert run.calls == []
    assert status.ok is True


def test_grant_is_not_probed_when_the_files_are_already_broken(tmp_path: Path) -> None:
    """Don't burn a sudo round-trip (or emit a confusing error) on a missing wrapper."""
    bin_dir, sudoers = _install(tmp_path, "hal0-update")
    (bin_dir / "hal0-update").unlink()
    run = FakeRun()

    status = probe_seam(_UPDATE, bin_dir=bin_dir, sudoers_dir=sudoers, euid=0, run=run)

    assert run.calls == []
    assert status.grant_ok is None


def test_probe_seams_walks_the_whole_inventory(tmp_path: Path) -> None:
    statuses = probe_seams(
        bin_dir=tmp_path / "nothing", sudoers_dir=tmp_path / "nothing", euid=1000, run=FakeRun()
    )
    assert [s.spec.name for s in statuses] == [s.name for s in SEAMS]
    assert all(s.ok is False for s in statuses)


def test_spec_is_frozen() -> None:
    """The inventory is a constant — nothing may mutate it at runtime."""
    with pytest.raises(dataclasses.FrozenInstanceError):
        _SYSTEMCTL.name = "other"  # type: ignore[misc]


def test_seam_spec_roles_are_actionable() -> None:
    assert all(isinstance(s, SeamSpec) and s.role for s in SEAMS)
