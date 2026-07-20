"""Unit tests for the slot image-resolution chain (spec-hw-slot-ownership §3).

The chain collapsed to::

    image_default = RUNNER_IMAGES[slot.BINARY]     (code registry)
    effective     = slot.image_pin or image_default

Reversing the prior spec-flags-ownership §7 chain: the raw ``slot.image`` /
``[slot].image`` string reads collapsed into the typed ``image_pin`` escape
hatch, and the ``profile.image`` tier was DELETED (profiles are device-agnostic
tune templates, carrying no image). These tests pin the new contract.
"""

from __future__ import annotations

from types import SimpleNamespace

from hal0.config.schema import DEFAULT_ROCMFPX_IMAGE, FALLBACK_VULKAN_IMAGE
from hal0.providers.container import (
    _profile_image_and_flags,
    _resolve_image_ref,
)


def _profile(image: str | None = None, *, backend: str = "vulkan") -> SimpleNamespace:
    """A device-agnostic profile stand-in. ``image`` is set only to prove it is
    IGNORED — real profiles no longer carry one. ``flags``/``mtp`` are present so
    _profile_image_and_flags can resolve its (unused-here) flag string."""
    return SimpleNamespace(image=image, backend=backend, device_class="gpu", flags="", mtp=False)


# --- image_pin escape hatch (tier 1) --------------------------------------- #


def test_image_pin_is_honored_verbatim() -> None:
    """slot.image_pin overrides the BINARY-resolved default, verbatim."""
    slot_cfg = {"image_pin": "ghcr.io/foo/debug:bar", "binary": "cpu"}
    assert _resolve_image_ref(slot_cfg, _profile()) == "ghcr.io/foo/debug:bar"


def test_empty_image_pin_falls_through_to_binary() -> None:
    """An empty-string image_pin is treated as 'no pin'."""
    slot_cfg = {"image_pin": "", "binary": "cpu"}
    assert _resolve_image_ref(slot_cfg, _profile()) == FALLBACK_VULKAN_IMAGE


def test_non_string_image_pin_ignored() -> None:
    """A non-string image_pin (e.g. a stray table) never becomes str(dict)."""
    slot_cfg = {"image_pin": {"oops": 1}, "binary": "cpu"}
    assert _resolve_image_ref(slot_cfg, _profile()) == FALLBACK_VULKAN_IMAGE


# --- BINARY-resolved default (tier 2) -------------------------------------- #


def test_binary_resolves_the_image() -> None:
    """slot.binary is a RUNNER_IMAGES key → its resolved image."""
    assert _resolve_image_ref({"binary": "cpu"}, _profile()) == FALLBACK_VULKAN_IMAGE


def test_unknown_binary_falls_back_to_hw_default() -> None:
    """An unknown BINARY key is skipped (not a launch crash) → HW-gated default."""
    assert _resolve_image_ref({"binary": "does-not-exist"}, _profile()) == DEFAULT_ROCMFPX_IMAGE


def test_no_binary_uses_hw_gated_default_gpu() -> None:
    """No pin, no binary, GPU lane → the universal rocmfpx runner image."""
    profile = SimpleNamespace(image=None, backend="vulkan", device_class="gpu")
    assert _resolve_image_ref(None, profile) == DEFAULT_ROCMFPX_IMAGE


def test_no_binary_cpu_lane_uses_lean_toolbox() -> None:
    """No pin, no binary, CPU lane → the lean toolbox, not the big GPU runner."""
    profile = SimpleNamespace(image=None, backend=None, device_class="cpu")
    assert _resolve_image_ref(None, profile) == FALLBACK_VULKAN_IMAGE


# --- DELETED tiers: profile.image + raw slot.image are NOT read ------------ #


def test_profile_image_is_ignored() -> None:
    """spec §3: profile.image is DELETED from the chain — never read."""
    profile = SimpleNamespace(
        image="ghcr.io/foo/profilepin:1", backend="vulkan", device_class="gpu"
    )
    assert _resolve_image_ref(None, profile) == DEFAULT_ROCMFPX_IMAGE


def test_raw_slot_image_key_is_ignored() -> None:
    """The old top-level ``image`` string is no longer read (collapsed into
    image_pin by the migration lane)."""
    assert _resolve_image_ref({"image": "ghcr.io/foo/old:1"}, _profile()) == DEFAULT_ROCMFPX_IMAGE


def test_nested_slot_image_key_is_ignored() -> None:
    """The old nested ``[slot].image`` string is no longer read either."""
    slot_cfg = {"slot": {"image": "ghcr.io/foo/nested:1"}}
    assert _resolve_image_ref(slot_cfg, _profile()) == DEFAULT_ROCMFPX_IMAGE


def test_image_gen_dict_does_not_break_resolution() -> None:
    """The [image] image-gen table (#599) shares no key with image_pin, so it
    can never be mis-read as a ref (the prior str(dict) overload is gone)."""
    slot_cfg = {"image": {"idle_restore_minutes": 0, "default_steps": 30}, "binary": "cpu"}
    assert _resolve_image_ref(slot_cfg, _profile()) == FALLBACK_VULKAN_IMAGE


# --- _profile_image_and_flags integration ---------------------------------- #


def test_profile_image_and_flags_honours_image_pin() -> None:
    """The launch renderer's (image, flags) tuple uses the slot image_pin."""
    slot_cfg = {"image_pin": "ghcr.io/hal0ai/hal0-rocmfpx:vulkan-minicpm5"}
    image, _flags = _profile_image_and_flags(_profile(), slot_cfg=slot_cfg)
    assert image == "ghcr.io/hal0ai/hal0-rocmfpx:vulkan-minicpm5"


def test_profile_image_and_flags_default_when_nothing_set() -> None:
    """No image_pin, no binary → the HW-gated rocmfpx default."""
    profile = SimpleNamespace(
        image=None, flags="-ngl 999", mtp=False, backend="rocm", device_class="gpu"
    )
    image, _flags = _profile_image_and_flags(profile)
    assert image == DEFAULT_ROCMFPX_IMAGE
