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

from hal0.providers.container import _effective_runner, _resolve_image_ref
from hal0.slots.config_write import (
    _reconcile_device_profile,
    reconcile_and_guard_slot_config,
    reconcile_slot_updates,
)
from hal0.slots.manager import SlotManager
from hal0.slots.state import RunnerFamilyMismatch, SlotConfigError


def _gpu_cfg(name: str, *, device: str, profile: str, model: str = "m") -> dict:
    return {
        "name": name,
        "port": 8090,
        "type": "llm",
        "device": device,
        "profile": profile,
        "provider": "llama-server",
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

    await sm.update_config("util", {"idle_timeout_s": 600})

    cfg = await sm.get_config("util")
    assert cfg["device"] == "gpu-rocm"
    assert cfg["profile"] == "chat"
    assert cfg["idle_timeout_s"] == 600


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
            "model": {"default": "kokoro"},
        },
    )

    await sm.update_config("voice", {"idle_timeout_s": 120})

    cfg = await sm.get_config("voice")
    assert cfg["device"] == "cpu"
    assert cfg["profile"] == "kokoro"


# ── Task 5: profile apply flips runtime + device (profile wins, D4) ────────


def _catalog_with_runner(tmp_path, monkeypatch, runner="qwen3tts"):
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
    monkeypatch.setattr("hal0.providers._gpu.render_node_present", lambda *a, **k: True)
    cfg = {"profile": "pf", "device": "gpu-vulkan", "binary": ""}
    _reconcile_device_profile(cfg, changed={"profile"})
    assert cfg["binary"] == "qwen3tts"
    assert cfg["device"] == "gpu-rocm"  # derived from the single backend


def test_profile_runner_infeasible_rocm_no_kfd_raises(tmp_path, monkeypatch):
    _catalog_with_runner(tmp_path, monkeypatch)
    monkeypatch.setattr("hal0.providers._gpu.kfd_present", lambda *a, **k: False)
    cfg = {"profile": "pf", "device": "gpu-vulkan", "binary": ""}
    with pytest.raises(SlotConfigError):
        _reconcile_device_profile(cfg, changed={"profile"})
    assert cfg["binary"] == ""  # no partial write
    assert cfg["device"] == "gpu-vulkan"


@pytest.mark.parametrize(("kfd", "render"), [(True, False), (False, False)])
def test_profile_runner_infeasible_rocm_without_both_nodes_raises(
    tmp_path, monkeypatch, kfd, render
):
    """#2313: a ROCm-only runtime opens /dev/kfd AND a render node. On a box
    with kfd forwarded but no ``/dev/dri/renderD*`` the apply must refuse
    rather than write a ``gpu-rocm`` slot that cannot spawn — the picker
    already hides that lane there (#2354)."""
    _catalog_with_runner(tmp_path, monkeypatch)
    monkeypatch.setattr("hal0.providers._gpu.kfd_present", lambda *a, **k: kfd)
    monkeypatch.setattr("hal0.providers._gpu.render_node_present", lambda *a, **k: render)
    cfg = {"profile": "pf", "device": "gpu-vulkan", "binary": ""}
    with pytest.raises(SlotConfigError, match="render node"):
        _reconcile_device_profile(cfg, changed={"profile"})
    assert cfg["binary"] == ""  # no partial write
    assert cfg["device"] == "gpu-vulkan"


def test_profile_runner_multi_backend_keeps_lane(tmp_path, monkeypatch):
    _catalog_with_runner(tmp_path, monkeypatch, runner="standard")
    cfg = {"profile": "pf", "device": "gpu-vulkan", "binary": "qwen3tts"}
    _reconcile_device_profile(cfg, changed={"profile"})
    assert cfg["binary"] == "standard"
    assert cfg["device"] == "gpu-vulkan"  # combined default: lane untouched


def test_profile_runner_ignored_on_unrelated_write(tmp_path, monkeypatch):
    _catalog_with_runner(tmp_path, monkeypatch)
    cfg = {"profile": "pf", "device": "gpu-vulkan", "binary": ""}
    _reconcile_device_profile(cfg, changed={"threads"})
    assert cfg["binary"] == ""  # drift heals only on profile edit


# ── a runner the profile does not launch (non-llama ``binary``) ──────────────
#
# The provider comes from the profile's family and, on the llama path, the image
# from ``binary``. A non-llama runner under a llama profile would launch that
# runner's image with llama-server arguments, so the opt-out clears it and the
# write seam refuses to create it.


def _rocm_lane(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("hal0.providers._gpu.kfd_present", lambda *a, **k: True)
    monkeypatch.setattr("hal0.providers._gpu.render_node_present", lambda *a, **k: True)


def _llm_cfg(**overrides: object) -> dict:
    return {"name": "flash", "type": "llm", "device": "gpu-rocm", **overrides}


def test_halogen_opt_in_then_opt_out_clears_the_runner(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _rocm_lane(monkeypatch)
    cfg = _llm_cfg(device="gpu-vulkan", profile="agent")

    cfg = reconcile_slot_updates(cfg, {"profile": "halogen"})
    assert cfg["binary"] == "halogen"
    assert cfg["device"] == "gpu-rocm"

    cfg = reconcile_slot_updates(cfg, {"profile": "agent"})
    assert "binary" not in cfg
    assert cfg["device"] == "gpu-rocm"  # ADR-0029: a profile selects no hardware
    assert _effective_runner(cfg, None).runtime_family == "llama-server"
    assert "peonist" not in _resolve_image_ref(cfg, None)


def test_clearing_the_profile_clears_a_runner_it_cannot_launch(tmp_hal0_home: str) -> None:
    cfg = _llm_cfg(profile="halogen", binary="halogen")
    cfg = reconcile_slot_updates(cfg, {"profile": None})
    assert "profile" not in cfg
    assert "binary" not in cfg


def test_opt_out_clears_the_runner_on_a_nested_config(tmp_hal0_home: str) -> None:
    cfg = {"slot": _llm_cfg(profile="halogen", binary="halogen"), "model": {"default": "m"}}
    cfg = reconcile_slot_updates(cfg, {"profile": "agent"})
    assert cfg["slot"]["profile"] == "agent"
    assert "binary" not in cfg["slot"]


def test_llama_family_runner_survives_a_runnerless_profile(tmp_hal0_home: str) -> None:
    """``cuda`` is a llama runner: the llama provider launches its image correctly."""
    cfg = _llm_cfg(device="gpu-cuda", profile="agent", binary="cuda")
    cfg = reconcile_slot_updates(cfg, {"profile": "chat"})
    assert cfg["binary"] == "cuda"


def test_reselecting_an_engine_profile_keeps_its_own_runner(tmp_hal0_home: str) -> None:
    """Runner-less seeds of a single-purpose engine still launch that engine."""
    cfg = {"name": "voice", "type": "tts", "device": "cpu", "profile": "kokoro", "binary": "kokoro"}
    cfg = reconcile_slot_updates(cfg, {"profile": "kokoro"})
    assert cfg["binary"] == "kokoro"


def test_an_unrelated_write_leaves_the_runner_alone(tmp_hal0_home: str) -> None:
    """The clear is driven by a profile write, like the rest of the reconcile."""
    cfg = _llm_cfg(profile="agent", binary="halogen")
    cfg = reconcile_slot_updates(cfg, {"threads": 4})
    assert cfg["binary"] == "halogen"


@pytest.mark.parametrize(
    "base",
    [
        pytest.param(_llm_cfg(profile="agent"), id="llama-profile"),
        pytest.param(_llm_cfg(), id="no-profile"),
    ],
)
def test_write_seam_refuses_a_runner_the_profile_cannot_launch(
    tmp_hal0_home: str, base: dict
) -> None:
    with pytest.raises(RunnerFamilyMismatch) as exc:
        reconcile_and_guard_slot_config("flash", base, {"binary": "halogen"})
    assert exc.value.code == "slot.runner_family_mismatch"
    assert exc.value.status == 409
    assert "halogen" in exc.value.details["profiles"]
    assert "halogen" in str(exc.value)


def test_write_seam_refuses_the_mismatched_pair_written_together(tmp_hal0_home: str) -> None:
    """A write that names both is the operator's own choice, so it is refused
    rather than having its ``binary`` quietly dropped."""
    base = _llm_cfg(profile="halogen", binary="halogen")
    with pytest.raises(RunnerFamilyMismatch):
        reconcile_and_guard_slot_config("flash", base, {"profile": "agent", "binary": "halogen"})


def test_write_seam_allows_a_llama_runner_under_the_halogen_profile(tmp_hal0_home: str) -> None:
    """The provider launches its own image, so this pair launches correctly."""
    base = _llm_cfg(profile="halogen", binary="halogen")
    merged = reconcile_and_guard_slot_config("flash", base, {"binary": "standard"})
    assert merged["binary"] == "standard"


def test_write_seam_does_not_veto_an_unrelated_write_on_an_old_slot(tmp_hal0_home: str) -> None:
    """A write that moves neither key did not create the state on disk."""
    base = _llm_cfg(profile="agent", binary="halogen")
    merged = reconcile_and_guard_slot_config("flash", base, {"threads": 4})
    assert merged["threads"] == 4


async def test_create_refuses_a_runner_the_profile_cannot_launch(tmp_hal0_home: str) -> None:
    sm = SlotManager()
    with pytest.raises(RunnerFamilyMismatch):
        await sm.create(
            "flash",
            {**_gpu_cfg("flash", device="gpu-rocm", profile="agent"), "binary": "halogen"},
        )
