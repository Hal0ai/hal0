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


class TestConvergeIsNotInTheMigrationChain:
    """#1953 review: the converge must NOT run from run_post_activation_migrations.

    That chain runs in an asyncio.to_thread inside hal0-api (User=hal0) and
    BEFORE seam.activate(). Calling the converge from there was wrong twice
    over: chown hit EPERM as an unprivileged user and reported "failed" for
    exactly the boxes it was meant to fix, and on the FIRST update delivering
    the code the running daemon is still the PREVIOUS release, where the
    function does not exist at all.

    It now runs root-side in refresh_gpu_perms_unit during activate.
    """

    def test_migrations_do_not_call_the_converge(self) -> None:
        import inspect

        src = inspect.getsource(updater_mod.run_post_activation_migrations)
        assert "converge_kfd_group(" not in src

    def test_the_privileged_unit_refresh_starts_the_unit(self) -> None:
        """Starting it is what converges an updated box without a reboot."""
        import inspect

        src = inspect.getsource(updater_mod.refresh_gpu_perms_unit)
        assert '"start", "hal0-gpu-perms.service"' in src.replace("'", '"')

    def test_a_failed_enable_is_reported_not_swallowed(self) -> None:
        """Otherwise the next update sees identical content and never retries."""
        import inspect

        src = inspect.getsource(updater_mod.refresh_gpu_perms_unit)
        assert "gpu_perms_unit_enable_failed" in src
        assert "up_to_date" in src  # no early return that skips the enable retry
