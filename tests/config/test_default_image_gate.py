"""Default container image resolver (:func:`resolve_default_image`).

``hal0-rocmfpx`` is the universal AMD-GPU default — its Mesa/RADV Vulkan backend
runs on any AMD GPU, so it is NOT gated on Strix-Halo. Only CUDA and CPU-only
lanes get their own leaner images. The resolver is deterministic and probe-free.
"""

from __future__ import annotations

import pytest

from hal0.config.schema import (
    DEFAULT_ROCMFPX_IMAGE,
    FALLBACK_CUDA_IMAGE,
    FALLBACK_VULKAN_IMAGE,
    resolve_default_image,
)


@pytest.mark.parametrize("backend", ["rocm", "vulkan"])
def test_amd_gpu_lanes_get_rocmfpx(backend: str) -> None:
    assert resolve_default_image(backend, "gpu") == DEFAULT_ROCMFPX_IMAGE


def test_unspecified_lane_defaults_to_rocmfpx() -> None:
    # GPU-first platform: an unknown/empty lane defaults to the rocmfpx runner.
    assert resolve_default_image("", "gpu") == DEFAULT_ROCMFPX_IMAGE
    assert resolve_default_image(None, None) == DEFAULT_ROCMFPX_IMAGE


def test_cuda_lane_gets_cuda_image() -> None:
    assert resolve_default_image("cuda", "gpu") == FALLBACK_CUDA_IMAGE


def test_cpu_lane_gets_lean_toolbox() -> None:
    # A 7.5 GB ROCm image is pointless for CPU-only; use the lean toolbox.
    assert resolve_default_image("vulkan", "cpu") == FALLBACK_VULKAN_IMAGE
    assert resolve_default_image("cpu", None) == FALLBACK_VULKAN_IMAGE
