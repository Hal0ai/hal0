"""Unit tests for the slot.image override + DEFAULT_ROCMFPX_IMAGE chain.

Covers the v0.9.5 image-control refactor: image resolution walks
``slot.image`` -> ``profile.image`` -> ``DEFAULT_ROCMFPX_IMAGE`` (in that
order), and the slot-level override always wins.  These tests pin the
contract operators rely on after the refactor.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from hal0.config.schema import DEFAULT_ROCMFPX_IMAGE, ProfileConfig
from hal0.providers.container import (
    _profile_image_and_flags,
    _resolve_image_ref,
)


def _profile(image: str | None = None) -> ProfileConfig:
    """Build a minimal ProfileConfig (or pass image=None to drop the field)."""
    raw: dict[str, object] = {
        "image": image if image is not None else "",
        "flags": "-ngl 999 --jinja",
        "mtp": False,
        "device_class": "gpu",
    }
    if image is None:
        raw.pop("image", None)
    return ProfileConfig.model_validate(raw)


# --- _resolve_image_ref: pure priority chain ------------------------------ #


def test_resolve_image_ref_slot_top_level_string_wins() -> None:
    """slot.image (top-level string) overrides everything else."""
    profile = _profile("ghcr.io/hal0ai/hal0-rocmfpx:server")
    slot_cfg = {"image": "ghcr.io/foo/override:bar"}
    assert _resolve_image_ref(slot_cfg, profile) == "ghcr.io/foo/override:bar"


def test_resolve_image_ref_slot_nested_section_wins() -> None:
    """slot.slot.image (nested [slot] table) also counts as override."""
    profile = _profile("ghcr.io/hal0ai/hal0-rocmfpx:server")
    slot_cfg = {"slot": {"image": "ghcr.io/foo/nested:baz"}}
    assert _resolve_image_ref(slot_cfg, profile) == "ghcr.io/foo/nested:baz"


def test_resolve_image_ref_falls_back_to_profile_image() -> None:
    """No slot override -> profile.image is used (Phase 1 back-compat)."""
    profile = _profile("ghcr.io/hal0ai/hal0-rocmfpx:server")
    assert _resolve_image_ref(None, profile) == "ghcr.io/hal0ai/hal0-rocmfpx:server"


def test_resolve_image_ref_falls_back_to_default_when_profile_has_no_image() -> None:
    """No slot override AND no profile.image -> DEFAULT_ROCMFPX_IMAGE."""
    profile = SimpleNamespace(image=None)
    assert _resolve_image_ref(None, profile) == DEFAULT_ROCMFPX_IMAGE


def test_resolve_image_ref_ignores_image_gen_dict() -> None:
    """[image] TOML table (#599 image-gen settings) must NOT be treated as
    a container-image ref. Treating that dict as a ref renders str(dict)
    into ExecStart and podman fails with 'invalid reference format'.
    """
    profile = _profile("ghcr.io/hal0ai/hal0-rocmfpx:server")
    slot_cfg = {"image": {"idle_restore_minutes": 0, "default_steps": 30}}
    assert _resolve_image_ref(slot_cfg, profile) == "ghcr.io/hal0ai/hal0-rocmfpx:server"


def test_resolve_image_ref_empty_string_falls_through() -> None:
    """Empty string slot override is treated as 'no override'."""
    profile = _profile("ghcr.io/hal0ai/hal0-rocmfpx:server")
    assert _resolve_image_ref({"image": ""}, profile) == "ghcr.io/hal0ai/hal0-rocmfpx:server"


# --- _profile_image_and_flags: integration with the slot --------------------- #


def test_profile_image_and_flags_honours_slot_override() -> None:
    """Direct test of the function called by the launch renderer.

    Pins the user-visible behaviour: editing a slot's image in the slot
    TOML causes the resolved (image, flags) tuple to use that image, NOT
    the profile's image. This is the answer to 'with your plan, will the
    image field in slot edit overrule the profile?' -> YES.
    """
    profile = _profile("ghcr.io/hal0ai/hal0-rocmfpx:server")
    slot_cfg = {"image": "ghcr.io/hal0ai/hal0-rocmfpx:vulkan-minicpm5"}
    image, _flags = _profile_image_and_flags(profile, slot_cfg=slot_cfg)
    assert image == "ghcr.io/hal0ai/hal0-rocmfpx:vulkan-minicpm5"


def test_profile_image_and_flags_default_when_nothing_set() -> None:
    """No slot image, profile has no image -> DEFAULT_ROCMFPX_IMAGE."""
    profile = SimpleNamespace(image=None, flags="-ngl 999", mtp=False)
    image, _flags = _profile_image_and_flags(profile)
    assert image == DEFAULT_ROCMFPX_IMAGE


@pytest.mark.parametrize(
    "seed_name,expected_image",
    [
        # The 2x2 (backend x {dense,moe}) ROCmFPX grid consolidated in 0.9.5.
        # c077206 became the default runner in #1173 (hal0-bench in-tree).
        ("rocm-dense", "ghcr.io/hal0ai/hal0-rocmfpx:c077206"),
        ("rocm-moe", "ghcr.io/hal0ai/hal0-rocmfpx:c077206"),
        ("vulkan-dense", "ghcr.io/hal0ai/hal0-rocmfpx:c077206"),
        ("vulkan-moe", "ghcr.io/hal0ai/hal0-rocmfpx:c077206"),
    ],
)
def test_seed_profiles_image_pinned_to_c077206(seed_name: str, expected_image: str) -> None:
    """The ROCmFPX runner seed profiles use the current default image.

    Pins the rollout: every fresh install gets the c077206 ROCmFPX
    runner on its first launch unless the operator pins a different
    image in their slot TOML.
    """
    from hal0.config.schema import SEED_PROFILES

    assert SEED_PROFILES[seed_name]["image"] == expected_image
    assert expected_image == DEFAULT_ROCMFPX_IMAGE
