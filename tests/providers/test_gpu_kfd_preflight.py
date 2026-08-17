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
    GpuPreflightError,
    host_is_amd_gpu,
    kfd_present,
    require_kfd_for_gpu_slot,
)


class TestKfdPresent:
    def test_true_when_the_node_exists(self, tmp_path) -> None:
        node = tmp_path / "kfd"
        node.write_text("")
        assert kfd_present(str(node)) is True

    def test_false_when_it_does_not(self, tmp_path) -> None:
        assert kfd_present(str(tmp_path / "kfd")) is False

    def test_false_when_it_exists_but_is_not_accessible(self, tmp_path) -> None:
        """An LXC passthrough with a mis-mapped gid leaves the node visible but
        unopenable — HIP still fails and llama.cpp still lands on the invalid
        Vulkan lane, so existence alone must not count as a pass."""
        node = tmp_path / "kfd"
        node.write_text("")
        node.chmod(0o000)
        try:
            if os.access(str(node), os.R_OK):  # running as root — chmod is advisory
                pytest.skip("root bypasses file permissions")
            assert kfd_present(str(node)) is False
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
