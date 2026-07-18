"""Model preferred runner (§7.1b / ML-4): a model's ``preferred_runner`` loads with it.

A registry model may declare ``preferred_runner`` — a key into
``hal0.runners.RUNNER_IMAGES``. On slot create (empty ``image``) and on every
model swap the slot adopts that runner's resolved image, but ONLY when it
fits the slot's device/backend; an incompatible preference is ignored so the
slot keeps its device-default image and the manager never flips the slot's
hardware to satisfy a model. Sibling of ``test_model_preferred_profile.py``.
"""

from __future__ import annotations

from hal0.registry.model import Model
from hal0.registry.store import ModelRegistry
from hal0.runners import get_runner, resolve_runner_image
from hal0.slots.manager import SlotManager


def _register(model_id: str, *, preferred_runner: str | None) -> None:
    ModelRegistry().add(
        Model(
            id=model_id,
            path=f"/tmp/{model_id}.gguf",
            capabilities=["chat"],
            preferred_runner=preferred_runner,
        )
    )


def _gpu_vulkan_cfg(name: str, model: str) -> dict:
    # No ``image`` key — the create path fills it from the model preference.
    return {
        "name": name,
        "port": 8092,
        "type": "llm",
        "device": "gpu-vulkan",
        "provider": "llama-server",
        "enabled": True,
        "group": "custom",
        "model": {"default": model},
    }


async def test_create_adopts_compatible_preferred_runner(tmp_hal0_home: str) -> None:
    _register("vk-runner-model", preferred_runner="vulkanfpx")
    sm = SlotManager()
    await sm.create("g", _gpu_vulkan_cfg("g", "vk-runner-model"))
    cfg = await sm.get_config("g")
    assert cfg["image"] == resolve_runner_image(get_runner("vulkanfpx"))


async def test_create_ignores_incompatible_device_class(tmp_hal0_home: str) -> None:
    # "cpu" runner is device_class=cpu — must NOT be forced onto a GPU slot.
    _register("cpu-runner-model", preferred_runner="cpu")
    sm = SlotManager()
    await sm.create("g", _gpu_vulkan_cfg("g", "cpu-runner-model"))
    cfg = await sm.get_config("g")
    assert not cfg.get("image")


async def test_create_ignores_cross_backend_preferred_runner(tmp_hal0_home: str) -> None:
    # rocmfpx is device_class=gpu but backend=rocm — a vulkan slot must not
    # adopt it (we never flip the slot's hardware to satisfy a model
    # preference).
    _register("rocm-runner-model", preferred_runner="rocmfpx")
    sm = SlotManager()
    await sm.create("g", _gpu_vulkan_cfg("g", "rocm-runner-model"))
    cfg = await sm.get_config("g")
    assert not cfg.get("image")


async def test_create_ignores_unknown_runner_key(tmp_hal0_home: str) -> None:
    _register("bad-runner-model", preferred_runner="not-a-real-runner")
    sm = SlotManager()
    await sm.create("g", _gpu_vulkan_cfg("g", "bad-runner-model"))
    cfg = await sm.get_config("g")
    assert not cfg.get("image")


async def test_create_leaves_explicit_image_override_untouched(tmp_hal0_home: str) -> None:
    _register("vk-runner-model2", preferred_runner="vulkanfpx")
    sm = SlotManager()
    cfg_in = _gpu_vulkan_cfg("g", "vk-runner-model2")
    cfg_in["image"] = "ghcr.io/operator/custom:pin"
    await sm.create("g", cfg_in)
    cfg = await sm.get_config("g")
    assert cfg["image"] == "ghcr.io/operator/custom:pin"


async def test_apply_preferred_runner_swaps_when_compatible(tmp_hal0_home: str) -> None:
    _register("vk-runner-model3", preferred_runner="vulkanfpx")
    sm = SlotManager()
    cfg_in = _gpu_vulkan_cfg("g", "vk-runner-model3")
    cfg_in["image"] = "ghcr.io/operator/custom:pin"  # deliberate initial pin
    await sm.create("g", cfg_in)
    changed = await sm._apply_preferred_runner("g", "vk-runner-model3")
    assert changed is True
    cfg = await sm.get_config("g")
    assert cfg["image"] == resolve_runner_image(get_runner("vulkanfpx"))


async def test_apply_preferred_runner_noop_when_already_adopted(tmp_hal0_home: str) -> None:
    _register("vk-runner-model4", preferred_runner="vulkanfpx")
    sm = SlotManager()
    cfg_in = _gpu_vulkan_cfg("g", "vk-runner-model4")
    cfg_in["image"] = resolve_runner_image(get_runner("vulkanfpx"))
    await sm.create("g", cfg_in)
    changed = await sm._apply_preferred_runner("g", "vk-runner-model4")
    assert changed is False


async def test_apply_preferred_runner_skips_incompatible(tmp_hal0_home: str) -> None:
    _register("rocm-runner-model2", preferred_runner="rocmfpx")
    sm = SlotManager()
    cfg_in = _gpu_vulkan_cfg("g", "rocm-runner-model2")
    cfg_in["image"] = "ghcr.io/operator/custom:pin"
    await sm.create("g", cfg_in)
    changed = await sm._apply_preferred_runner("g", "rocm-runner-model2")
    assert changed is False
    assert (await sm.get_config("g"))["image"] == "ghcr.io/operator/custom:pin"


async def test_apply_preferred_runner_noop_when_no_preference(tmp_hal0_home: str) -> None:
    _register("no-pref-model", preferred_runner=None)
    sm = SlotManager()
    cfg_in = _gpu_vulkan_cfg("g", "no-pref-model")
    cfg_in["image"] = "ghcr.io/operator/custom:pin"
    await sm.create("g", cfg_in)
    changed = await sm._apply_preferred_runner("g", "no-pref-model")
    assert changed is False
    assert (await sm.get_config("g"))["image"] == "ghcr.io/operator/custom:pin"


def test_runner_fits_slot_matrix(tmp_hal0_home: str) -> None:
    fits = SlotManager._runner_fits_slot
    gpu_vulkan = {"type": "llm", "device": "gpu-vulkan"}
    assert fits("vulkanfpx", gpu_vulkan) is True
    assert fits("rocmfpx", gpu_vulkan) is False  # cross-backend
    assert fits("cpu", gpu_vulkan) is False  # wrong device class
    assert fits("comfyui", gpu_vulkan) is False  # img runner, wrong device class
    assert fits("does-not-exist", gpu_vulkan) is False
