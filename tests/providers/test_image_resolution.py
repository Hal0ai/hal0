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

from hal0.config.schema import DEFAULT_ROCMFPX_IMAGE
from hal0.providers.container import (
    _profile_image_and_flags,
    _resolve_image_ref,
)
from hal0.runners import get_runner, resolve_runner_image


def _cpu_image() -> str:
    """The image the ``cpu`` runner resolves to right now.

    Computed rather than hard-coded: tier 3 of ``_resolve_image_ref`` IS
    ``resolve_runner_image``, so an env override or a manifest digest pin
    (the ``cpu`` runner took one on #2126) moves both sides together and
    these tests keep asserting the tier, not a literal.
    """
    return resolve_runner_image(get_runner("cpu"))


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
    assert _resolve_image_ref(slot_cfg, _profile()) == _cpu_image()


def test_non_string_image_pin_ignored() -> None:
    """A non-string image_pin (e.g. a stray table) never becomes str(dict)."""
    slot_cfg = {"image_pin": {"oops": 1}, "binary": "cpu"}
    assert _resolve_image_ref(slot_cfg, _profile()) == _cpu_image()


# --- BINARY-resolved default (tier 2) -------------------------------------- #


def test_binary_resolves_the_image() -> None:
    """slot.binary is a RUNNER_IMAGES key → its resolved image."""
    assert _resolve_image_ref({"binary": "cpu"}, _profile()) == _cpu_image()


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
    assert _resolve_image_ref(None, profile) == _cpu_image()


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
    assert _resolve_image_ref(slot_cfg, _profile()) == _cpu_image()


# --- [slots] default_images override (runner-image-catalogue v2) ------------ #


def _fake_config(default_images: dict[str, str]):
    """A Hal0Config stand-in exposing just what the override tier reads."""
    return SimpleNamespace(slots=SimpleNamespace(default_images=default_images))


def test_default_images_override_beats_baked_default(monkeypatch) -> None:
    """No pin + [slots].default_images entry for the effective family → the
    operator override wins over the baked registry default."""
    monkeypatch.setattr(
        "hal0.config.loader.load_hal0_config",
        lambda: _fake_config({"cpu": "ghcr.io/hal0ai/hal0-combined:0824"}),
    )
    slot_cfg = {"binary": "cpu"}
    assert _resolve_image_ref(slot_cfg, _profile()) == "ghcr.io/hal0ai/hal0-combined:0824"


def test_image_pin_still_beats_default_images_override(monkeypatch) -> None:
    monkeypatch.setattr(
        "hal0.config.loader.load_hal0_config",
        lambda: _fake_config({"cpu": "ghcr.io/hal0ai/hal0-combined:0824"}),
    )
    slot_cfg = {"image_pin": "ghcr.io/foo/debug:bar", "binary": "cpu"}
    assert _resolve_image_ref(slot_cfg, _profile()) == "ghcr.io/foo/debug:bar"


def test_default_images_override_applies_to_hw_gated_family(monkeypatch) -> None:
    """No binary: the override keys on the HW-gated runner (rocmfpx here).

    The override ref is deliberately NOT the baked default (which is
    hal0-combined:0824 as of #2041) so this can only pass via the tier.
    """
    monkeypatch.setattr(
        "hal0.config.loader.load_hal0_config",
        lambda: _fake_config({"rocmfpx": "ghcr.io/hal0ai/hal0-combined:0999"}),
    )
    profile = SimpleNamespace(image=None, backend="rocm", device_class="gpu")
    assert _resolve_image_ref(None, profile) == "ghcr.io/hal0ai/hal0-combined:0999"


def test_default_images_absent_family_keeps_baked_default(monkeypatch) -> None:
    """An override for a DIFFERENT family never leaks across families."""
    monkeypatch.setattr(
        "hal0.config.loader.load_hal0_config",
        lambda: _fake_config({"rocmfpx": "ghcr.io/hal0ai/hal0-combined:0824"}),
    )
    assert _resolve_image_ref({"binary": "cpu"}, _profile()) == _cpu_image()


def test_default_images_config_load_failure_fails_soft(monkeypatch) -> None:
    """A broken hal0.toml must not wedge a slot launch — baked default wins."""

    def _boom() -> None:
        raise OSError("permission denied")

    monkeypatch.setattr("hal0.config.loader.load_hal0_config", _boom)
    assert _resolve_image_ref({"binary": "cpu"}, _profile()) == _cpu_image()


def test_default_images_canonical_key_applies_when_effective_runner_is_alias(
    monkeypatch,
) -> None:
    """runner-image-catalogue v3, task 11: a ``[slots].default_images`` map
    with only the canonical ``rocmfpx`` key still applies when the effective
    runner is the ``vulkanfpx`` alias (they share DEFAULT_ROCMFPX_IMAGE, so
    one lever governs both)."""
    monkeypatch.setattr(
        "hal0.config.loader.load_hal0_config",
        lambda: _fake_config({"rocmfpx": "ghcr.io/hal0ai/hal0-combined:0999"}),
    )
    slot_cfg = {"binary": "vulkanfpx"}
    assert _resolve_image_ref(slot_cfg, _profile()) == "ghcr.io/hal0ai/hal0-combined:0999"


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


# --- the CPU lane resolves to a real CPU image (#2126) ---------------------- #


def test_cpu_lane_resolves_to_the_cpu_toolbox_not_the_gpu_fallback() -> None:
    """#2126: a correctly-derived ``device = "cpu"`` slot used to resolve to
    ``FALLBACK_VULKAN_IMAGE`` — the GPU toolbox — because that is what the
    ``cpu`` runner carried, and on a GPU-less host that image SIGILLs about a
    second into model load. It now resolves to hal0-toolbox-cpu, which is
    built ``GGML_NATIVE=OFF`` and therefore portable.

    Asserted against the constants rather than the resolved string so the test
    survives the digest pin (which makes the resolved ref a ``@sha256:`` form
    in an install that can read manifest.json, and the bare tag otherwise).
    """
    from hal0.config.schema import FALLBACK_VULKAN_IMAGE
    from hal0.runners import RUNNER_IMAGES

    runner = RUNNER_IMAGES["cpu"]
    assert runner.image != FALLBACK_VULKAN_IMAGE
    assert runner.image == "ghcr.io/hal0ai/hal0-toolbox-cpu:v1"
    # …and it can take a digest pin like every other runner, which the
    # placeholder deliberately could not.
    assert runner.manifest_key == "cpu"
    assert _resolve_image_ref({"binary": "cpu"}, _profile()) == _cpu_image()


def test_cpu_lane_predicate_passes_on_a_shipped_build() -> None:
    """:func:`cpu_lane_has_runner_image` is the installer gate's predicate. On
    a shipped build it answers True; it exists as the regression guard for a
    revert to the GPU fallback, not as a description of a broken state."""
    from hal0.runners import cpu_lane_has_runner_image

    assert cpu_lane_has_runner_image() is True


def test_cpu_lane_predicate_fails_if_the_runner_reverts_to_the_gpu_fallback(
    monkeypatch,
) -> None:
    """The #2126 world, reconstructed: point the ``cpu`` runner back at the
    Vulkan toolbox and the predicate must say so, so the installer refuses
    instead of shipping a box that crash-loops."""
    from dataclasses import replace

    from hal0.config.schema import FALLBACK_VULKAN_IMAGE
    from hal0.runners import RUNNER_IMAGES, cpu_lane_has_runner_image

    reverted = replace(RUNNER_IMAGES["cpu"], image=FALLBACK_VULKAN_IMAGE, manifest_key=None)
    monkeypatch.setitem(RUNNER_IMAGES, "cpu", reverted)
    assert cpu_lane_has_runner_image() is False


def test_cpu_lane_env_override_still_satisfies_the_predicate(monkeypatch) -> None:
    """Tier 1 of ``resolve_runner_image``, and the escape hatch the installer
    gate leaves open for an operator running their own CPU llama-server build.
    It has to keep working even from the reverted world, so the predicate must
    RESOLVE rather than read the registry literal."""
    from dataclasses import replace

    from hal0.config.schema import FALLBACK_VULKAN_IMAGE
    from hal0.runners import RUNNER_IMAGES, cpu_lane_has_runner_image

    reverted = replace(RUNNER_IMAGES["cpu"], image=FALLBACK_VULKAN_IMAGE, manifest_key=None)
    monkeypatch.setitem(RUNNER_IMAGES, "cpu", reverted)
    monkeypatch.setenv("HAL0_TOOLBOX_IMAGE_CPU", "ghcr.io/example/llama-cpu:v1")
    assert cpu_lane_has_runner_image() is True


def test_cpu_lane_predicate_ignores_a_slot_level_image_pin(monkeypatch) -> None:
    """``image_pin`` fixes ONE slot; the predicate is about the LANE, which is
    what an install-time gate can reason about (no slots exist yet). Driven
    from the reverted world so the pin has something to fail to rescue."""
    from dataclasses import replace

    from hal0.config.schema import FALLBACK_VULKAN_IMAGE
    from hal0.runners import RUNNER_IMAGES, cpu_lane_has_runner_image

    reverted = replace(RUNNER_IMAGES["cpu"], image=FALLBACK_VULKAN_IMAGE, manifest_key=None)
    monkeypatch.setitem(RUNNER_IMAGES, "cpu", reverted)
    pinned = {"image_pin": "ghcr.io/example/llama-cpu:v1", "binary": "cpu"}
    assert _resolve_image_ref(pinned, _profile()) == "ghcr.io/example/llama-cpu:v1"
    assert cpu_lane_has_runner_image() is False
