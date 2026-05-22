"""Unit tests for hal0.api.routes.hardware — flatten + platform pass-through.

The flatten shape is consumed by the Vue Hardware + FirstRun views; we
freeze its contract here so a future refactor of HardwareInfo doesn't
silently regress the dashboard.
"""

from __future__ import annotations

from hal0.api.routes.hardware import _flatten_for_ui, _platform_label
from hal0.config.schema import GPUInfo, HardwareInfo, NPUInfo


def test_flatten_pass_through_kvm_with_virtio_gpu() -> None:
    info = HardwareInfo(
        cpu_model="QEMU Virtual CPU version 2.5+",
        cpu_cores=4,
        cpu_threads=4,
        ram_mb=16384,
        unified_memory_mb=16384,
        gpus=[GPUInfo(vendor="unknown", name="Red Hat, Inc. Virtio 1.0 GPU")],
        npu=NPUInfo(present=False),
        platform="kvm",
    ).model_dump(mode="python")
    flat = _flatten_for_ui(info)
    assert flat["cpu_name"] == "QEMU Virtual CPU version 2.5+"
    assert flat["ram_total_mb"] == 16384
    assert flat["gpu_name"].startswith("Red Hat")
    assert flat["platform"] == "kvm"
    assert flat["platform_label"] == "KVM virtual machine"
    assert flat["memory_kind"] == "system"
    assert flat["npu_present"] is False


def test_flatten_strix_halo_is_unified() -> None:
    info = HardwareInfo(
        cpu_model="AMD Ryzen AI Max+ PRO 395",
        cpu_cores=16,
        cpu_threads=32,
        ram_mb=128 * 1024,
        unified_memory_mb=128 * 1024,
        gpus=[GPUInfo(vendor="amd", name="Radeon 8060S", vram_mb=96 * 1024)],
        npu=NPUInfo(present=True, vendor="amd", name="AMD NPU (XDNA)"),
        platform="strix-halo",
    ).model_dump(mode="python")
    flat = _flatten_for_ui(info)
    assert flat["platform"] == "strix-halo"
    assert flat["platform_label"] == "Strix Halo (unified memory)"
    assert flat["memory_kind"] == "unified"
    assert flat["is_uma"] is True


def test_flatten_bare_metal_nvidia_promotes_gpu_into_label() -> None:
    info = HardwareInfo(
        cpu_model="Intel i9-13900K",
        cpu_cores=8,
        cpu_threads=24,
        ram_mb=64 * 1024,
        unified_memory_mb=64 * 1024,
        gpus=[GPUInfo(vendor="nvidia", name="NVIDIA GeForce RTX 4080", vram_mb=16 * 1024)],
        npu=NPUInfo(present=False),
        platform="bare-metal-nvidia-gpu",
    ).model_dump(mode="python")
    flat = _flatten_for_ui(info)
    assert flat["memory_kind"] == "system"
    assert flat["platform_label"] == "Bare metal — NVIDIA GeForce RTX 4080"
    assert flat["vram_total_mb"] == 16 * 1024
    assert flat["gtt_total_mb"] == 0


def test_flatten_handles_legacy_payload_without_platform() -> None:
    """A pre-platform /etc/hal0/hardware.json on disk should still flatten
    cleanly — we don't want stale caches to crash the dashboard.
    """
    info = HardwareInfo(
        cpu_model="Generic x86_64",
        ram_mb=8192,
        gpus=[],
        npu=NPUInfo(present=False),
    ).model_dump(mode="python")
    # Simulate a HardwareInfo missing the platform key altogether
    info.pop("platform", None)
    flat = _flatten_for_ui(info)
    assert flat["platform"] == "unknown"
    assert flat["platform_label"] == "Unknown platform"
    assert flat["memory_kind"] == "system"


def test_platform_label_for_known_strings() -> None:
    assert _platform_label("wsl2", {}) == "WSL 2"
    assert _platform_label("proxmox-kvm", {}) == "Proxmox VM (KVM)"
    assert _platform_label("lxc", {}) == "Linux container (LXC)"
    assert _platform_label("nonsense-value", {}) == "Unknown platform"
