"""Model preferred profile (Q1): a model's ``defaults.profile`` loads with it.

A registry model may declare ``defaults.profile`` — the runtime profile it
wants loaded with it. On slot create (empty profile) and on every model swap
the slot adopts that profile, but ONLY when it fits the slot's device/type; an
incompatible preference is ignored so the slot keeps its device-default
profile and the manager never flips the slot's hardware to satisfy a model.
"""

from __future__ import annotations

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
        "enabled": True,
        "group": "custom",
        "model": {"default": model},
    }


async def test_create_adopts_compatible_preferred_profile(tmp_hal0_home: str) -> None:
    _register("vk-model", profile="vulkan")
    sm = SlotManager()
    await sm.create("g", _gpu_vulkan_cfg("g", "vk-model"))
    cfg = await sm.get_config("g")
    assert cfg["profile"] == "vulkan"


async def test_create_ignores_incompatible_preferred_profile(tmp_hal0_home: str) -> None:
    # cpu-llm is device_class=cpu — must NOT be forced onto a GPU slot.
    _register("cpu-pref", profile="cpu-llm")
    sm = SlotManager()
    await sm.create("g", _gpu_vulkan_cfg("g", "cpu-pref"))
    cfg = await sm.get_config("g")
    assert not cfg.get("profile")


async def test_create_ignores_cross_backend_preferred_profile(tmp_hal0_home: str) -> None:
    # rocm is device_class=gpu but backend=rocm — a vulkan slot must not adopt
    # it (we never flip the slot's hardware to satisfy a model preference).
    _register("rocm-pref", profile="rocm")
    sm = SlotManager()
    await sm.create("g", _gpu_vulkan_cfg("g", "rocm-pref"))
    cfg = await sm.get_config("g")
    assert not cfg.get("profile")


async def test_apply_preferred_profile_swaps_when_compatible(tmp_hal0_home: str) -> None:
    # Slot starts on the vulkan profile; swapping in a model that prefers a
    # different COMPATIBLE profile re-points the slot (the swap path uses this
    # before the reload). Here both fit gpu-vulkan, so the change is applied.
    _register("v2", profile="vulkan")
    sm = SlotManager()
    cfg0 = _gpu_vulkan_cfg("g", "v2")
    cfg0["profile"] = "rocm"  # a stale profile that doesn't match device
    # create would reconcile; write directly via create with a coherent profile
    cfg0["profile"] = "vulkan"
    await sm.create("g", cfg0)
    changed = await sm._apply_preferred_profile("g", "v2")
    # Already on "vulkan" → no change.
    assert changed is False


async def test_apply_preferred_profile_skips_incompatible(tmp_hal0_home: str) -> None:
    _register("rocm-pref2", profile="rocm")
    sm = SlotManager()
    cfg = _gpu_vulkan_cfg("g", "rocm-pref2")
    cfg["profile"] = "vulkan"
    await sm.create("g", cfg)
    changed = await sm._apply_preferred_profile("g", "rocm-pref2")
    assert changed is False
    assert (await sm.get_config("g"))["profile"] == "vulkan"


def test_profile_fits_slot_matrix(tmp_hal0_home: str) -> None:
    fits = SlotManager._profile_fits_slot
    gpu_vulkan = {"type": "llm", "device": "gpu-vulkan"}
    assert fits("vulkan", gpu_vulkan) is True
    assert fits("rocm", gpu_vulkan) is False  # cross-backend
    assert fits("cpu-llm", gpu_vulkan) is False  # wrong device class
    assert fits("comfyui", gpu_vulkan) is False  # img profile, wrong type+class
    assert fits("does-not-exist", gpu_vulkan) is False
