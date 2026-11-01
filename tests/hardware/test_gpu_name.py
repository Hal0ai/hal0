"""Tests for hal0.hardware.gpu_name — the one home of the GPU display name (#3156).

The probe's ``GPUInfo.name`` is whatever the detector saw: an ``lspci`` line on
AMD ("Advanced Micro Devices, Inc. [AMD/ATI] Device 1586 (rev c1)" when the
box's pci.ids predates the part), the ``nvidia-smi`` product name on NVIDIA.
``gpu_display_name`` turns it into a short label for the operator.
"""

from __future__ import annotations

import pytest

from hal0.hardware.gpu_name import gpu_display_name

STRIX_HALO_PCI = "Advanced Micro Devices, Inc. [AMD/ATI] Device 1586 (rev c1)"
STRIX_HALO_CPU = "AMD RYZEN AI MAX+ 395 w/ Radeon 8060S"


def test_unresolved_amd_pci_string_takes_the_apu_marketing_name_from_the_cpu() -> None:
    assert gpu_display_name(STRIX_HALO_PCI, cpu_model=STRIX_HALO_CPU) == "Radeon 8060S"


def test_unresolved_amd_pci_string_without_a_radeon_cpu_is_a_short_pci_label() -> None:
    # No marketing name to be had: say what is known, without the vendor
    # boilerplate and the revision that truncate a card caption.
    assert gpu_display_name(STRIX_HALO_PCI, cpu_model="AMD EPYC 7763") == "AMD Device 1586"
    assert gpu_display_name(STRIX_HALO_PCI) == "AMD Device 1586"


def test_resolved_discrete_card_takes_the_bracketed_marketing_name() -> None:
    raw = (
        "Advanced Micro Devices, Inc. [AMD/ATI] Navi 31 [Radeon RX 7900 XT/7900 XTX/7900M] (rev c8)"
    )
    assert gpu_display_name(raw, cpu_model="AMD Ryzen 9 7950X 16-Core Processor") == (
        "Radeon RX 7900 XT/7900 XTX/7900M"
    )


def test_multi_sku_pci_name_defers_to_the_cpu_when_it_names_the_igpu() -> None:
    # Newer pci.ids list Strix Halo as three SKUs; the CPU string says which.
    raw = (
        "Advanced Micro Devices, Inc. [AMD/ATI] Strix Halo "
        "[Radeon Graphics / Radeon 8050S / Radeon 8060S] (rev c1)"
    )
    assert gpu_display_name(raw, cpu_model=STRIX_HALO_CPU) == "Radeon 8060S"
    assert gpu_display_name(raw, cpu_model="AMD EPYC 7763") == (
        "Radeon Graphics / Radeon 8050S / Radeon 8060S"
    )


def test_resolved_amd_name_without_a_radeon_bracket_is_the_cleaned_pci_name() -> None:
    raw = "Advanced Micro Devices, Inc. [AMD/ATI] Cezanne (rev c9)"
    assert gpu_display_name(raw) == "Cezanne"


@pytest.mark.parametrize(
    "name",
    [
        "NVIDIA GeForce RTX 4080",  # nvidia-smi product name
        "Radeon 8060S",  # already a display name
        "AMD GPU",  # the probe's own placeholder
        "Red Hat, Inc. Virtio 1.0 GPU",  # a VM's virtio GPU
    ],
)
def test_a_name_that_is_not_an_lspci_amd_line_passes_through_unchanged(name: str) -> None:
    assert gpu_display_name(name, cpu_model=STRIX_HALO_CPU) == name


@pytest.mark.parametrize("name", ["", "   ", None])
def test_no_name_is_an_empty_label(name: str | None) -> None:
    assert gpu_display_name(name, cpu_model=STRIX_HALO_CPU) == ""
