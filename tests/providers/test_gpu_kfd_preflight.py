"""#1888 — /dev/kfd is a hard requirement for AMD-GPU llama.cpp slots.

The release-pinned ROCmFPX runner is one HIP+Vulkan build. llama.cpp picks
ROCm when ``/dev/kfd`` is visible and SILENTLY falls back to that image's
Vulkan backend when ``ggml_rocm_init`` fails — and that Vulkan backend emits
invalid tokens for every model it serves, at full nominal speed, while HTTP
200, container health, ``hal0 doctor`` and the SSE ``done`` frame all read
green. A box without ``/dev/kfd`` therefore has no valid GPU lane at all, and
the only honest behaviour is to refuse the load and say why.

Regression shape: before this, ``load_sync`` happily started such a slot and
every text surface on the box was garbage for hours.
"""

from __future__ import annotations

import os

import pytest

from hal0.providers._gpu import (
    ENV_ALLOW_VULKAN_FALLBACK,
    KFD_MISSING,
    KFD_NOT_OPENABLE,
    KFD_OK,
    GpuPreflightError,
    converge_kfd_device_group,
    host_is_amd_gpu,
    kfd_present,
    kfd_status,
    require_kfd_for_gpu_slot,
    resolve_kfd_target_gid,
)


class TestKfdPresent:
    def test_true_when_the_node_exists(self, tmp_path) -> None:
        node = tmp_path / "kfd"
        node.write_text("")
        assert kfd_present(str(node)) is True

    def test_false_when_it_does_not(self, tmp_path) -> None:
        assert kfd_present(str(tmp_path / "kfd")) is False

    def test_false_when_it_exists_but_this_process_cannot_open_it(self, tmp_path) -> None:
        """An LXC passthrough with a mis-mapped gid leaves the node visible but
        unopenable — HIP still fails and llama.cpp still lands on the invalid
        Vulkan lane, so existence alone must not count as a pass.

        ``for_uid=None`` asks about THIS process, which is the only identity a
        DAC probe can honestly answer for.
        """
        node = tmp_path / "kfd"
        node.write_text("")
        node.chmod(0o000)
        try:
            if os.access(str(node), os.R_OK):  # running as root — chmod is advisory
                pytest.skip("root bypasses file permissions")
            assert kfd_present(str(node), for_uid=None) is False
        finally:
            node.chmod(0o600)

    def test_unopenable_node_still_passes_for_the_root_slot_runner(self, tmp_path) -> None:
        """#1953: the identity that matters is the one that OPENS the device.

        hal0-api runs as ``hal0``; the slot containers run rootful as root. A
        plain LXC passthrough routinely leaves ``/dev/kfd`` ``root:root 0660``
        while the render node lands ``root:render 0660`` — ROCm works fine in
        the container, but a probe run as ``hal0`` used to report False and
        #1923's guard then refused every AMD GPU slot on a healthy box.
        """
        node = tmp_path / "kfd"
        node.write_text("")
        node.chmod(0o660)  # group-only rw, group is the test user's — not hal0's
        assert kfd_present(str(node)) is True  # default: SLOT_RUNNER_UID (root)
        assert kfd_status(str(node), for_uid=0) == KFD_OK

    def test_status_distinguishes_missing_from_unopenable(self, tmp_path) -> None:
        """The two shapes need OPPOSITE remedies, so they must not be one bool."""
        assert kfd_status(str(tmp_path / "nope"), for_uid=0) == KFD_MISSING
        node = tmp_path / "kfd"
        node.write_text("")
        node.chmod(0o000)
        try:
            if os.access(str(node), os.R_OK):
                pytest.skip("root bypasses file permissions")
            assert kfd_status(str(node), for_uid=None) == KFD_NOT_OPENABLE
        finally:
            node.chmod(0o600)


class TestHostIsAmdGpu:
    def test_true_when_the_amdgpu_module_dir_exists(self, tmp_path) -> None:
        assert host_is_amd_gpu(str(tmp_path)) is True

    def test_false_otherwise(self, tmp_path) -> None:
        assert host_is_amd_gpu(str(tmp_path / "nope")) is False


class TestRequireKfdForGpuSlot:
    @pytest.mark.parametrize("device", ["gpu-rocm", "gpu-vulkan"])
    def test_amd_gpu_slot_without_kfd_is_refused(self, device, tmp_path) -> None:
        with pytest.raises(GpuPreflightError) as exc:
            require_kfd_for_gpu_slot(
                "agent", device=device, kfd_path=str(tmp_path / "kfd"), env={}, amd_host=True
            )
        msg = str(exc.value)
        # The operator must be able to act on this without reading the source.
        assert "agent" in msg
        assert "/kfd" in msg
        assert "1888" in msg

    @pytest.mark.parametrize("device", ["gpu-rocm", "gpu-vulkan"])
    def test_passes_when_kfd_is_present(self, device, tmp_path) -> None:
        node = tmp_path / "kfd"
        node.write_text("")
        require_kfd_for_gpu_slot("agent", device=device, kfd_path=str(node), env={}, amd_host=True)

    def test_gpu_vulkan_on_a_non_amd_host_is_not_gated(self, tmp_path) -> None:
        """An Intel iGPU / NVIDIA-without-CDI box has no /dev/kfd by design and
        is not the hardware #1888 was characterised on — demanding one there
        would strand every GPU slot on unaffected hardware."""
        require_kfd_for_gpu_slot(
            "utility",
            device="gpu-vulkan",
            kfd_path=str(tmp_path / "kfd"),
            env={},
            amd_host=False,
        )

    def test_gpu_rocm_is_gated_even_on_a_non_amd_host(self, tmp_path) -> None:
        """The device name IS the ROCm claim — a gpu-rocm slot with no compute
        node is broken regardless of what the host reports."""
        with pytest.raises(GpuPreflightError):
            require_kfd_for_gpu_slot(
                "agent",
                device="gpu-rocm",
                kfd_path=str(tmp_path / "kfd"),
                env={},
                amd_host=False,
            )

    @pytest.mark.parametrize("device", ["cpu", "npu", "gpu-cuda", "", "img"])
    def test_non_rocmfpx_devices_are_untouched(self, device, tmp_path) -> None:
        """CPU/NPU slots need no compute node, and gpu-cuda runs the upstream
        CUDA image via CDI — none of them can hit the Vulkan fallback."""
        require_kfd_for_gpu_slot(
            "x", device=device, kfd_path=str(tmp_path / "kfd"), env={}, amd_host=True
        )

    def test_explicit_opt_in_downgrades_to_a_warning(self, tmp_path) -> None:
        require_kfd_for_gpu_slot(
            "agent",
            device="gpu-rocm",
            kfd_path=str(tmp_path / "kfd"),
            env={ENV_ALLOW_VULKAN_FALLBACK: "1"},
            amd_host=True,
        )

    def test_a_non_truthy_opt_in_still_refuses(self, tmp_path) -> None:
        with pytest.raises(GpuPreflightError):
            require_kfd_for_gpu_slot(
                "agent",
                device="gpu-rocm",
                kfd_path=str(tmp_path / "kfd"),
                env={ENV_ALLOW_VULKAN_FALLBACK: "0"},
                amd_host=True,
            )


class TestLoadSyncGuard:
    """The guard must fire on the LOAD path, not on unit rendering — a
    preview/status render has to stay host-independent."""

    def test_load_sync_refuses_a_gpu_slot_with_no_kfd(self, monkeypatch) -> None:
        from hal0.providers import container as container_mod

        monkeypatch.setattr(container_mod, "kfd_present_used", False, raising=False)
        monkeypatch.setattr(container_mod, "require_kfd_for_gpu_slot", _raiser, raising=True)
        provider = container_mod.ContainerProvider()
        with pytest.raises(GpuPreflightError):
            provider.load_sync({"name": "agent", "device": "gpu-rocm", "port": 8081}, {})

    def test_load_sync_calls_the_guard_with_the_slots_device(self, monkeypatch) -> None:
        from hal0.providers import container as container_mod

        seen: dict[str, str] = {}

        def _spy(slot_name: str, *, device: str, **_kw: object) -> None:
            seen["slot"] = slot_name
            seen["device"] = device
            raise GpuPreflightError("stop here — the rest of load_sync writes units")

        monkeypatch.setattr(container_mod, "require_kfd_for_gpu_slot", _spy)
        provider = container_mod.ContainerProvider()
        with pytest.raises(GpuPreflightError):
            provider.load_sync({"name": "utility", "device": "gpu-vulkan", "port": 8082}, {})
        assert seen == {"slot": "utility", "device": "gpu-vulkan"}


def _raiser(*_a: object, **_kw: object) -> None:
    raise GpuPreflightError("no /dev/kfd")


class TestResolveKfdTargetGid:
    """#1953: the target gid must come from the DEVICE NODE, never a group name.

    The kernel gates on the integer. On a halo143-class box ``renderD128`` is
    owned by gid 993 whose ``/etc/group`` name is ``clock``, while ``render``
    resolves to a different, useless gid — so ``grp.getgrnam("render")``
    produces a number that grants nothing. Baking in 993 (or any constant) is
    the same bug wearing a different hat.
    """

    def test_follows_the_render_node_gid(self, tmp_path, monkeypatch) -> None:
        node = tmp_path / "renderD128"
        node.write_text("")
        os.chown(node, -1, os.getgid())
        monkeypatch.setattr(
            "hal0.providers._gpu.resolve_gpu_device_paths", lambda *a, **k: [str(node)]
        )
        monkeypatch.setattr(
            "hal0.providers._gpu._device_node_for_group",
            lambda name, paths: str(node) if name == "render" else None,
        )
        assert resolve_kfd_target_gid() == os.stat(node).st_gid

    def test_returns_none_rather_than_guessing_a_constant(self, monkeypatch) -> None:
        """No render node → no opinion. Guessing 993 is how a wrong gid gets baked in."""
        monkeypatch.setattr("hal0.providers._gpu.resolve_gpu_device_paths", lambda *a, **k: [])
        monkeypatch.setattr("hal0.providers._gpu._device_node_for_group", lambda name, paths: None)
        assert resolve_kfd_target_gid() is None


class TestConvergeKfdDeviceGroup:
    def test_noop_when_already_aligned(self, tmp_path, monkeypatch) -> None:
        kfd = tmp_path / "kfd"
        kfd.write_text("")
        kfd.chmod(0o660)
        monkeypatch.setattr(
            "hal0.providers._gpu.resolve_kfd_target_gid",
            lambda *a, **k: os.stat(kfd).st_gid,
        )
        status, _ = converge_kfd_device_group(str(kfd))
        assert status == "noop"

    def test_skips_when_no_render_node_to_learn_from(self, tmp_path, monkeypatch) -> None:
        kfd = tmp_path / "kfd"
        kfd.write_text("")
        monkeypatch.setattr("hal0.providers._gpu.resolve_kfd_target_gid", lambda *a, **k: None)
        status, detail = converge_kfd_device_group(str(kfd))
        assert status == "skipped"
        assert "render node" in detail

    def test_skips_when_the_node_is_absent(self, tmp_path) -> None:
        status, _ = converge_kfd_device_group(str(tmp_path / "nope"))
        assert status == "skipped"

    def test_dry_run_reports_without_writing(self, tmp_path, monkeypatch) -> None:
        kfd = tmp_path / "kfd"
        kfd.write_text("")
        kfd.chmod(0o600)
        before = os.stat(kfd).st_mode
        monkeypatch.setattr("hal0.providers._gpu.resolve_kfd_target_gid", lambda *a, **k: 4242)
        status, _ = converge_kfd_device_group(str(kfd), dry_run=True)
        assert status == "changed"
        assert os.stat(kfd).st_mode == before

    def test_failure_is_reported_not_raised(self, tmp_path, monkeypatch) -> None:
        """Unprivileged LXC: chgrp is EPERM. Surface the host remedy, never abort."""
        kfd = tmp_path / "kfd"
        kfd.write_text("")
        monkeypatch.setattr("hal0.providers._gpu.resolve_kfd_target_gid", lambda *a, **k: 4242)

        def _boom(*a, **k):
            raise PermissionError("Operation not permitted")

        monkeypatch.setattr(os, "chown", _boom)
        status, detail = converge_kfd_device_group(str(kfd))
        assert status == "failed"
        assert "gid=4242" in detail


class TestGuardRemedyText:
    def test_unopenable_does_not_tell_you_to_re_forward_the_device(
        self, tmp_path, monkeypatch
    ) -> None:
        """#1953's headline harm: the old text sent operators to reboot production
        for a device that was already forwarded."""
        kfd = tmp_path / "kfd"
        kfd.write_text("")
        monkeypatch.setattr("hal0.providers._gpu.kfd_status", lambda *a, **k: KFD_NOT_OPENABLE)
        monkeypatch.setattr("hal0.providers._gpu.resolve_kfd_target_gid", lambda *a, **k: 993)
        with pytest.raises(GpuPreflightError) as err:
            require_kfd_for_gpu_slot("brain", device="gpu-rocm", kfd_path=str(kfd))
        msg = str(err.value)
        assert "IS forwarded" in msg
        assert "chgrp 993" in msg
        assert "pct stop/start" in msg  # only as the unprivileged-LXC sub-case
        assert "dev1:" not in msg  # the WRONG remedy must not appear

    def test_missing_still_tells_you_to_forward_it(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("hal0.providers._gpu.kfd_status", lambda *a, **k: KFD_MISSING)
        with pytest.raises(GpuPreflightError) as err:
            require_kfd_for_gpu_slot("brain", device="gpu-rocm", kfd_path=str(tmp_path / "nope"))
        assert "dev1:" in str(err.value)
