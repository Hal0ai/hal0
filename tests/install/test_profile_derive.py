"""Hardware-probe profile derivation (FirstRun v2, design D4)."""

from __future__ import annotations

from hal0.config.schema import GPUInfo, HardwareInfo, NPUInfo
from hal0.install.profile_derive import (
    derive_device,
    derive_profile,
    npu_takes_utility,
)


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
    assert derive_profile("chat", "gpu-rocm") == "rocm-dnse"


def test_chat_on_vulkan_only_box_picks_vulkan():
    hw = _hw(compute=False, vulkan=True)
    assert derive_device("chat", hw, npu_opt_in=False) == "gpu-vulkan"
    assert derive_profile("chat", "gpu-vulkan") == "vulkan"


def test_embed_on_rocm_box_is_rocm_not_mtp():
    assert derive_profile("embed", "gpu-rocm") == "rocm"


def test_npu_chat_lane_requires_present_and_optin():
    # The NPU chat lane (agent / utility role) is selected only when the NPU
    # is present AND the operator opted in.
    assert derive_device("agent", _hw(npu=True), npu_opt_in=True) == "npu"
    assert derive_device("agent", _hw(npu=True), npu_opt_in=False) is None
    assert derive_device("agent", _hw(npu=False), npu_opt_in=True) is None
    assert derive_profile("agent", "npu") == "flm"


def test_npu_present_is_chat_only_no_trio_passengers():
    """NPU-present box: chat lane goes to NPU, but the stt/embed trio
    passengers are NOT auto-provisioned (design 2026-06-15)."""
    hw = _hw(npu=True)
    # chat lane on the NPU
    assert derive_device("agent", hw, npu_opt_in=True) == "npu"
    # trio shadow passengers are gated off entirely
    assert derive_device("stt-npu", hw, npu_opt_in=True) is None
    assert derive_device("embed-npu", hw, npu_opt_in=True) is None


def test_embed_on_npu_box_derives_to_gpu_not_npu():
    """If embed is selected on an NPU box, it derives to the GPU lane,
    never to the NPU (design 2026-06-15)."""
    hw = _hw(npu=True, compute=True)
    assert derive_device("embed", hw, npu_opt_in=True) == "gpu-rocm"
    assert derive_profile("embed", "gpu-rocm") == "rocm"


def test_npu_takes_utility_when_present_and_optin():
    """The iGPU `utility` slot is suppressed when the NPU claims the
    utility role; otherwise utility stays on the iGPU (design 2026-06-15)."""
    # NPU present + opted in → NPU takes utility, iGPU utility suppressed.
    assert npu_takes_utility(_hw(npu=True), npu_opt_in=True) is True
    # opted out → iGPU keeps utility.
    assert npu_takes_utility(_hw(npu=True), npu_opt_in=False) is False
    # no NPU → iGPU keeps utility.
    assert npu_takes_utility(_hw(npu=False), npu_opt_in=True) is False


def test_utility_capability_routes_like_chat_lane():
    """The `utility` capability follows the chat lane: NPU when claimed,
    else the GPU lane (so the iGPU utility seed stays coherent)."""
    npu_box = _hw(npu=True, compute=True)
    assert derive_device("utility", npu_box, npu_opt_in=True) == "npu"
    assert derive_profile("utility", "npu") == "flm"
    # NPU-absent → utility stays on the iGPU.
    igpu_box = _hw(npu=False, compute=True)
    assert derive_device("utility", igpu_box, npu_opt_in=False) == "gpu-rocm"


def test_tts_is_cpu_kokoro():
    assert derive_device("tts", _hw(), npu_opt_in=False) == "cpu"
    assert derive_profile("tts", "cpu") == "tts"


def test_strix_platform_forces_rocm_even_if_compute_flag_missing():
    # platform=strix-halo is the canonical FP4 signal.
    hw = _hw(platform="strix-halo", compute=False, vulkan=True)
    assert derive_device("chat", hw, npu_opt_in=False) == "gpu-rocm"
