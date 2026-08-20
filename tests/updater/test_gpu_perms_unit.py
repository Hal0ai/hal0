"""#1953 durability — the boot-time converge and its delivery to existing boxes.

Two gaps this closes:

1. ``/dev/kfd`` is recreated every boot, so the install/update-time converge is
   undone unless the host's LXC ``dev`` entry carries ``gid=``.
2. A unit shipped only by ``install.sh`` never reaches a box that upgrades via
   ``hal0 update`` — the #1689 shape.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import hal0.updater.updater as updater_mod
from hal0.install import gpu_perms


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
