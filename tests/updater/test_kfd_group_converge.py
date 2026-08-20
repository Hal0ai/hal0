"""#1953 — converge /dev/kfd's group with the render node's on every update.

A plain LXC ``dev`` passthrough lands ``/dev/dri/renderD128`` as ``root:render``
and ``/dev/kfd`` as ``root:root``. The rootful slot containers open both, so
ROCm genuinely works — but every hal0-user probe reports the GPU unusable, and
#1923's guard then refuses every AMD GPU slot on a healthy box.

The installer fixes this at install time; existing boxes only ever see it if the
update path converges it too (the #1689 shape: an asset the installer applies
and the updater never re-applies never reaches an upgraded box).
"""

from __future__ import annotations

import hal0.updater.updater as updater_mod


class TestConvergeKfdGroup:
    def test_skipped_on_a_non_amd_host(self, monkeypatch) -> None:
        """No amdgpu bound → no compute node to align. Never touch the box."""
        monkeypatch.setattr("hal0.providers._gpu.host_is_amd_gpu", lambda *a, **k: False)
        called: list[object] = []
        monkeypatch.setattr(
            "hal0.providers._gpu.converge_kfd_device_group",
            lambda *a, **k: called.append(1) or ("changed", ""),
        )
        assert updater_mod.converge_kfd_group() == "skipped"
        assert called == []

    def test_converges_on_an_amd_host(self, monkeypatch) -> None:
        monkeypatch.setattr("hal0.providers._gpu.host_is_amd_gpu", lambda *a, **k: True)
        monkeypatch.setattr(
            "hal0.providers._gpu.converge_kfd_device_group",
            lambda *a, **k: ("changed", "gid 0 -> 993"),
        )
        assert updater_mod.converge_kfd_group() == "changed"

    def test_a_refused_chgrp_is_reported_not_raised(self, monkeypatch) -> None:
        """Unprivileged LXC: chgrp is EPERM. An update must not abort over it."""
        monkeypatch.setattr("hal0.providers._gpu.host_is_amd_gpu", lambda *a, **k: True)
        monkeypatch.setattr(
            "hal0.providers._gpu.converge_kfd_device_group",
            lambda *a, **k: ("failed", "Operation not permitted"),
        )
        assert updater_mod.converge_kfd_group() == "failed"


class TestMigrationOrdering:
    def test_kfd_converge_runs_before_the_vulkan_relabel(self) -> None:
        """Ordering is load-bearing, not cosmetic.

        ``relabel_stale_vulkan_slots`` branches on the compute node's state to
        pick ``gpu-rocm`` vs ``cpu``. If the converge ran after it, the relabel
        would decide from the un-repaired device and the two passes could reach
        different conclusions about the same box.
        """
        import inspect

        src = inspect.getsource(updater_mod.run_post_activation_migrations)
        assert src.index("converge_kfd_group(") < src.index("relabel_stale_vulkan_slots(")
