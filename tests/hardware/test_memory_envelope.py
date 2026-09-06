"""Tests for hal0.hardware.memory_envelope — the single-owner memory budget
function (#1868). Fixtures mirror the hal0 x ODS parity brief's matrix:
Strix Halo 128 GB, Strix Halo 64 GB, kfd-less AMD, NVIDIA, CPU-only.
"""

from __future__ import annotations

from hal0.config.schema import GPUInfo, HardwareInfo, NPUInfo
from hal0.hardware.memory_envelope import (
    CPU_MEMORY_CEILING_MIB,
    CPU_MEMORY_FLOOR_MIB,
    UNIFIED_MEMORY_FLOOR_MIB,
    clamp_context_size,
    max_affordable_context_tokens,
    memory_envelope,
)


def _strix_halo(ram_mb: int) -> HardwareInfo:
    return HardwareInfo(
        platform="strix-halo",
        ram_mb=ram_mb,
        unified_memory_mb=ram_mb,
        gpus=[
            GPUInfo(
                vendor="amd",
                name="Radeon 8060S",
                vram_mb=ram_mb,
                compute_capable=True,
                vulkan_capable=True,
            )
        ],
        npu=NPUInfo(present=True, vendor="amd"),
    )


def _kfd_less_amd(ram_mb: int = 32768) -> HardwareInfo:
    """An AMD box with a Vulkan-capable render node but no working ROCm."""
    return HardwareInfo(
        platform="bare-metal-amd-gpu",
        ram_mb=ram_mb,
        unified_memory_mb=ram_mb,
        gpus=[
            GPUInfo(
                vendor="amd",
                name="Radeon 780M",
                vram_mb=512,
                compute_capable=False,
                vulkan_capable=True,
            )
        ],
    )


def _nvidia(ram_mb: int = 65536, vram_mb: int = 24576) -> HardwareInfo:
    return HardwareInfo(
        platform="bare-metal-nvidia-gpu",
        ram_mb=ram_mb,
        unified_memory_mb=ram_mb,
        gpus=[
            GPUInfo(
                vendor="nvidia",
                name="RTX 4090",
                vram_mb=vram_mb,
                compute_capable=True,
                vulkan_capable=True,
            )
        ],
    )


def _cpu_only(ram_mb: int = 32768) -> HardwareInfo:
    return HardwareInfo(
        platform="bare-metal-cpu-only",
        ram_mb=ram_mb,
        unified_memory_mb=ram_mb,
        gpus=[],
    )


# ── memory_envelope ──────────────────────────────────────────────────────────


def test_strix_halo_128gb_uses_55_percent_of_ram():
    hw = _strix_halo(131072)
    env = memory_envelope(hw)
    assert env.source == "unified system memory"
    assert env.usable_mib == 131072 * 0.55


def test_strix_halo_64gb_uses_55_percent_of_ram():
    hw = _strix_halo(65536)
    env = memory_envelope(hw)
    assert env.source == "unified system memory"
    assert env.usable_mib == 65536 * 0.55


def test_strix_halo_small_ram_floors_at_the_unified_floor():
    hw = _strix_halo(2048)
    env = memory_envelope(hw)
    assert env.source == "unified system memory"
    assert env.usable_mib == UNIFIED_MEMORY_FLOOR_MIB


def test_kfd_less_amd_with_small_vram_is_not_treated_as_unified():
    """A Vulkan-only iGPU with a tiny dedicated VRAM carve-out is a discrete
    GPU pool, not a UMA pool sized like RAM — its own vram_mb is the budget."""
    hw = _kfd_less_amd(ram_mb=32768)
    env = memory_envelope(hw)
    assert env.source == "GPU VRAM"
    assert env.usable_mib == 512


def test_nvidia_discrete_gpu_uses_its_own_vram():
    hw = _nvidia(ram_mb=65536, vram_mb=24576)
    env = memory_envelope(hw)
    assert env.source == "GPU VRAM"
    assert env.usable_mib == 24576


def test_cpu_only_uses_bounded_fraction_of_ram():
    hw = _cpu_only(ram_mb=32768)
    env = memory_envelope(hw)
    assert env.source == "system RAM"
    assert env.usable_mib == CPU_MEMORY_CEILING_MIB  # 0.35 * 32768 > ceiling


def test_cpu_only_small_ram_floors():
    hw = _cpu_only(ram_mb=4096)
    env = memory_envelope(hw)
    assert env.source == "system RAM"
    assert env.usable_mib == CPU_MEMORY_FLOOR_MIB


def test_envelope_never_exceeds_actual_ram_on_a_tiny_cpu_box():
    """The floor constants bound the FRACTION, never a promise bigger than
    the box's real RAM (a sub-3 GiB CPU-only VM must not get a 3 GiB
    envelope it does not have)."""
    hw = _cpu_only(ram_mb=1024)
    env = memory_envelope(hw)
    assert env.usable_mib <= 1024


def test_envelope_never_exceeds_actual_ram_on_a_tiny_unified_box():
    hw = _strix_halo(1024)
    env = memory_envelope(hw)
    assert env.usable_mib <= 1024


def test_no_probe_defaults_reads_as_cpu_only():
    """An all-defaults HardwareInfo() (no probe yet) must never invent a GPU
    pool — CPU-only is the conservative floor."""
    env = memory_envelope(HardwareInfo())
    assert env.source == "system RAM"


# ── max_affordable_context_tokens / clamp_context_size ──────────────────────


def test_affordable_tokens_scale_with_envelope():
    small = max_affordable_context_tokens(_strix_halo(65536))
    large = max_affordable_context_tokens(_strix_halo(131072))
    assert large > small


def test_affordable_tokens_never_negative_when_model_exceeds_envelope():
    hw = _cpu_only(ram_mb=4096)  # envelope floors at 3072 MiB
    assert max_affordable_context_tokens(hw, model_mib=999999.0) == 0


def test_clamp_context_size_passes_through_when_it_fits():
    hw = _strix_halo(131072)
    clamped, warning = clamp_context_size(65536, hw)
    assert clamped == 65536
    assert warning is None


def test_clamp_context_size_clamps_an_oversized_seed_on_a_small_box():
    """#1868: the shipped 65536 seed on a box too small to afford it once the
    model weights (here a 6 GiB GGUF) are counted against the envelope."""
    hw = _cpu_only(ram_mb=8192)
    clamped, warning = clamp_context_size(65536, hw, floor_tokens=0, model_mib=6000.0)
    assert clamped < 65536
    assert warning is not None
    assert "65,536" in warning


def test_clamp_context_size_never_drops_below_the_floor():
    """A chat-capable slot must not silently clamp under Hermes' floor
    (#1827) — it keeps the floor and warns that the box may not afford it."""
    hw = _cpu_only(ram_mb=2048)
    clamped, warning = clamp_context_size(65536, hw, floor_tokens=64000, model_mib=6000.0)
    assert clamped == 64000
    assert warning is not None
    assert "floor" in warning
