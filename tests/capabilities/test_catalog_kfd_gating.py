"""rocm_lane_present() decides the ROCm lane in the capability picker too
(#2216 sibling, #1966, #2354): ``available_backends``'s GPU/ROCm badge must
not depend on ``rocm-smi`` alone, ComfyUI's picker row must not survive on a
kfd-less AMD box when its image is ROCm-only, and /dev/kfd without a render
node is not a ROCm lane for either (#2313).
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


# ── available_backends: the ROCm device nodes suffice for gpu-rocm ─────────


def test_gpu_rocm_badge_appears_with_kfd_present_and_no_rocm_smi() -> None:
    """#2216 sibling: a fresh container has no rocm-smi (compute_capable
    reads False) but a forwarded /dev/kfd — the ROCm badge must still show."""
    hw = _amd_gpu_hw(compute_capable=False)
    with (
        patch("hal0.capabilities.catalog.load_hardware_info", return_value=hw),
        patch("hal0.capabilities.catalog.rocm_lane_present", return_value=True),
        patch("hal0.capabilities.catalog._flm_image_present", return_value=False),
    ):
        ids = {b["id"] for b in catalog.available_backends()}
    assert "gpu-rocm" in ids


def test_gpu_rocm_badge_absent_without_kfd_or_rocm_smi() -> None:
    hw = _amd_gpu_hw(compute_capable=False)
    with (
        patch("hal0.capabilities.catalog.load_hardware_info", return_value=hw),
        patch("hal0.capabilities.catalog.rocm_lane_present", return_value=False),
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
        patch("hal0.capabilities.catalog.rocm_lane_present", return_value=False),
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
        patch("hal0.capabilities.catalog.rocm_lane_present", return_value=True),
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
        patch("hal0.capabilities.catalog.rocm_lane_present", return_value=False),
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
        patch("hal0.capabilities.catalog.rocm_lane_present", return_value=False),
    ):
        variants = catalog._backend_variants(entry)
    assert variants == []


# ── /dev/kfd without a render node is not a ROCm lane (#2313, #2354) ────────
#
# These patch the two underlying probes, not ``rocm_lane_present`` itself, so
# the picker is exercised through the real shared predicate.

_NODES = {
    "kfd_only": (True, False),
    "both": (True, True),
    "neither": (False, False),
}


def _nodes(monkeypatch: pytest.MonkeyPatch, shape: str) -> None:
    kfd, render = _NODES[shape]
    monkeypatch.setattr("hal0.providers._gpu.kfd_present", lambda *a, **k: kfd)
    monkeypatch.setattr("hal0.providers._gpu.render_node_present", lambda *a, **k: render)


@pytest.mark.parametrize(
    ("shape", "offered"), [("kfd_only", False), ("both", True), ("neither", False)]
)
def test_gpu_rocm_badge_needs_kfd_and_a_render_node(
    monkeypatch: pytest.MonkeyPatch, shape: str, offered: bool
) -> None:
    """#2354: an LXC with /dev/kfd forwarded and no ``/dev/dri/renderD*``
    must not be offered the GPU (ROCm) row — the slot cannot open its device."""
    _nodes(monkeypatch, shape)
    hw = _amd_gpu_hw(compute_capable=False)
    with (
        patch("hal0.capabilities.catalog.load_hardware_info", return_value=hw),
        patch("hal0.capabilities.catalog._flm_image_present", return_value=False),
    ):
        ids = {b["id"] for b in catalog.available_backends()}
    assert ("gpu-rocm" in ids) is offered


@pytest.mark.parametrize(
    ("shape", "expected"), [("kfd_only", []), ("both", ["gpu-vulkan"]), ("neither", [])]
)
@pytest.mark.parametrize("entry", ["tagged", "explicit"])
def test_comfyui_row_needs_kfd_and_a_render_node(
    monkeypatch: pytest.MonkeyPatch, shape: str, expected: list[str], entry: str
) -> None:
    """ComfyUI's image is ROCm-only, so its row follows the same predicate as
    the gpu-rocm badge (and Qwen3-TTS, which rides that badge): on a kfd-only
    AMD box the generic GPU row is still advertised, so this gate is the only
    thing keeping a guaranteed-to-fail row out of the picker."""
    _nodes(monkeypatch, shape)
    row = _image_entry() if entry == "tagged" else types.SimpleNamespace(provider="comfyui")
    with (
        patch(
            "hal0.capabilities.catalog.available_backends",
            return_value=[{"id": "gpu-vulkan"}, {"id": "cpu"}],
        ),
        patch("hal0.capabilities.catalog.host_is_amd_gpu", return_value=True),
    ):
        variants = catalog._backend_variants(row)
    assert variants == expected
