"""kfd_present() decides the ROCm lane in the capability picker too (#2216
sibling, #1966): ``available_backends``'s GPU/ROCm badge must not depend on
``rocm-smi`` alone, and ComfyUI's picker row must not survive on a kfd-less
AMD box when its image is ROCm-only.
"""

from __future__ import annotations

import types
from typing import Any
from unittest.mock import patch

import pytest

from hal0.capabilities import catalog


def _amd_gpu_hw(*, compute_capable: bool, vulkan_capable: bool = True) -> Any:
    gpu = types.SimpleNamespace(
        vendor="amd",
        compute_capable=compute_capable,
        vulkan_capable=vulkan_capable,
    )
    return types.SimpleNamespace(npu=types.SimpleNamespace(present=False), gpus=[gpu])


# ── available_backends: kfd_present() alone is sufficient for gpu-rocm ──────


def test_gpu_rocm_badge_appears_with_kfd_present_and_no_rocm_smi() -> None:
    """#2216 sibling: a fresh container has no rocm-smi (compute_capable
    reads False) but a forwarded /dev/kfd — the ROCm badge must still show."""
    hw = _amd_gpu_hw(compute_capable=False)
    with (
        patch("hal0.capabilities.catalog.load_hardware_info", return_value=hw),
        patch("hal0.capabilities.catalog.kfd_present", return_value=True),
        patch("hal0.capabilities.catalog._flm_image_present", return_value=False),
    ):
        ids = {b["id"] for b in catalog.available_backends()}
    assert "gpu-rocm" in ids


def test_gpu_rocm_badge_absent_without_kfd_or_rocm_smi() -> None:
    hw = _amd_gpu_hw(compute_capable=False)
    with (
        patch("hal0.capabilities.catalog.load_hardware_info", return_value=hw),
        patch("hal0.capabilities.catalog.kfd_present", return_value=False),
        patch("hal0.capabilities.catalog._flm_image_present", return_value=False),
    ):
        ids = {b["id"] for b in catalog.available_backends()}
    assert "gpu-rocm" not in ids
    assert "gpu-vulkan" in ids  # the render node is still real


# ── ComfyUI picker row suppressed on a kfd-less AMD box (#1966) ─────────────


def _image_entry() -> Any:
    """Shaped like a curated image-capability row (no explicit .provider)."""
    return types.SimpleNamespace(capability="image", comfyui_subdir="checkpoints")


def test_comfyui_row_suppressed_when_kfd_absent_on_amd_host() -> None:
    with (
        patch(
            "hal0.capabilities.catalog.available_backends",
            return_value=[{"id": "gpu-vulkan"}, {"id": "cpu"}],
        ),
        patch("hal0.capabilities.catalog.host_is_amd_gpu", return_value=True),
        patch("hal0.capabilities.catalog.kfd_present", return_value=False),
    ):
        variants = catalog._backend_variants(_image_entry())
    assert variants == []


def test_comfyui_row_offered_when_kfd_present_on_amd_host() -> None:
    with (
        patch(
            "hal0.capabilities.catalog.available_backends",
            return_value=[{"id": "gpu-vulkan"}, {"id": "gpu-rocm"}, {"id": "cpu"}],
        ),
        patch("hal0.capabilities.catalog.host_is_amd_gpu", return_value=True),
        patch("hal0.capabilities.catalog.kfd_present", return_value=True),
    ):
        variants = catalog._backend_variants(_image_entry())
    assert variants == ["gpu-vulkan"]


def test_comfyui_row_unaffected_on_non_amd_host() -> None:
    """The kfd gate is AMD-specific — a non-AMD box's Vulkan row (NVIDIA,
    Intel) is untouched; it was never the ROCm-mislabelled shape."""
    with (
        patch(
            "hal0.capabilities.catalog.available_backends",
            return_value=[{"id": "gpu-vulkan"}, {"id": "cpu"}],
        ),
        patch("hal0.capabilities.catalog.host_is_amd_gpu", return_value=False),
        patch("hal0.capabilities.catalog.kfd_present", return_value=False),
    ):
        variants = catalog._backend_variants(_image_entry())
    assert variants == ["gpu-vulkan"]


def test_explicit_comfyui_provider_row_also_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    """The twin path (an explicit ``entry.provider == 'comfyui'`` row, e.g. a
    registry entry an operator tagged directly) gets the same guard."""
    entry = types.SimpleNamespace(provider="comfyui")
    with (
        patch(
            "hal0.capabilities.catalog.available_backends",
            return_value=[{"id": "gpu-vulkan"}, {"id": "cpu"}],
        ),
        patch("hal0.capabilities.catalog.host_is_amd_gpu", return_value=True),
        patch("hal0.capabilities.catalog.kfd_present", return_value=False),
    ):
        variants = catalog._backend_variants(entry)
    assert variants == []
