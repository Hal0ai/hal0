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
from hal0.runners import get_runner, resolve_runner_image


@pytest.mark.parametrize("backend", ["rocm", "vulkan"])
def test_amd_gpu_lanes_get_rocmfpx(backend: str) -> None:
    assert resolve_default_image(backend, "gpu") == DEFAULT_ROCMFPX_IMAGE


def test_unspecified_lane_defaults_to_rocmfpx() -> None:
    # GPU-first platform: an unknown/empty lane defaults to the rocmfpx runner.
    assert resolve_default_image("", "gpu") == DEFAULT_ROCMFPX_IMAGE
    assert resolve_default_image(None, None) == DEFAULT_ROCMFPX_IMAGE


def test_cuda_lane_gets_cuda_image() -> None:
    assert resolve_default_image("cuda", "gpu") == FALLBACK_CUDA_IMAGE


def test_cpu_lane_gets_the_cpu_toolbox() -> None:
    """A 7.5 GB ROCm image is pointless for CPU-only, so this lane has always
    had its own leaner image — but until #2126 that image was
    :data:`FALLBACK_VULKAN_IMAGE`, a GPU build, which SIGILLs at model load on
    a box with no GPU. It now resolves the ``cpu`` runner's own image."""
    cpu_image = resolve_runner_image(get_runner("cpu"))
    assert resolve_default_image("vulkan", "cpu") == cpu_image
    assert resolve_default_image("cpu", None) == cpu_image
    assert cpu_image != FALLBACK_VULKAN_IMAGE
