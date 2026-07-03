"""Parity/regression lock for the device→profile derivations (finding PS-4).

The platform review found three parallel ``(capability, device) → profile``
derivations that drifted apart across #834's churn:

  1. :func:`hal0.install.profile_derive.derive_profile` — the install-flavoured,
     MTP-preferring resolver (gpu-rocm + dense chat/coder → ``rocm-dnse``).
  2. :data:`hal0.config.schema.DEVICE_DEFAULT_PROFILES` — the canonical, plain
     device-class-representative base table (never MTP).
  3. The verbatim-twin ``_profile_for_fit`` blocks in ``capabilities/catalog.py``
     and ``capabilities/orchestrator.py`` (now both routed through the shared
     :func:`hal0.capabilities.profile_fit.profile_name_for_fit`).

The consolidation in PS-4 is a literal-dedup + shared-helper refactor with ZERO
behaviour change. These tests pin the CURRENT matrix so a future unification
cannot silently drift — critically they lock in the deliberate split between the
MTP-preferring install path and the non-MTP picker/reconcile path (the naive
three-way merge the finding proposed would break this).
"""

from __future__ import annotations

import pytest

from hal0.capabilities.catalog import _profile_for_fit as catalog_profile_for_fit
from hal0.capabilities.orchestrator import CapabilityOrchestrator
from hal0.capabilities.profile_fit import profile_name_for_fit
from hal0.config.schema import DEVICE_DEFAULT_PROFILES
from hal0.install.profile_derive import derive_profile

# The exact CURRENT (capability, device) → derive_profile matrix. Any change
# here is a behaviour change and must be a deliberate, reviewed edit.
_CAPABILITIES = ("chat", "coder", "embed", "utility", "tts", "agent", "image")
_DEVICES = ("gpu-rocm", "gpu-vulkan", "cpu", "npu")

# derive_profile (install path): MTP-preferring on ROCm for dense chat/coder.
_DERIVE_MATRIX: dict[tuple[str, str], str] = {
    # gpu-rocm — dense chat/coder get MTP rocm-dnse; everything else plain rocm.
    ("chat", "gpu-rocm"): "rocm-dnse",
    ("coder", "gpu-rocm"): "rocm-dnse",
    ("embed", "gpu-rocm"): "rocm",
    ("utility", "gpu-rocm"): "rocm",
    ("tts", "gpu-rocm"): "rocm",
    ("agent", "gpu-rocm"): "rocm",
    ("image", "gpu-rocm"): "rocm",
    # gpu-vulkan — always the vulkan profile.
    ("chat", "gpu-vulkan"): "vulkan",
    ("coder", "gpu-vulkan"): "vulkan",
    ("embed", "gpu-vulkan"): "vulkan",
    ("utility", "gpu-vulkan"): "vulkan",
    ("tts", "gpu-vulkan"): "vulkan",
    ("agent", "gpu-vulkan"): "vulkan",
    ("image", "gpu-vulkan"): "vulkan",
    # cpu — tts stays on kokoro; everything else on the CPU llama profile
    # (the Wave-1 cpu → "cpu-llm" fix for #807/#834).
    ("chat", "cpu"): "cpu-llm",
    ("coder", "cpu"): "cpu-llm",
    ("embed", "cpu"): "cpu-llm",
    ("utility", "cpu"): "cpu-llm",
    ("tts", "cpu"): "tts",
    ("agent", "cpu"): "cpu-llm",
    ("image", "cpu"): "cpu-llm",
    # npu — always flm.
    ("chat", "npu"): "flm",
    ("coder", "npu"): "flm",
    ("embed", "npu"): "flm",
    ("utility", "npu"): "flm",
    ("tts", "npu"): "flm",
    ("agent", "npu"): "flm",
    ("image", "npu"): "flm",
}


@pytest.mark.parametrize(("capability", "device"), _DERIVE_MATRIX.keys())
def test_derive_profile_matrix_is_pinned(capability: str, device: str) -> None:
    """derive_profile locks the exact current output for every cap x device."""
    assert derive_profile(capability, device) == _DERIVE_MATRIX[(capability, device)]


def test_derive_profile_rocm_prefers_mtp_for_dense_chat_coder() -> None:
    # The one genuine install-path specialisation on ROCm.
    assert derive_profile("chat", "gpu-rocm") == "rocm-dnse"
    assert derive_profile("coder", "gpu-rocm") == "rocm-dnse"
    assert derive_profile("embed", "gpu-rocm") == "rocm"


def test_derive_profile_cpu_tts_vs_non_tts() -> None:
    # The one genuine install-path specialisation on CPU (Wave-1 cpu-llm fix).
    assert derive_profile("tts", "cpu") == "tts"
    assert derive_profile("chat", "cpu") == "cpu-llm"


def test_device_default_profiles_table_is_pinned() -> None:
    """Guards the Wave-1 cpu → "cpu-llm" fix and the canonical base table."""
    assert DEVICE_DEFAULT_PROFILES == {
        "gpu-rocm": "rocm",
        "gpu-vulkan": "vulkan",
        "cpu": "cpu-llm",
        "npu": "flm",
    }


@pytest.mark.parametrize("capability", _CAPABILITIES)
@pytest.mark.parametrize("device", _DEVICES)
def test_profile_for_fit_twin_parity(capability: str, device: str, tmp_hal0_home: str) -> None:
    """catalog._profile_for_fit and orchestrator._profile_for_fit resolve to
    the SAME profile name for every cap x device.

    Proves the two blocks are currently identical AND prevents the extracted
    shared helper from regressing only one copy. ``self`` is unused by the
    orchestrator method, so it is invoked unbound with ``None``.
    """
    cat = catalog_profile_for_fit(capability, device)
    orch = CapabilityOrchestrator._profile_for_fit(None, capability, device)
    cat_name = cat.name if cat is not None else None
    orch_name = orch.name if orch is not None else None
    assert cat_name == orch_name


@pytest.mark.parametrize("capability", _CAPABILITIES)
@pytest.mark.parametrize("device", _DEVICES)
def test_profile_for_fit_matches_shared_helper(
    capability: str, device: str, tmp_hal0_home: str
) -> None:
    """The resolved catalog profile name equals the shared helper's name."""
    cat = catalog_profile_for_fit(capability, device)
    expected = profile_name_for_fit(capability, device)
    cat_name = cat.name if cat is not None else None
    assert cat_name == expected


def test_fit_helper_is_non_mtp_on_rocm() -> None:
    """The picker/apply fit path NEVER prefers the MTP rocm-dnse image —
    that is the install path's job only. This is the guard the naive
    three-way merge would break.
    """
    assert profile_name_for_fit("chat", "gpu-rocm") == "rocm"
    assert profile_name_for_fit("coder", "gpu-rocm") == "rocm"
    assert profile_name_for_fit("embed", "gpu-rocm") == "rocm"
    assert profile_name_for_fit("chat", "gpu-vulkan") == "vulkan"


def test_base_profile_for_backend_is_non_mtp(tmp_hal0_home: str) -> None:
    """_base_profile_for_backend answers the backend→non-MTP-base question so a
    drawer device-flip never silently switches a slot onto the MTP image.
    """
    from hal0.config.loader import load_profiles_config
    from hal0.slots.manager import _base_profile_for_backend

    catalog = load_profiles_config()
    assert _base_profile_for_backend(catalog, "rocm") == "rocm"
    assert _base_profile_for_backend(catalog, "vulkan") == "vulkan"


def test_reconcile_device_flip_stays_non_mtp(tmp_hal0_home: str) -> None:
    """Flipping a chat slot gpu-rocm → gpu-vulkan yields "vulkan", never the
    MTP "rocm-dnse" — locks in the deliberate non-MTP reconcile semantics.
    """
    from hal0.slots.manager import _reconcile_device_profile

    # Slot was on the rocm-dnse (MTP) profile; operator flips the device only.
    cfg_dict: dict[str, object] = {"device": "gpu-vulkan", "profile": "rocm-dnse"}
    _reconcile_device_profile(cfg_dict, changed={"device"})
    assert cfg_dict["profile"] == "vulkan"
    assert cfg_dict["profile"] != "rocm-dnse"
