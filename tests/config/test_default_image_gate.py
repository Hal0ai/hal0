"""HW-gated default container image (:func:`resolve_default_image`).

Covers the gfx1151/Strix-Halo gate that decides whether a slot with no explicit
image pin runs the unified ``hal0-rocmfpx`` runner or a generic toolbox image.
The gate is conservative: an absent/ambiguous probe must fall back to the lean
toolbox, never the wrong-ISA rocmfpx runner.
"""

from __future__ import annotations

import pytest

from hal0.config.schema import (
    DEFAULT_ROCMFPX_IMAGE,
    FALLBACK_CUDA_IMAGE,
    FALLBACK_ROCM_IMAGE,
    FALLBACK_VULKAN_IMAGE,
    GPUInfo,
    HardwareInfo,
    resolve_default_image,
    rocmfpx_capable,
)


def _strix_by_cpu() -> HardwareInfo:
    return HardwareInfo(cpu_model="AMD RYZEN AI MAX+ 395 w/ Radeon 8060S")


def _strix_by_gpu() -> HardwareInfo:
    return HardwareInfo(
        cpu_model="Some Generic CPU",
        gpus=[GPUInfo(name="Advanced Micro Devices Device 1586 (rev c1)", vram_mb=98304)],
    )


def _non_strix() -> HardwareInfo:
    return HardwareInfo(
        cpu_model="AMD Ryzen 9 7950X",
        gpus=[GPUInfo(name="AMD Radeon RX 7900 XTX", vram_mb=24576)],
    )


# ── rocmfpx_capable: the raw gate ─────────────────────────────────────────── #


def test_capable_by_cpu_marker() -> None:
    assert rocmfpx_capable(_strix_by_cpu()) is True


def test_capable_by_gpu_name_marker() -> None:
    assert rocmfpx_capable(_strix_by_gpu()) is True


def test_not_capable_on_non_strix() -> None:
    assert rocmfpx_capable(_non_strix()) is False


def test_not_capable_on_none() -> None:
    assert rocmfpx_capable(None) is False


def test_not_capable_on_empty_probe() -> None:
    assert rocmfpx_capable(HardwareInfo()) is False


# ── resolve_default_image: HW-gated lane selection ────────────────────────── #


@pytest.mark.parametrize("backend", ["vulkan", "rocm"])
def test_strix_gpu_lanes_get_rocmfpx(backend: str) -> None:
    assert resolve_default_image(backend, "gpu", hw=_strix_by_cpu()) == DEFAULT_ROCMFPX_IMAGE


def test_non_strix_rocm_falls_back_to_rocm_toolbox() -> None:
    assert resolve_default_image("rocm", "gpu", hw=_non_strix()) == FALLBACK_ROCM_IMAGE


def test_non_strix_vulkan_falls_back_to_vulkan_toolbox() -> None:
    assert resolve_default_image("vulkan", "gpu", hw=_non_strix()) == FALLBACK_VULKAN_IMAGE


def test_cpu_lane_never_rocmfpx_even_on_strix() -> None:
    # A cpu device_class on a Strix box must still get the lean CPU-mode image.
    assert resolve_default_image("vulkan", "cpu", hw=_strix_by_cpu()) == FALLBACK_VULKAN_IMAGE


def test_cuda_lane() -> None:
    assert resolve_default_image("cuda", "gpu", hw=_non_strix()) == FALLBACK_CUDA_IMAGE
