"""CUDA release gate on the slot create / config-write / load paths.

CUDA is switched off in this release (``hal0.model_meta.CUDA_ENABLED``):
NVIDIA GPUs run on the Vulkan lane. The ``gpu-cuda`` device and ``cuda``
runner stay schema-valid so an existing config still PARSES, but no writer
may create or re-point a slot onto CUDA and the load path refuses it — all
with :data:`hal0.model_meta.CUDA_UNSUPPORTED_MESSAGE` via
:class:`hal0.slots.state.CudaNotSupported`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0 import model_meta
from hal0.config.schema import SlotConfig
from hal0.slots.config_write import _reconcile_device_profile, refuse_cuda_selection
from hal0.slots.manager import SlotManager
from hal0.slots.state import CudaNotSupported, SlotConfigError


def _cfg(name: str, *, device: str, profile: str = "chat", binary: str = "") -> dict:
    cfg = {
        "name": name,
        "port": 8091,
        "type": "llm",
        "device": device,
        "profile": profile,
        "provider": "llama-server",
        "group": "custom",
        "model": {"default": "m"},
    }
    if binary:
        cfg["binary"] = binary
    return cfg


def _write_existing_cuda_toml(home: str, name: str = "nv") -> Path:
    """A slot TOML written by an earlier release that supported CUDA."""
    slots = Path(home) / "etc" / "hal0" / "slots"
    slots.mkdir(parents=True, exist_ok=True)
    path = slots / f"{name}.toml"
    path.write_text(
        f'name = "{name}"\nport = 8092\ntype = "llm"\ndevice = "gpu-cuda"\n'
        'profile = "chat"\nprovider = "llama-server"\ngroup = "custom"\n'
        '\n[model]\ndefault = "m"\n'
    )
    return path


def test_cuda_not_supported_is_a_slot_config_error() -> None:
    """Existing ``except SlotConfigError`` callers (and the 400 mapping) catch it."""
    assert issubclass(CudaNotSupported, SlotConfigError)
    assert CudaNotSupported.code == "slot.cuda_not_supported"
    assert CudaNotSupported.status == 400


async def test_create_with_gpu_cuda_device_is_refused(tmp_hal0_home: str) -> None:
    sm = SlotManager()
    with pytest.raises(CudaNotSupported) as exc:
        await sm.create("nv", _cfg("nv", device="gpu-cuda"))
    assert str(exc.value) == model_meta.CUDA_UNSUPPORTED_MESSAGE
    assert "Vulkan lane" in str(exc.value)
    assert exc.value.details["selected"] == {"device": "gpu-cuda"}
    # Refused before anything landed on disk.
    assert not (Path(tmp_hal0_home) / "etc" / "hal0" / "slots" / "nv.toml").exists()


async def test_create_with_cuda_runner_is_refused(tmp_hal0_home: str) -> None:
    sm = SlotManager()
    with pytest.raises(CudaNotSupported):
        await sm.create("nv", _cfg("nv", device="gpu-vulkan", binary="cuda"))


def test_profile_runner_cuda_adoption_is_refused(tmp_path, monkeypatch) -> None:
    """A profile whose runner would flip the slot onto gpu-cuda is refused too."""
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    etc_dir = tmp_path / "etc" / "hal0"
    etc_dir.mkdir(parents=True, exist_ok=True)
    (etc_dir / "profiles.toml").write_text('[profile.nv]\nrunner = "cuda"\n')
    cfg = {"profile": "nv", "device": "gpu-vulkan", "binary": ""}
    with pytest.raises(CudaNotSupported):
        _reconcile_device_profile(cfg, changed={"profile"})


def test_existing_gpu_cuda_config_still_parses() -> None:
    """Hidden, not removed: the schema keeps accepting the value."""
    cfg = SlotConfig.model_validate(
        {"name": "nv", "port": 8092, "device": "gpu-cuda", "model": {"default": "m"}}
    )
    assert cfg.device == "gpu-cuda"


async def test_existing_gpu_cuda_slot_loads_config_but_refuses_load(tmp_hal0_home: str) -> None:
    _write_existing_cuda_toml(tmp_hal0_home)
    sm = SlotManager()
    # The config itself reads back fine ...
    cfg = await sm.get_config("nv")
    assert cfg["device"] == "gpu-cuda"
    # ... but loading it is refused with the Vulkan-lane message.
    with pytest.raises(CudaNotSupported) as exc:
        await sm.load("nv")
    assert "Vulkan lane" in str(exc.value)


async def test_existing_gpu_cuda_slot_can_move_to_vulkan(tmp_hal0_home: str) -> None:
    """A write that moves the slot OFF cuda always succeeds; an unrelated
    write that leaves the lane alone is not blocked either."""
    _write_existing_cuda_toml(tmp_hal0_home)
    sm = SlotManager()
    await sm.update_config("nv", {"port": 8093})  # unrelated: allowed
    await sm.update_config("nv", {"device": "gpu-vulkan"})
    cfg = await sm.get_config("nv")
    assert cfg["device"] == "gpu-vulkan"


async def test_update_onto_gpu_cuda_is_refused(tmp_hal0_home: str) -> None:
    sm = SlotManager()
    await sm.create("vk", _cfg("vk", device="gpu-vulkan"))
    with pytest.raises(CudaNotSupported):
        await sm.update_config("vk", {"device": "gpu-cuda"})
    cfg = await sm.get_config("vk")
    assert cfg["device"] == "gpu-vulkan"


def test_refuse_is_a_noop_when_switch_on(monkeypatch) -> None:
    """Reversible: the one switch re-admits the lane with no other change."""
    monkeypatch.setattr(model_meta, "CUDA_ENABLED", True)
    refuse_cuda_selection({"device": "gpu-cuda", "binary": "cuda"})


def test_refuse_ignores_non_lane_writes() -> None:
    refuse_cuda_selection({"device": "gpu-cuda"}, changed={"port"})
    with pytest.raises(CudaNotSupported):
        refuse_cuda_selection({"device": "gpu-cuda"}, changed={"device"})
    with pytest.raises(CudaNotSupported):
        refuse_cuda_selection({"slot": {"backend": "cuda"}})  # nested shape, legacy key
