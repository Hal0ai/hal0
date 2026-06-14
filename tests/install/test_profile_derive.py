"""Hardware-probe profile derivation (FirstRun v2, design D4)."""

from __future__ import annotations

from hal0.config.schema import GPUInfo, HardwareInfo, NPUInfo
from hal0.install.profile_derive import derive_device, derive_profile


def _hw(*, platform="bare-metal-amd-gpu", compute=True, vulkan=True, npu=False):
    return HardwareInfo(
        cpu_model="x",
        cpu_cores=8,
        cpu_threads=16,
        ram_mb=131072,
        unified_memory_mb=131072,
        platform=platform,
        gpus=[
            GPUInfo(
                vendor="amd",
                name="g",
                vram_mb=80000,
                compute_capable=compute,
                vulkan_capable=vulkan,
            )
        ],
        npu=NPUInfo(present=npu, vendor="amd" if npu else "", name="", driver=""),
    )


def test_chat_on_rocm_box_picks_rocm_mtp():
    hw = _hw(compute=True)
    assert derive_device("chat", hw, npu_opt_in=False) == "gpu-rocm"
    assert derive_profile("chat", "gpu-rocm") == "rocm-mtp"


def test_chat_on_vulkan_only_box_picks_vulkan():
    hw = _hw(compute=False, vulkan=True)
    assert derive_device("chat", hw, npu_opt_in=False) == "gpu-vulkan"
    assert derive_profile("chat", "gpu-vulkan") == "vulkan"


def test_embed_on_rocm_box_is_rocm_not_mtp():
    assert derive_profile("embed", "gpu-rocm") == "rocm"


def test_npu_trio_requires_present_and_optin():
    assert derive_device("agent", _hw(npu=True), npu_opt_in=True) == "npu"
    assert derive_device("agent", _hw(npu=True), npu_opt_in=False) is None
    assert derive_device("agent", _hw(npu=False), npu_opt_in=True) is None
    assert derive_profile("agent", "npu") == "flm"


def test_tts_is_cpu_kokoro():
    assert derive_device("tts", _hw(), npu_opt_in=False) == "cpu"
    assert derive_profile("tts", "cpu") == "tts"


def test_strix_platform_forces_rocm_even_if_compute_flag_missing():
    # platform=strix-halo is the canonical FP4 signal.
    hw = _hw(platform="strix-halo", compute=False, vulkan=True)
    assert derive_device("chat", hw, npu_opt_in=False) == "gpu-rocm"
