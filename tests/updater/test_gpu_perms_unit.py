"""#1953 durability — the boot-time converge and its delivery to existing boxes.

Two gaps this closes:

1. ``/dev/kfd`` is recreated every boot, so the install/update-time converge is
   undone unless the host's LXC ``dev`` entry carries ``gid=``.
2. A unit shipped only by ``install.sh`` never reaches a box that upgrades via
   ``hal0 update`` — the #1689 shape.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

import hal0.updater.updater as updater_mod
from hal0.install import gpu_perms


class FakeRun:
    """Recording stand-in for ``subprocess.run`` — never touches the real OS."""

    def __init__(self, *, returncode: int = 0, stdout: str = "", stderr: bytes = b"") -> None:
        self.calls: list[list[str]] = []
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, self.stderr)


class TestGpuPermsEntryPoint:
    def test_non_amd_host_is_a_noop(self, monkeypatch) -> None:
        monkeypatch.setattr("hal0.providers._gpu.host_is_amd_gpu", lambda *a, **k: False)
        called: list[int] = []
        monkeypatch.setattr(
            "hal0.providers._gpu.converge_kfd_device_group",
            lambda *a, **k: called.append(1) or ("changed", ""),
        )
        assert gpu_perms.main() == 0
        assert called == []

    @pytest.mark.parametrize("status", ["changed", "noop", "skipped", "failed"])
    def test_every_outcome_exits_zero(self, status, monkeypatch) -> None:
        """A GPU-permissions tidy-up must never be able to wedge the boot."""
        monkeypatch.setattr("hal0.providers._gpu.host_is_amd_gpu", lambda *a, **k: True)
        monkeypatch.setattr(
            "hal0.providers._gpu.converge_kfd_device_group",
            lambda *a, **k: (status, "detail"),
        )
        assert gpu_perms.main() == 0

    def test_an_unexpected_exception_still_exits_zero(self, monkeypatch) -> None:
        monkeypatch.setattr("hal0.providers._gpu.host_is_amd_gpu", lambda *a, **k: True)

        def _boom(*a, **k):
            raise RuntimeError("nope")

        monkeypatch.setattr("hal0.providers._gpu.converge_kfd_device_group", _boom)
        assert gpu_perms.main() == 0


class TestGpuPermsUnitRefresh:
    def test_unprivileged_is_a_noop(self, monkeypatch, tmp_path: Path) -> None:
        """The unprivileged daemon must never write /etc/systemd/system."""
        monkeypatch.setattr(os, "geteuid", lambda: 1000)
        assert updater_mod.refresh_gpu_perms_unit(tmp_path) == "skipped"

    def test_a_release_without_the_unit_is_skipped_not_fatal(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(os, "geteuid", lambda: 0)
        assert updater_mod.refresh_gpu_perms_unit(tmp_path) == "skipped"


def _release_tree_with_unit(tmp_path: Path) -> Path:
    target = tmp_path / "hal0-1.0.0"
    unit_dir = target / "installer" / "systemd"
    unit_dir.mkdir(parents=True)
    (unit_dir / "hal0-gpu-perms.service").write_text(
        "[Unit]\n"
        "Description=hal0 GPU device permissions\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        "ExecStart=/usr/lib/hal0/venv/bin/python -m hal0.install.gpu_perms\n"
        "\n"
        "[Install]\n"
        "WantedBy=hal0.target\n",
        encoding="utf-8",
    )
    return target


class TestGpuPermsUnitPrefixRewrite:
    """#1982 — the update-path refresh must apply the same HAL0_PREFIX venv
    rewrite ``install.sh`` performs at install time, or a custom-prefix box's
    ``hal0-gpu-perms.service`` dies ``203/EXEC`` on every subsequent update.
    """

    @pytest.fixture
    def unit_dst(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        dst_dir = tmp_path / "etc-systemd-system"
        dst_dir.mkdir()
        dst = dst_dir / "hal0-gpu-perms.service"
        monkeypatch.setattr("hal0.updater.updater.GPU_PERMS_UNIT_DST", dst)
        return dst

    def test_default_prefix_is_written_verbatim(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, unit_dst: Path
    ) -> None:
        monkeypatch.setattr(os, "geteuid", lambda: 0)
        monkeypatch.setattr("hal0.updater.updater.sys.prefix", "/usr/lib/hal0/venv")
        monkeypatch.setattr("hal0.updater.updater.subprocess.run", FakeRun())
        target = _release_tree_with_unit(tmp_path)

        result = updater_mod.refresh_gpu_perms_unit(target)

        assert result == "installed"
        assert "ExecStart=/usr/lib/hal0/venv/bin/python" in unit_dst.read_text()

    def test_custom_prefix_rewrites_the_interpreter_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, unit_dst: Path
    ) -> None:
        """Mirrors install.sh's ``VENV_DIR`` rewrite (installer/install.sh:1630-1645):
        the ExecStart interpreter must point at the ACTUAL venv this process
        is running from, not the FHS default baked into the bundled unit.
        """
        monkeypatch.setattr(os, "geteuid", lambda: 0)
        custom_venv = tmp_path / "opt" / "hal0" / "venv"
        (custom_venv / "bin").mkdir(parents=True)
        (custom_venv / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
        monkeypatch.setattr("hal0.updater.updater.sys.prefix", str(custom_venv))
        monkeypatch.setattr("hal0.updater.updater.subprocess.run", FakeRun())
        target = _release_tree_with_unit(tmp_path)

        result = updater_mod.refresh_gpu_perms_unit(target)

        assert result == "installed"
        written = unit_dst.read_text()
        assert "/usr/lib/hal0/venv" not in written
        exec_line = next(ln for ln in written.splitlines() if ln.startswith("ExecStart="))
        interpreter = exec_line.removeprefix("ExecStart=").split()[0]
        assert Path(interpreter).is_file(), (
            f"rewritten ExecStart interpreter {interpreter!r} does not exist — "
            "the unit would die 203/EXEC on activation"
        )
        assert interpreter == str(custom_venv / "bin" / "python")


class TestUnitFileContract:
    """The unit's ordering is load-bearing; assert it rather than trusting it."""

    def _unit(self) -> str:
        root = Path(__file__).resolve().parents[2]
        return (root / "installer" / "systemd" / "hal0-gpu-perms.service").read_text()

    def test_runs_after_dev_is_populated_and_before_hal0(self) -> None:
        unit = self._unit()
        # There is no node to chgrp until /dev is populated...
        assert "systemd-tmpfiles-setup-dev.service" in unit
        # ...and slots must observe the converged state, never race it.
        assert "Before=hal0.target" in unit

    def test_is_a_oneshot_that_cannot_wedge_the_boot(self) -> None:
        unit = self._unit()
        assert "Type=oneshot" in unit
        assert "ConditionPathExists=/dev/kfd" in unit

    def test_does_not_bake_a_gid(self) -> None:
        """The whole point: the gid is re-derived from the render node at boot.

        A tmpfiles.d/udev rule would have to name one, and a baked gid is not
        portable — hence a service.
        """
        directives = [
            ln for ln in self._unit().splitlines() if ln.strip() and not ln.lstrip().startswith("#")
        ]
        body = "\n".join(directives)
        assert "gid=" not in body
        assert "993" not in body
