"""Model preferred profile (Q1): a model's ``defaults.profile`` loads with it.

A registry model may declare ``defaults.profile`` — the runtime profile it
wants loaded with it. On slot create (empty profile) and on every model swap
the slot adopts that profile, but ONLY when it fits the slot's device/type; an
incompatible preference is ignored so the slot keeps its device-default
profile and the manager never flips the slot's hardware to satisfy a model.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.registry.model import Model, ModelDefaults
from hal0.registry.store import ModelRegistry
from hal0.slots.manager import SlotManager


def _register(model_id: str, *, profile: str | None) -> None:
    ModelRegistry().add(
        Model(
            id=model_id,
            path=f"/tmp/{model_id}.gguf",
            capabilities=["chat"],
            defaults=ModelDefaults(profile=profile),
        )
    )


def _gpu_vulkan_cfg(name: str, model: str) -> dict:
    # No ``profile`` key — the create path fills it from the model preference.
    return {
        "name": name,
        "port": 8091,
        "type": "llm",
        "device": "gpu-vulkan",
        "provider": "llama-server",
        "group": "custom",
        "model": {"default": model},
    }


async def test_create_adopts_compatible_preferred_profile(tmp_hal0_home: str) -> None:
    _register("vk-model", profile="chat")
    sm = SlotManager()
    await sm.create("g", _gpu_vulkan_cfg("g", "vk-model"))
    cfg = await sm.get_config("g")
    assert cfg["profile"] == "chat"


async def test_create_adopts_device_agnostic_preferred_profile(tmp_hal0_home: str) -> None:
    # 1.0 seeds are device-agnostic logical recipes; the slot keeps its device.
    _register("cpu-pref", profile="cpu-chat")
    sm = SlotManager()
    await sm.create("g", _gpu_vulkan_cfg("g", "cpu-pref"))
    cfg = await sm.get_config("g")
    assert cfg.get("profile") == "cpu-chat"


async def test_create_ignores_cross_backend_preferred_profile(tmp_hal0_home: str) -> None:
    # chat is device_class=gpu, device-agnostic — a vulkan slot ADOPTS it
    _register("chat-pref", profile="chat")
    sm = SlotManager()
    await sm.create("g", _gpu_vulkan_cfg("g", "chat-pref"))
    cfg = await sm.get_config("g")
    assert cfg.get("profile") == "chat"


async def test_apply_preferred_profile_swaps_when_compatible(tmp_hal0_home: str) -> None:
    # Slot starts on the chat profile; swapping in a model that prefers a
    # different COMPATIBLE profile re-points the slot (the swap path uses this
    # before the reload). Here both fit gpu-vulkan, so the change is applied.
    _register("v2", profile="dense")
    sm = SlotManager()
    cfg0 = _gpu_vulkan_cfg("g", "v2")
    cfg0["profile"] = "chat"
    await sm.create("g", cfg0)
    changed = await sm._apply_preferred_profile("g", "v2")
    assert changed is True
    assert (await sm.get_config("g"))["profile"] == "dense"


async def test_apply_preferred_profile_adopts_device_agnostic_seed(tmp_hal0_home: str) -> None:
    _register("cpu-chat-pref", profile="cpu-chat")
    sm = SlotManager()
    cfg = _gpu_vulkan_cfg("g", "cpu-chat-pref")
    cfg["profile"] = "chat"
    await sm.create("g", cfg)
    changed = await sm._apply_preferred_profile("g", "cpu-chat-pref")
    assert changed is True
    assert (await sm.get_config("g"))["profile"] == "cpu-chat"


def test_profile_fits_slot_matrix(tmp_hal0_home: str) -> None:
    fits = SlotManager._profile_fits_slot
    gpu_vulkan = {"type": "llm", "device": "gpu-vulkan"}
    assert fits("chat", gpu_vulkan) is True
    assert fits("chat", {"type": "llm", "device": "gpu-rocm"}) is True
    assert fits("cpu-chat", gpu_vulkan) is True  # seed is device-agnostic
    assert fits("comfyui", gpu_vulkan) is False  # img profile, wrong type+class
    assert fits("does-not-exist", gpu_vulkan) is False


@pytest.fixture
def profile_catalog_fixture(tmp_hal0_home: str) -> str:
    """Add profile "pf" — runner="promptforge" (rocm) — to the catalog.

    ``load_profiles_config``/``ProfileCatalog`` read ``profiles.toml`` off
    ``paths.profiles_toml()`` (``$HAL0_HOME/etc/hal0/profiles.toml``), which
    ``tmp_hal0_home`` already isolates — writing the file there is enough for
    every catalog reader in this process to pick "pf" up. ``backend="rocm"``
    puts "pf" at odds with a ``gpu-vulkan`` slot's backend so the two new
    tests actually exercise the runner-carrying-profile veto relief (Task 6)
    rather than passing on the pre-existing device-agnostic (backend=None)
    fast path.
    """
    etc_dir = Path(tmp_hal0_home) / "etc" / "hal0"
    etc_dir.mkdir(parents=True, exist_ok=True)
    (etc_dir / "profiles.toml").write_text('[profile.pf]\nrunner = "promptforge"\nbackend = "rocm"\n')
    return tmp_hal0_home


def test_runner_profile_fits_across_gpu_lanes(profile_catalog_fixture) -> None:
    # profile "pf" carries runner="promptforge" (rocm); slot is gpu-vulkan.
    # Same device class → fits (the config-write reconcile flips the lane).
    assert SlotManager._profile_fits_slot("pf", {"type": "llm", "device": "gpu-vulkan"})


def test_runner_profile_still_vetoed_cross_class(profile_catalog_fixture) -> None:
    # gpu runtime on a cpu slot stays a veto — crossing device class is a
    # re-create, not an adoption.
    assert not SlotManager._profile_fits_slot("pf", {"type": "llm", "device": "cpu"})
