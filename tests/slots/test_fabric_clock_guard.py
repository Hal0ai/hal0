"""Tests for hal0.slots.fabric_clock_guard (pure, tmp-path fake sysfs)."""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.slots.fabric_clock_guard import (
    FabricClockState,
    fabric_clock_message,
    read_fabric_clock,
    slot_uses_gpu,
    slot_uses_npu,
)

_LEVELS = [
    "0: 400Mhz",
    "1: 800Mhz",
    "2: 1200Mhz",
    "3: 1600Mhz",
    "4: 1800Mhz",
    "5: 1900Mhz",
    "6: 1950Mhz",
    "7: 2000Mhz",
]


def _fake_card(root: Path, *, card: str = "card1") -> Path:
    device = root / "class" / "drm" / card / "device"
    device.mkdir(parents=True)
    return device


def _write(device: Path, *, perf: str, marked_index: int | None) -> None:
    lines = list(_LEVELS)
    if marked_index is not None:
        lines[marked_index] = lines[marked_index] + " *"
    (device / "pp_dpm_fclk").write_text("\n".join(lines) + "\n")
    (device / "power_dpm_force_performance_level").write_text(perf)


# ── read_fabric_clock ────────────────────────────────────────────────────────


def test_held_via_high(tmp_path: Path) -> None:
    device = _fake_card(tmp_path)
    _write(device, perf="high", marked_index=7)
    state = read_fabric_clock(tmp_path)
    assert state.readable
    assert state.held
    assert state.perf_level == "high"


def test_held_via_manual_plus_top_marked(tmp_path: Path) -> None:
    device = _fake_card(tmp_path)
    _write(device, perf="manual", marked_index=7)
    state = read_fabric_clock(tmp_path)
    assert state.readable
    assert state.held
    assert state.active_index == 7
    assert state.top_index == 7


def test_not_held_auto(tmp_path: Path) -> None:
    device = _fake_card(tmp_path)
    _write(device, perf="auto", marked_index=7)
    state = read_fabric_clock(tmp_path)
    assert state.readable
    assert not state.held


def test_not_held_manual_but_lower_level_marked(tmp_path: Path) -> None:
    device = _fake_card(tmp_path)
    _write(device, perf="manual", marked_index=3)
    state = read_fabric_clock(tmp_path)
    assert state.readable
    assert not state.held
    assert state.active_index == 3
    assert state.top_index == 7


def test_not_held_when_multiple_markers_even_at_top(tmp_path: Path) -> None:
    """review P2-6: shell requires exactly one `*`; Python must too.

    A tree with both level 0 and level 7 (top) starred — e.g. a torn sysfs
    read, or a firmware quirk reporting two "active" levels — used to
    overwrite ``active_index`` with whichever marker was seen last, so a
    0-and-7-starred tree read as ``active_index == 7 == top_index`` and
    ``held == True``. That is the shell's own ``exactly one`` precondition
    stated inversely: with more than one marker, there is no reliable
    active level at all, and the clock must read as NOT held.
    """
    device = _fake_card(tmp_path)
    lines = list(_LEVELS)
    lines[0] = lines[0] + " *"
    lines[7] = lines[7] + " *"
    (device / "pp_dpm_fclk").write_text("\n".join(lines) + "\n")
    (device / "power_dpm_force_performance_level").write_text("manual")
    state = read_fabric_clock(tmp_path)
    assert state.readable
    assert state.active_index is None
    assert not state.held


def test_unreadable_when_no_amdgpu_device(tmp_path: Path) -> None:
    (tmp_path / "class" / "drm").mkdir(parents=True)
    state = read_fabric_clock(tmp_path)
    assert not state.readable
    assert not state.held
    assert state.device is None


def test_unreadable_when_control_file_missing(tmp_path: Path) -> None:
    device = tmp_path / "class" / "drm" / "card0" / "device"
    device.mkdir(parents=True)
    (device / "pp_dpm_fclk").write_text("\n".join(_LEVELS) + "\n")
    # power_dpm_force_performance_level deliberately absent -> not a candidate
    state = read_fabric_clock(tmp_path)
    assert not state.readable


def test_prefers_soc_gpu_from_kfd_topology(tmp_path: Path) -> None:
    # Two amdgpu devices; only card2 is named by KFD topology as gfx1151.
    held_card = _fake_card(tmp_path, card="card1")
    _write(held_card, perf="auto", marked_index=7)
    soc_card = _fake_card(tmp_path, card="card2")
    _write(soc_card, perf="high", marked_index=7)

    render = tmp_path / "class" / "drm" / "renderD128"
    render.mkdir(parents=True)
    (render / "device").symlink_to(soc_card)

    node = tmp_path / "class" / "kfd" / "kfd" / "topology" / "nodes" / "1"
    node.mkdir(parents=True)
    (node / "properties").write_text("gfx_target_version 110501\ndrm_render_minor 128\n")

    state = read_fabric_clock(tmp_path)
    assert state.device == str(soc_card)
    assert state.held


# ── predicates ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("backend", ["flm", "xdna2"])
def test_slot_uses_npu_true_for_npu_backends(backend: str) -> None:
    assert slot_uses_npu({"backend": backend})


@pytest.mark.parametrize("backend", ["vulkan", "rocm", "hip"])
def test_slot_uses_gpu_true_for_gpu_backends(backend: str) -> None:
    assert slot_uses_gpu({"backend": backend})


def test_slot_uses_gpu_false_for_npu_backend() -> None:
    assert not slot_uses_gpu({"backend": "flm"})


def test_slot_uses_gpu_false_for_cuda_backend() -> None:
    """review P2-4: CUDA is a discrete NVIDIA GPU, not the gfx1151 iGPU."""
    assert not slot_uses_gpu({"backend": "cuda"})
    assert not slot_uses_gpu({"backend": "cuda", "device_class": "gpu"})


def test_slot_uses_npu_false_for_gpu_backend() -> None:
    assert not slot_uses_npu({"backend": "vulkan"})


def test_predicates_read_device_class_field() -> None:
    assert slot_uses_npu({"backend": "", "device_class": "npu"})
    assert slot_uses_gpu({"backend": "", "device_class": "gpu"})


def test_predicates_accept_object_with_attributes() -> None:
    class Slot:
        backend = "vulkan"

    assert slot_uses_gpu(Slot())
    assert not slot_uses_npu(Slot())


# ── fabric_clock_message ─────────────────────────────────────────────────────


def test_message_names_npu_and_gpu_slots_and_host_commands() -> None:
    state = FabricClockState(
        device="/sys/class/drm/card1/device",
        perf_level="auto",
        levels=tuple(_LEVELS),
        active_index=0,
        top_index=7,
        held=False,
        readable=True,
    )
    msg = fabric_clock_message(state, "flm-brain", ["chat", "coder"])
    assert "flm-brain" in msg
    assert "chat, coder" in msg
    assert "hal0-fabric-clock hold" in msg
    assert "scripts/proxmox-ve/hal0-fabric-clock" in msg
    assert "ROCm/TheRock#8709" in msg


def test_message_notes_unreadable_state() -> None:
    state = FabricClockState(device=None, perf_level=None, readable=False)
    msg = fabric_clock_message(state, "flm-brain", ["chat"])
    assert "could not be read" in msg
    assert "hal0-fabric-clock hold" in msg


def test_message_reassures_when_already_held() -> None:
    state = FabricClockState(
        device="/sys/class/drm/card1/device",
        perf_level="high",
        held=True,
        readable=True,
    )
    msg = fabric_clock_message(state, "flm-brain", ["chat"])
    assert "safely" in msg
