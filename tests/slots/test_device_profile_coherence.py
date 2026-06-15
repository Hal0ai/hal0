"""Device↔profile backend coherence on slot create / update_config.

A GPU slot carries two fields that each imply a backend: ``device``
(``gpu-rocm``/``gpu-vulkan`` → the llama-server backend) and ``profile``
(the ProfileConfig, whose ``backend`` selects the container image + flags).
Before this guard they could diverge silently: the dashboard set the
``utility`` slot's ``device`` to ``gpu-vulkan`` while leaving its
``profile`` at ``rocm-dnse``, so the slot reported ``backend=vulkan`` yet
resolved the ROCm image + ROCm-only MTP draft flags — and re-picking the
profile in the drawer never corrected the device.

The invariant: for a GPU slot, ``device`` must agree with
``profile.backend``. Whichever field the operator changes wins; the other
is reconciled. An explicit contradiction (both changed, still conflicting)
is rejected rather than silently resolved. Non-GPU profiles (npu/cpu/img,
``backend=None``) are left untouched.
"""

from __future__ import annotations

import pytest

from hal0.slots.manager import SlotManager
from hal0.slots.state import SlotConfigError


def _gpu_cfg(name: str, *, device: str, profile: str, model: str = "m") -> dict:
    return {
        "name": name,
        "port": 8090,
        "type": "llm",
        "device": device,
        "profile": profile,
        "provider": "llama-server",
        "enabled": True,
        "group": "custom",
        "model": {"default": model},
    }


async def test_profile_change_drives_device(tmp_hal0_home: str) -> None:
    """Switching the profile re-derives device — the exact utility-slot bug.

    A vulkan slot re-pointed at the rocm-dnse profile must end up on
    ``device=gpu-rocm``; otherwise the drawer 'changes' the profile but the
    backend stays vulkan forever.
    """
    sm = SlotManager()
    await sm.create("util", _gpu_cfg("util", device="gpu-vulkan", profile="vulkan"))

    await sm.update_config("util", {"profile": "rocm-dnse"})

    cfg = await sm.get_config("util")
    assert cfg["profile"] == "rocm-dnse"
    assert cfg["device"] == "gpu-rocm"


async def test_device_change_reconciles_conflicting_profile(tmp_hal0_home: str) -> None:
    """Flipping device across backends drops an incompatible profile.

    The ``POST /api/slots/{name}/backend`` control writes only ``device``;
    a cross-backend flip must reconcile the profile to a compatible one so
    no rocm-dnse+vulkan pair is ever persisted.
    """
    sm = SlotManager()
    await sm.create("util", _gpu_cfg("util", device="gpu-rocm", profile="rocm-dnse"))

    await sm.update_config("util", {"device": "gpu-vulkan"})

    cfg = await sm.get_config("util")
    assert cfg["device"] == "gpu-vulkan"
    assert cfg["profile"] == "vulkan"


async def test_unrelated_update_preserves_coherent_pair(tmp_hal0_home: str) -> None:
    """A change that touches neither device nor profile leaves both intact."""
    sm = SlotManager()
    await sm.create("util", _gpu_cfg("util", device="gpu-rocm", profile="rocm-dnse"))

    await sm.update_config("util", {"model": {"context_size": 32768}})

    cfg = await sm.get_config("util")
    assert cfg["device"] == "gpu-rocm"
    assert cfg["profile"] == "rocm-dnse"
    assert cfg["model"]["context_size"] == 32768


async def test_explicit_contradiction_rejected(tmp_hal0_home: str) -> None:
    """Changing both fields to conflicting backends is an operator error."""
    sm = SlotManager()
    await sm.create("util", _gpu_cfg("util", device="gpu-rocm", profile="rocm-dnse"))

    with pytest.raises(SlotConfigError, match=r"(?i)backend"):
        await sm.update_config("util", {"profile": "rocm-dnse", "device": "gpu-vulkan"})


async def test_create_rejects_incoherent_pair(tmp_hal0_home: str) -> None:
    """create() must refuse a vulkan device paired with a rocm profile.

    This is the door the dashboard left open — 'allowed the utility slot to
    be set to vulkan but with a ROCM-MTP profile'.
    """
    sm = SlotManager()
    with pytest.raises(SlotConfigError, match=r"(?i)backend"):
        await sm.create("util", _gpu_cfg("util", device="gpu-vulkan", profile="rocm-dnse"))


async def test_non_gpu_profile_untouched(tmp_hal0_home: str) -> None:
    """A non-GPU profile (backend=None) never triggers device reconciliation."""
    sm = SlotManager()
    await sm.create(
        "voice",
        {
            "name": "voice",
            "port": 8091,
            "type": "tts",
            "device": "cpu",
            "profile": "tts",
            "provider": "llama-server",
            "enabled": True,
            "group": "custom",
            "model": {"default": "kokoro"},
        },
    )

    await sm.update_config("voice", {"model": {"context_size": 2048}})

    cfg = await sm.get_config("voice")
    assert cfg["device"] == "cpu"
    assert cfg["profile"] == "tts"
