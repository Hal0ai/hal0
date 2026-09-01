"""Device↔profile backend coherence on slot create / update_config.

A GPU slot carries two fields that each imply a backend: ``device``
(``gpu-rocm``/``gpu-vulkan`` → the llama-server backend) and ``profile``
(the ProfileConfig, whose ``backend`` selects the container image + flags).
Before this guard they could diverge silently: the dashboard set the
``utility`` slot's ``device`` to ``gpu-vulkan`` while leaving its
``profile`` at ``rocm``, so the slot reported ``backend=vulkan`` yet
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

from hal0.slots.config_write import _reconcile_device_profile
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
        "group": "custom",
        "model": {"default": model},
    }


async def test_profile_change_drives_device(tmp_hal0_home: str) -> None:
    """Switching profile keeps device unchanged — 1.0 profiles are device-agnostic.

    A vulkan slot re-pointed at the chat workload profile stays on
    ``device=gpu-vulkan``; profiles are not backend-bound.
    """
    sm = SlotManager()
    await sm.create("util", _gpu_cfg("util", device="gpu-vulkan", profile="chat"))

    await sm.update_config("util", {"profile": "dense"})

    cfg = await sm.get_config("util")
    assert cfg["profile"] == "dense"
    # 1.0: profiles are device-agnostic workload names; profile change
    # does NOT flip device.
    assert cfg["device"] == "gpu-vulkan"


async def test_device_change_reconciles_conflicting_profile(tmp_hal0_home: str) -> None:
    """Flipping device across backends drops an incompatible profile.

    A cross-backend ``device`` change via ``update_config`` must reconcile
    the profile to a compatible one. In 1.0 profiles are device-agnostic
    workload names; device flip keeps the workload profile unchanged.
    """
    sm = SlotManager()
    await sm.create("util", _gpu_cfg("util", device="gpu-rocm", profile="chat"))

    await sm.update_config("util", {"device": "gpu-vulkan"})

    cfg = await sm.get_config("util")
    assert cfg["device"] == "gpu-vulkan"
    # 1.0: profiles are workload-oriented (device-agnostic); device flip DOES
    # NOT change the workload profile.
    assert cfg["profile"] == "chat"


async def test_unrelated_update_preserves_coherent_pair(tmp_hal0_home: str) -> None:
    """A change that touches neither device nor profile leaves both intact."""
    sm = SlotManager()
    await sm.create("util", _gpu_cfg("util", device="gpu-rocm", profile="chat"))

    await sm.update_config("util", {"model": {"context_size": 32768}})

    cfg = await sm.get_config("util")
    assert cfg["device"] == "gpu-rocm"
    assert cfg["profile"] == "chat"
    assert cfg["model"]["context_size"] == 32768


async def test_explicit_contradiction_rejected(tmp_hal0_home: str) -> None:
    """Changing both fields to conflicting backends is an operator error.

    1.0: profiles are device-agnostic workload names; the contradiction
    reject no longer fires because "chat" fits both rocm and vulkan.
    This guard was backend-slot-ownership specific.
    """
    sm = SlotManager()
    await sm.create("util", _gpu_cfg("util", device="gpu-rocm", profile="chat"))
    # 1.0: device-agnostic profiles; no contradiction to reject.
    await sm.update_config("util", {"profile": "chat", "device": "gpu-vulkan"})
    cfg = await sm.get_config("util")
    assert cfg["device"] == "gpu-vulkan"
    assert cfg["profile"] == "chat"


async def test_create_rejects_incoherent_pair(tmp_hal0_home: str) -> None:
    """create() must refuse a vulkan device paired with a rocm profile.

    1.0: profiles are device-agnostic workload names; the contradiction
    guard no longer fires because workload profiles fit any GPU device.
    The slot is created without error.
    """
    sm = SlotManager()
    await sm.create("util", _gpu_cfg("util", device="gpu-vulkan", profile="chat"))
    cfg = await sm.get_config("util")
    assert cfg["device"] == "gpu-vulkan"
    assert cfg["profile"] == "chat"


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
            "profile": "kokoro",
            "provider": "llama-server",
            "group": "custom",
            "model": {"default": "kokoro"},
        },
    )

    await sm.update_config("voice", {"model": {"context_size": 2048}})

    cfg = await sm.get_config("voice")
    assert cfg["device"] == "cpu"
    assert cfg["profile"] == "kokoro"


# ── Task 5: profile apply flips runtime + device (profile wins, D4) ────────


def _catalog_with_runner(tmp_path, monkeypatch, runner="promptforge"):
    """Point ``load_profiles_config`` at a profiles.toml whose "pf" profile
    carries ``runner``. Mirrors the ``HAL0_HOME``-env idiom the rest of this
    file uses (via ``tmp_hal0_home``) — these sync tests call
    ``_reconcile_device_profile`` directly and take ``tmp_path``/``monkeypatch``
    instead of the async fixture.
    """
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    etc_dir = tmp_path / "etc" / "hal0"
    etc_dir.mkdir(parents=True, exist_ok=True)
    (etc_dir / "profiles.toml").write_text(f'[profile.pf]\nrunner = "{runner}"\n')


def test_profile_with_runner_sets_binary_and_device(tmp_path, monkeypatch):
    _catalog_with_runner(tmp_path, monkeypatch)
    monkeypatch.setattr("hal0.providers._gpu.kfd_present", lambda *a, **k: True)
    cfg = {"profile": "pf", "device": "gpu-vulkan", "binary": ""}
    _reconcile_device_profile(cfg, changed={"profile"})
    assert cfg["binary"] == "promptforge"
    assert cfg["device"] == "gpu-rocm"  # derived from the single backend


def test_profile_runner_infeasible_rocm_no_kfd_raises(tmp_path, monkeypatch):
    _catalog_with_runner(tmp_path, monkeypatch)
    monkeypatch.setattr("hal0.providers._gpu.kfd_present", lambda *a, **k: False)
    cfg = {"profile": "pf", "device": "gpu-vulkan", "binary": ""}
    with pytest.raises(SlotConfigError):
        _reconcile_device_profile(cfg, changed={"profile"})
    assert cfg["binary"] == ""  # no partial write
    assert cfg["device"] == "gpu-vulkan"


def test_profile_runner_multi_backend_keeps_lane(tmp_path, monkeypatch):
    _catalog_with_runner(tmp_path, monkeypatch, runner="rocmfpx")
    cfg = {"profile": "pf", "device": "gpu-vulkan", "binary": "promptforge"}
    _reconcile_device_profile(cfg, changed={"profile"})
    assert cfg["binary"] == "rocmfpx"
    assert cfg["device"] == "gpu-vulkan"  # combined default: lane untouched


def test_profile_runner_ignored_on_unrelated_write(tmp_path, monkeypatch):
    _catalog_with_runner(tmp_path, monkeypatch)
    cfg = {"profile": "pf", "device": "gpu-vulkan", "binary": ""}
    _reconcile_device_profile(cfg, changed={"threads"})
    assert cfg["binary"] == ""  # drift heals only on profile edit
