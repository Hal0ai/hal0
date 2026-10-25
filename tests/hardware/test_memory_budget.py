"""Tests for hal0.hardware.memory_budget — the single owner of the dashboard's
memory figures (ADR-0039).

The model is pure: a GPU sample, per-slot attribution rows, ComfyUI's own
figure and a RAM reading go in, one ``MemoryBudget`` comes out. Fixtures are
in MiB (what the sample and ``build_per_slot`` carry); expectations are in GB
(MiB / 1024), rounded to one decimal.
"""

from __future__ import annotations

import asyncio
import dataclasses
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from hal0.api import memory_source
from hal0.api.memory_source import collect_memory_budget
from hal0.hardware.gpu_view import GPUMemorySample
from hal0.hardware.memory_budget import (
    RENDER_MARGIN_GB,
    GpuBudget,
    MemoryBudget,
    RamBudget,
    SlotShare,
    build_memory_budget,
    cgroup_limit_mb,
    read_ram,
)

RAM = RamBudget(limit_gb=64.0, used_gb=20.0, free_gb=44.0, source="container")


def _shared_sample(
    *,
    gtt_total_mb: float | None = 118784.0,
    gtt_used_mb: float | None = 44441.6,
) -> GPUMemorySample:
    """A UMA part: tiny BAR carve-out in VRAM, the real pool is GTT."""
    return GPUMemorySample(
        vendor="amd",
        is_uma=True,
        vram_total_mb=512.0,
        gtt_total_mb=gtt_total_mb,
        total_mb=gtt_total_mb,
        vram_used_mb=100.0,
        gtt_used_mb=gtt_used_mb,
        used_mb=gtt_used_mb,
        gpu_busy=0.1,
        util_is_forced_high=False,
    )


def _dedicated_sample(
    *,
    vram_total_mb: float | None = 24576.0,
    vram_used_mb: float | None = 8192.0,
) -> GPUMemorySample:
    return GPUMemorySample(
        vendor="nvidia",
        is_uma=False,
        vram_total_mb=vram_total_mb,
        gtt_total_mb=None,
        total_mb=vram_total_mb,
        vram_used_mb=vram_used_mb,
        gtt_used_mb=None,
        used_mb=vram_used_mb,
        gpu_busy=0.2,
        util_is_forced_high=False,
    )


def _row(vram_mb: float, state: str = "ready", model_id: str = "m") -> dict[str, Any]:
    """A ``build_per_slot``-shaped row (UMA: mem_mb == vram_mb, ram_mb == 0)."""
    return {
        "vram_mb": vram_mb,
        "ram_mb": 0.0,
        "mem_mb": vram_mb,
        "state": state,
        "model_id": model_id,
    }


REFERENCE_ROWS = {
    "utility": _row(31539.2),
    "tts": _row(6348.8),
    "brain": _row(2867.2),
    "img": _row(6758.4),
}
REFERENCE_GPU_SLOTS = frozenset({"utility", "tts", "brain", "img"})


def _reference_budget(**overrides: Any) -> MemoryBudget:
    kwargs: dict[str, Any] = {
        "sample": _shared_sample(),
        "gpu_capable": True,
        "per_slot": REFERENCE_ROWS,
        "gpu_slots": REFERENCE_GPU_SLOTS,
        "img_slot": "img",
        "image_engine_mb": None,
        "ram": RAM,
    }
    kwargs.update(overrides)
    return build_memory_budget(**kwargs)


def _gpu(budget: MemoryBudget) -> GpuBudget:
    assert budget.gpu is not None
    return budget.gpu


def test_render_margin_is_two_gb() -> None:
    assert RENDER_MARGIN_GB == 2.0


def test_shared_pool_reference_box() -> None:
    budget = _reference_budget()
    gpu = _gpu(budget)

    assert gpu.kind == "shared"
    assert gpu.total_gb == 116.0
    assert gpu.used_gb == 43.4
    assert gpu.other_guests_gb == 0
    assert gpu.attribution_excess_gb == 3.0
    assert gpu.free_gb == 72.6
    # The image slot is the engine here (no ComfyUI view): 6758.4 MiB = 6.6 GB.
    assert gpu.image_engine_gb == 6.6
    assert budget.render_free_gb == 107.4  # 116 - 0 - 6.6 - 2


def test_this_box_lists_the_other_gpu_slots_with_their_state() -> None:
    gpu = _gpu(_reference_budget())

    assert {s.slot: (s.gb, s.state) for s in gpu.this_box} == {
        "utility": (30.8, "ready"),
        "tts": (6.2, "ready"),
        "brain": (2.8, "ready"),
    }
    assert all(isinstance(s, SlotShare) for s in gpu.this_box)
    assert "img" not in {s.slot for s in gpu.this_box}


def test_ram_budget_passes_through_untouched() -> None:
    budget = _reference_budget()

    assert budget.ram is RAM


def test_other_guests_on_a_second_guest() -> None:
    budget = _reference_budget(per_slot={}, gpu_slots=frozenset())
    gpu = _gpu(budget)

    assert gpu.this_box == ()
    assert gpu.image_engine_gb == 0
    assert gpu.other_guests_gb == 43.4
    assert gpu.attribution_excess_gb == 0
    assert budget.render_free_gb == 70.6


def test_image_engine_view_wins_over_slot_attribution() -> None:
    budget = _reference_budget(image_engine_mb=4505.6)
    gpu = _gpu(budget)

    assert gpu.image_engine_gb == 4.4
    # 43.4 used - 39.8 resident - 4.4 engine = -0.8 -> clamped.
    assert gpu.other_guests_gb == 0
    # 39.8 + 4.4 - 43.4 = 0.8 over-attributed.
    assert gpu.attribution_excess_gb == 0.8
    assert budget.render_free_gb == 109.6  # 116 - 0 - 4.4 - 2


def test_image_engine_zero_from_the_view_is_not_a_missing_view() -> None:
    """ComfyUI reporting 0 MiB is a figure, not "unknown" — slot attribution loses."""
    gpu = _gpu(_reference_budget(image_engine_mb=0.0))

    assert gpu.image_engine_gb == 0


def test_the_engine_is_named_and_marked_estimated_by_the_budget() -> None:
    """The backend says which slot hosts the engine and whether its figure is a guess."""
    by_slot = _gpu(_reference_budget())
    by_view = _gpu(_reference_budget(image_engine_mb=4505.6))

    assert (by_slot.image_engine_slot, by_slot.image_engine_estimated) == ("img", True)
    assert (by_view.image_engine_slot, by_view.image_engine_estimated) == ("img", False)


def test_no_img_slot_means_no_engine_name_and_nothing_estimated() -> None:
    gpu = _gpu(_reference_budget(img_slot=None, gpu_slots=frozenset(REFERENCE_ROWS)))

    assert gpu.image_engine_slot is None
    assert gpu.image_engine_estimated is False


def test_img_slot_without_a_row_is_named_but_not_estimated() -> None:
    """Nothing stood in for the engine: no ComfyUI reading and no row to borrow."""
    rows = {k: v for k, v in REFERENCE_ROWS.items() if k != "img"}
    gpu = _gpu(_reference_budget(per_slot=rows))

    assert gpu.image_engine_slot == "img"
    assert gpu.image_engine_gb == 0
    assert gpu.image_engine_estimated is False


def test_dedicated_card_ignores_comfyuis_device_wide_reading() -> None:
    """On a discrete card ComfyUI's ``vram_total - vram_free`` is the whole device.

    24 GB card, 15 GB in use: 14 GB is the brain slot and 1 GB is someone else's. ComfyUI,
    idle, still reports 15 GB (everything on the card). Taking that as the engine would
    book the brain's 14 GB twice and leave a render 7 GB short.
    """
    rows = {"brain": _row(14336.0)}
    budget = build_memory_budget(
        sample=_dedicated_sample(vram_total_mb=24576.0, vram_used_mb=15360.0),
        gpu_capable=True,
        per_slot=rows,
        gpu_slots=frozenset({"brain", "img"}),
        img_slot="img",
        image_engine_mb=15360.0,  # the device-wide figure
        ram=RAM,
    )
    gpu = _gpu(budget)

    assert gpu.kind == "dedicated"
    assert gpu.image_engine_gb == 0  # no img row to borrow: the engine is not resident
    assert gpu.other_guests_gb == 1.0
    assert gpu.attribution_excess_gb == 0
    assert budget.render_free_gb == 21.0  # 24 - 1 - 0 - 2, not 24 - 1 - 15 - 2 = 6


def test_dedicated_card_takes_the_img_slots_row_for_the_engine() -> None:
    rows = {"brain": _row(14336.0), "img": _row(4096.0)}
    budget = build_memory_budget(
        sample=_dedicated_sample(vram_total_mb=24576.0, vram_used_mb=19456.0),
        gpu_capable=True,
        per_slot=rows,
        gpu_slots=frozenset(rows),
        img_slot="img",
        image_engine_mb=19456.0,  # device-wide, ignored
        ram=RAM,
    )
    gpu = _gpu(budget)

    assert gpu.image_engine_gb == 4.0
    assert gpu.image_engine_estimated is True
    assert gpu.other_guests_gb == 1.0  # 19 used - 14 brain - 4 engine
    assert budget.render_free_gb == 17.0  # 24 - 1 - 4 - 2: the 14 GB brain is unloaded first


def test_image_slot_without_a_row_and_no_view_is_zero() -> None:
    rows = {k: v for k, v in REFERENCE_ROWS.items() if k != "img"}
    gpu = _gpu(_reference_budget(per_slot=rows))

    assert gpu.image_engine_gb == 0


def test_no_image_slot_means_every_gpu_row_is_this_box() -> None:
    gpu = _gpu(_reference_budget(img_slot=None, gpu_slots=frozenset(REFERENCE_ROWS)))

    assert {s.slot for s in gpu.this_box} == {"utility", "tts", "brain", "img"}
    assert gpu.image_engine_gb == 0


def test_starting_slot_reduces_render_free_not_other_guests() -> None:
    rows = {
        "utility": _row(10240.0),  # 10.0 GB, resident
        "brain": _row(5120.0, state="starting"),  # 5.0 GB, still loading
    }
    sample = _shared_sample(gtt_used_mb=20480.0)  # 20.0 GB used
    budget = build_memory_budget(
        sample=sample,
        gpu_capable=True,
        per_slot=rows,
        gpu_slots=frozenset(rows),
        img_slot=None,
        image_engine_mb=None,
        ram=RAM,
    )
    gpu = _gpu(budget)

    # The starting slot is excluded from the resident sum, so the 10 GB of
    # unattributed usage is a guest's, not a loading slot's.
    assert {s.slot: s.state for s in gpu.this_box} == {
        "utility": "ready",
        "brain": "starting",
    }
    assert gpu.other_guests_gb == 10.0  # 20 - 10 resident
    assert gpu.attribution_excess_gb == 0
    # 116 - 10 guests - 0 engine - 5 starting - 2 margin; the resident
    # utility slot is not subtracted (image mode unloads it).
    assert budget.render_free_gb == 99.0


def test_starting_slot_does_not_count_toward_attribution_excess() -> None:
    rows = {"brain": _row(51200.0, state="starting")}  # 50 GB loading
    budget = build_memory_budget(
        sample=_shared_sample(gtt_used_mb=10240.0),  # 10 GB used so far
        gpu_capable=True,
        per_slot=rows,
        gpu_slots=frozenset(rows),
        img_slot=None,
        image_engine_mb=None,
        ram=RAM,
    )
    gpu = _gpu(budget)

    assert gpu.attribution_excess_gb == 0
    assert gpu.other_guests_gb == 10.0
    assert budget.render_free_gb == 54.0  # 116 - 10 - 0 - 50 - 2


def test_dedicated_gpu_uses_vram_fields() -> None:
    budget = build_memory_budget(
        sample=_dedicated_sample(),
        gpu_capable=True,
        per_slot={},
        gpu_slots=frozenset(),
        img_slot=None,
        image_engine_mb=None,
        ram=RAM,
    )
    gpu = _gpu(budget)

    assert gpu.kind == "dedicated"
    assert gpu.total_gb == 24.0
    assert gpu.used_gb == 8.0
    assert gpu.free_gb == 16.0
    assert gpu.other_guests_gb == 8.0
    assert budget.render_free_gb == 14.0  # 24 - 8 - 0 - 2


def test_render_free_never_goes_negative() -> None:
    budget = build_memory_budget(
        sample=_dedicated_sample(vram_total_mb=4096.0, vram_used_mb=4000.0),
        gpu_capable=True,
        per_slot={},
        gpu_slots=frozenset(),
        img_slot=None,
        image_engine_mb=None,
        ram=RAM,
    )

    assert budget.render_free_gb == 0
    assert _gpu(budget).free_gb == 0.1


def test_free_never_goes_negative_when_used_exceeds_total() -> None:
    budget = _reference_budget(sample=_shared_sample(gtt_used_mb=120000.0))

    assert _gpu(budget).free_gb == 0


def test_no_gpu_lane_has_no_gpu_block() -> None:
    budget = _reference_budget(gpu_capable=False)

    assert budget.gpu is None
    assert budget.render_free_gb is None
    assert budget.ram is RAM


def test_gpu_capable_without_a_sample_has_no_gpu_block() -> None:
    budget = _reference_budget(sample=None)

    assert budget.gpu is None
    assert budget.render_free_gb is None


@pytest.mark.parametrize(
    "sample",
    [
        _shared_sample(gtt_total_mb=None),
        _dedicated_sample(vram_total_mb=None),
    ],
    ids=["shared-no-gtt-total", "dedicated-no-vram-total"],
)
def test_sample_without_a_total_for_its_kind_has_no_gpu_block(
    sample: GPUMemorySample,
) -> None:
    budget = _reference_budget(sample=sample)

    assert budget.gpu is None
    assert budget.render_free_gb is None


@pytest.mark.parametrize(
    "sample",
    [
        _shared_sample(gtt_used_mb=None),
        _dedicated_sample(vram_used_mb=None),
    ],
    ids=["shared-no-gtt-used", "dedicated-no-vram-used"],
)
def test_missing_used_counter_has_no_gpu_block(sample: GPUMemorySample) -> None:
    """A known total with no ``used`` reading must not read as 0 used.

    That would publish a headroom figure nothing measured.
    """
    budget = _reference_budget(sample=sample)

    assert budget.gpu is None
    assert budget.render_free_gb is None
    assert budget.ram is RAM


def test_shared_pool_ignores_the_vram_carveout() -> None:
    """On UMA the 512 MiB VRAM carve-out must not leak into the figures."""
    gpu = _gpu(_reference_budget())

    assert gpu.total_gb == 116.0  # not 0.5, not max-pooled


@pytest.mark.parametrize(
    ("sample", "kind", "total_gb", "used_gb"),
    [
        # ``total_mb`` / ``used_mb`` are the max-pooled counters across both
        # pools; they must never stand in for the pool's own.
        (
            dataclasses.replace(_shared_sample(), total_mb=200000.0, used_mb=90000.0),
            "shared",
            116.0,
            43.4,
        ),
        (
            dataclasses.replace(_dedicated_sample(), total_mb=200000.0, used_mb=90000.0),
            "dedicated",
            24.0,
            8.0,
        ),
    ],
    ids=["shared", "dedicated"],
)
def test_pool_comes_from_its_own_counters_not_the_max_pooled_ones(
    sample: GPUMemorySample, kind: str, total_gb: float, used_gb: float
) -> None:
    gpu = _gpu(_reference_budget(sample=sample, per_slot={}, gpu_slots=frozenset()))

    assert (gpu.kind, gpu.total_gb, gpu.used_gb) == (kind, total_gb, used_gb)


def test_non_gpu_slots_are_not_in_the_pool() -> None:
    rows = {**REFERENCE_ROWS, "npu": _row(8192.0, model_id="flm-model")}
    gpu = _gpu(_reference_budget(per_slot=rows))  # gpu_slots excludes "npu"

    assert "npu" not in {s.slot for s in gpu.this_box}
    # ...and its footprint does not feed the attribution maths either.
    assert gpu.attribution_excess_gb == 3.0
    assert gpu.other_guests_gb == 0


def test_gpu_slot_with_no_resident_row_is_absent() -> None:
    """build_per_slot omits non-resident slots; a configured-but-idle slot is not listed."""
    gpu = _gpu(_reference_budget(gpu_slots=REFERENCE_GPU_SLOTS | {"idle"}))

    assert "idle" not in {s.slot for s in gpu.this_box}


def test_row_missing_vram_counts_as_zero() -> None:
    rows = {
        "utility": {"state": "ready", "model_id": "m"},
        "tts": {"vram_mb": None, "state": "ready"},
    }
    gpu = _gpu(
        _reference_budget(
            per_slot=rows,
            gpu_slots=frozenset(rows),
            img_slot=None,
        )
    )

    assert {s.slot: s.gb for s in gpu.this_box} == {"utility": 0, "tts": 0}
    assert gpu.other_guests_gb == 43.4


def test_this_box_is_ordered_by_slot_name() -> None:
    gpu = _gpu(_reference_budget())

    assert [s.slot for s in gpu.this_box] == ["brain", "tts", "utility"]


def test_as_dict_shape() -> None:
    d = _reference_budget().as_dict()

    assert set(d) == {f.name for f in dataclasses.fields(MemoryBudget)}
    assert set(d["gpu"]) == {f.name for f in dataclasses.fields(GpuBudget)}
    assert set(d["ram"]) == {f.name for f in dataclasses.fields(RamBudget)}
    assert d["gpu"]["this_box"], "reference box has this-box slots"
    for share in d["gpu"]["this_box"]:
        assert set(share) == {f.name for f in dataclasses.fields(SlotShare)}
    assert d["ram"] == {
        "limit_gb": 64.0,
        "used_gb": 20.0,
        "free_gb": 44.0,
        "source": "container",
    }
    assert d["render_free_gb"] == 107.4
    # The engine is named by the backend, and flagged when it is the img slot's
    # own estimate (this reference box has no ComfyUI reading).
    assert d["gpu"]["image_engine_slot"] == "img"
    assert d["gpu"]["image_engine_estimated"] is True


def test_as_dict_is_json_native() -> None:
    import json

    json.dumps(_reference_budget().as_dict())
    assert isinstance(_reference_budget().as_dict()["gpu"]["this_box"], list)


def test_as_dict_without_a_gpu_lane() -> None:
    d = _reference_budget(gpu_capable=False).as_dict()

    assert d["gpu"] is None
    assert d["render_free_gb"] is None
    assert set(d) == {"gpu", "ram", "render_free_gb"}


def test_dataclasses_are_frozen_and_slotted() -> None:
    gpu = _gpu(_reference_budget())

    for obj in (gpu, _reference_budget(), RAM, gpu.this_box[0]):
        assert not hasattr(obj, "__dict__")
        with pytest.raises(dataclasses.FrozenInstanceError):
            obj.total_gb = 1.0  # type: ignore[attr-defined]


# ── Live RAM limit ───────────────────────────────────────────────────────────
# ``read_ram`` / ``cgroup_limit_mb`` take a filesystem root, so each test builds
# the files it needs under ``tmp_path`` -- /proc/meminfo, /proc/self/cgroup, the
# cgroup tree and /run/systemd/container.

_GIB = 1024**3
_V2_LEAF = "system.slice/hal0-api.service"


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _meminfo(root: Path, *, total_kb: int, available_kb: int) -> None:
    _write(
        root,
        "proc/meminfo",
        f"MemTotal:       {total_kb} kB\n"
        "MemFree:         1234567 kB\n"
        f"MemAvailable:   {available_kb} kB\n"
        "Buffers:          123456 kB\n",
    )


def _v2_chain(root: Path, *, leaf: str = "max", slice_: str = "max", top: str = "max") -> None:
    """A cgroup-v2 process in ``system.slice/hal0-api.service`` and its three limits."""
    _write(root, "proc/self/cgroup", f"0::/{_V2_LEAF}\n")
    _write(root, f"sys/fs/cgroup/{_V2_LEAF}/memory.max", f"{leaf}\n")
    _write(root, "sys/fs/cgroup/system.slice/memory.max", f"{slice_}\n")
    _write(root, "sys/fs/cgroup/memory.max", f"{top}\n")


def test_lxc_guest_meminfo_is_the_limit(tmp_path: Path) -> None:
    # Inside an LXC guest every visible memory.max reads "max": the guest's limit
    # sits above the namespace root, and only lxcfs's MemTotal shows it.
    _meminfo(tmp_path, total_kb=50331648, available_kb=48069259)
    _v2_chain(tmp_path)
    _write(tmp_path, "run/systemd/container", "lxc\n")

    ram = read_ram(tmp_path)

    assert ram == RamBudget(limit_gb=48.0, used_gb=2.2, free_gb=45.8, source="container")


def test_slice_limit_below_meminfo_wins(tmp_path: Path) -> None:
    _meminfo(tmp_path, total_kb=64 * 1024 * 1024, available_kb=57 * 1024 * 1024)
    _v2_chain(tmp_path, slice_=str(20 * _GIB))

    ram = read_ram(tmp_path)

    assert ram.limit_gb == 20.0
    assert ram.used_gb == 7.0
    assert ram.free_gb == 13.0
    # No container marker, but a finite limit up the chain is a container limit.
    assert ram.source == "container"


def test_bare_metal_reads_system(tmp_path: Path) -> None:
    _meminfo(tmp_path, total_kb=64 * 1024 * 1024, available_kb=40 * 1024 * 1024)
    _v2_chain(tmp_path)

    ram = read_ram(tmp_path)

    assert ram == RamBudget(limit_gb=64.0, used_gb=24.0, free_gb=40.0, source="system")


def test_v1_sentinel_is_unlimited(tmp_path: Path) -> None:
    _meminfo(tmp_path, total_kb=32 * 1024 * 1024, available_kb=30 * 1024 * 1024)
    _write(tmp_path, "proc/self/cgroup", "11:memory:/user.slice\n3:cpu,cpuacct:/user.slice\n")
    # The kernel's v1 "no limit" is a huge page-aligned number, not a literal.
    _write(
        tmp_path, "sys/fs/cgroup/memory/user.slice/memory.limit_in_bytes", "9223372036854771712\n"
    )
    _write(tmp_path, "sys/fs/cgroup/memory/memory.limit_in_bytes", "9223372036854775807\n")

    assert cgroup_limit_mb(tmp_path) is None
    ram = read_ram(tmp_path)
    assert ram.limit_gb == 32.0
    assert ram.source == "system"


def test_v1_finite_limit_up_the_chain_wins(tmp_path: Path) -> None:
    _meminfo(tmp_path, total_kb=32 * 1024 * 1024, available_kb=30 * 1024 * 1024)
    _write(tmp_path, "proc/self/cgroup", "11:memory:/lxc/ct/app\n")
    _write(
        tmp_path, "sys/fs/cgroup/memory/lxc/ct/app/memory.limit_in_bytes", "9223372036854771712\n"
    )
    _write(tmp_path, "sys/fs/cgroup/memory/lxc/ct/memory.limit_in_bytes", str(8 * _GIB))
    _write(tmp_path, "sys/fs/cgroup/memory/memory.limit_in_bytes", "9223372036854771712\n")

    ram = read_ram(tmp_path)

    assert ram.limit_gb == 8.0
    assert ram.source == "container"


def test_missing_meminfo_returns_zeros_not_raise(tmp_path: Path) -> None:
    assert read_ram(tmp_path) == RamBudget(limit_gb=0.0, used_gb=0.0, free_gb=0.0, source="system")
    assert read_ram(tmp_path / "does-not-exist").limit_gb == 0.0


def test_malformed_meminfo_returns_zeros_not_raise(tmp_path: Path) -> None:
    _write(tmp_path, "proc/meminfo", "MemTotal: lots kB\nMemAvailable:\n")

    ram = read_ram(tmp_path)

    assert (ram.limit_gb, ram.used_gb, ram.free_gb) == (0.0, 0.0, 0.0)


def test_available_above_total_is_zero_used(tmp_path: Path) -> None:
    _meminfo(tmp_path, total_kb=8 * 1024 * 1024, available_kb=9 * 1024 * 1024)

    ram = read_ram(tmp_path)

    assert (ram.limit_gb, ram.used_gb, ram.free_gb) == (8.0, 0.0, 8.0)


def test_used_above_the_limit_leaves_no_free(tmp_path: Path) -> None:
    # MemAvailable is system-wide, so a small slice limit can sit below `used`.
    _meminfo(tmp_path, total_kb=64 * 1024 * 1024, available_kb=32 * 1024 * 1024)
    _v2_chain(tmp_path, slice_=str(16 * _GIB))

    ram = read_ram(tmp_path)

    assert (ram.limit_gb, ram.used_gb, ram.free_gb) == (16.0, 32.0, 0.0)


def test_tightest_limit_on_the_chain_wins(tmp_path: Path) -> None:
    _v2_chain(tmp_path, leaf=str(30 * _GIB), slice_=str(20 * _GIB), top=str(40 * _GIB))

    assert cgroup_limit_mb(tmp_path) == 20 * 1024.0


def test_unusable_limit_files_are_ignored(tmp_path: Path) -> None:
    _write(tmp_path, "proc/self/cgroup", f"0::/{_V2_LEAF}\n")
    _write(tmp_path, f"sys/fs/cgroup/{_V2_LEAF}/memory.max", "")  # empty
    _write(tmp_path, "sys/fs/cgroup/system.slice/memory.max", "not a number\n")
    _write(tmp_path, "sys/fs/cgroup/memory.max", str(2**60))  # at the unlimited cut-off
    # Unreadable: a directory where a v1 limit file should be.
    (tmp_path / "sys/fs/cgroup/memory/memory.limit_in_bytes").mkdir(parents=True)

    assert cgroup_limit_mb(tmp_path) is None


def test_limit_just_below_the_cutoff_is_finite(tmp_path: Path) -> None:
    _write(tmp_path, "sys/fs/cgroup/memory.max", str(2**60 - 4096))

    assert cgroup_limit_mb(tmp_path) == (2**60 - 4096) / 1024**2


def test_no_cgroup_listing_reads_only_the_root_files(tmp_path: Path) -> None:
    _write(tmp_path, "sys/fs/cgroup/memory.max", str(4 * _GIB))
    # Present, but nothing says the process lives there: not part of any chain.
    _write(tmp_path, f"sys/fs/cgroup/{_V2_LEAF}/memory.max", str(1 * _GIB))

    assert cgroup_limit_mb(tmp_path) == 4096.0


def test_v1_root_file_is_read_without_a_cgroup_listing(tmp_path: Path) -> None:
    _write(tmp_path, "sys/fs/cgroup/memory/memory.limit_in_bytes", str(8 * _GIB))

    assert cgroup_limit_mb(tmp_path) == 8192.0


def test_dotdot_in_the_cgroup_path_cannot_leave_the_cgroup_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    _write(root, "proc/self/cgroup", "0::/../../outside\n")
    _write(root, "sys/outside/memory.max", str(1 * _GIB))  # what ../../outside would reach

    assert cgroup_limit_mb(root) is None


def test_cgroup_limit_never_raises_on_a_missing_root(tmp_path: Path) -> None:
    assert cgroup_limit_mb(tmp_path / "nope") is None


# ── The collector: gather the live inputs, hand them to build_memory_budget ──────


class _Stats:
    """``app.state.hardware_stats`` stand-in; records the thread it was read on."""

    def __init__(self, sample: GPUMemorySample | None) -> None:
        self.sample = sample
        self.thread: int | None = None

    def gpu_sample(self) -> GPUMemorySample | None:
        self.thread = threading.get_ident()
        return self.sample


class _Manager:
    """``app.state.slot_manager`` stand-in: ``list()`` and ``iter_configs()``."""

    def __init__(self, slots: list[Any], configs: list[dict[str, Any]]) -> None:
        self._slots = slots
        self._configs = configs

    async def list(self) -> list[Any]:
        return list(self._slots)

    async def iter_configs(self) -> list[dict[str, Any]]:
        return list(self._configs)


def _slot(name: str, state: str = "ready", model_id: str | None = "m") -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        state=state,
        model_id=model_id,
        backend="rocm",
        slot_id=None,
        metadata={"provider": "llama-server", "backend": "rocm"},
    )


class _Registry:
    """Every model is ``size_gib`` GiB on disk."""

    def __init__(self, size_gib: int = 4) -> None:
        self._size = size_gib * 1024**3

    def get(self, model_id: str) -> SimpleNamespace:
        return SimpleNamespace(size_bytes=self._size)


def _app(**state: Any) -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(**state))


@pytest.fixture
def collector_env(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Pin everything the collector reads outside ``app.state``: a GPU box with
    a fixed RAM reading, no cgroup probe, and a ComfyUI that does not answer."""
    from unittest.mock import AsyncMock

    env = SimpleNamespace(ram_thread=None, engine_calls=0, engine_mb=None)

    def fake_read_ram() -> RamBudget:
        env.ram_thread = threading.get_ident()
        return RAM

    async def fake_engine() -> float | None:
        env.engine_calls += 1
        return env.engine_mb

    monkeypatch.setattr(memory_source, "read_ram", fake_read_ram)
    monkeypatch.setattr("hal0.slots.capacity._host_has_capable_gpu", lambda: True)
    monkeypatch.setattr(
        "hal0.slots.capacity._container_cgroup_mem_bytes", AsyncMock(return_value=0)
    )
    monkeypatch.setattr("hal0.api.routes.comfyui.engine_used_mb", fake_engine)
    return env


async def test_collect_with_nothing_wired_still_returns_a_budget(
    collector_env: SimpleNamespace,
) -> None:
    budget = await collect_memory_budget(_app())

    assert budget.gpu is None
    assert budget.render_free_gb is None
    assert budget.ram == RAM


async def test_collect_without_a_slot_manager_has_an_empty_pool_attribution(
    collector_env: SimpleNamespace,
) -> None:
    budget = await collect_memory_budget(_app(hardware_stats=_Stats(_shared_sample())))

    gpu = _gpu(budget)
    assert gpu.this_box == ()
    assert gpu.other_guests_gb == gpu.used_gb  # nothing to attribute it to


async def test_collect_survives_a_broken_slot_manager(collector_env: SimpleNamespace) -> None:
    class Broken:
        async def list(self) -> list[Any]:
            raise RuntimeError("boom")

        async def iter_configs(self) -> list[dict[str, Any]]:
            raise RuntimeError("boom")

    budget = await collect_memory_budget(
        _app(hardware_stats=_Stats(_shared_sample()), slot_manager=Broken())
    )

    assert _gpu(budget).this_box == ()


async def test_collect_survives_a_failing_gpu_sample(collector_env: SimpleNamespace) -> None:
    class Failing:
        def gpu_sample(self) -> GPUMemorySample:
            raise OSError("sysfs gone")

    budget = await collect_memory_budget(_app(hardware_stats=Failing()))

    assert budget.gpu is None
    assert budget.ram == RAM


async def test_collect_derives_gpu_slots_and_the_img_slot_from_the_configs(
    collector_env: SimpleNamespace,
) -> None:
    configs = [
        {"name": "brain", "device": "gpu-vulkan"},
        {"name": "fallback"},  # no device: gpu_exclusive_group defaults it to gpu-rocm
        {"name": "npu-chat", "device": "npu"},
        {"name": "utility", "device": "cpu"},
        {"name": "img", "device": "gpu-rocm", "provider": "comfyui", "type": "image"},
    ]
    slots = [_slot(c["name"]) for c in configs]
    budget = await collect_memory_budget(
        _app(
            hardware_stats=_Stats(_shared_sample()),
            slot_manager=_Manager(slots, configs),
            model_registry=_Registry(size_gib=4),
        )
    )

    gpu = _gpu(budget)
    assert [s.slot for s in gpu.this_box] == ["brain", "fallback"]
    # The img slot's row stands in for the engine while ComfyUI does not answer.
    assert gpu.image_engine_gb == 4.0
    assert (gpu.image_engine_slot, gpu.image_engine_estimated) == ("img", True)


async def test_collect_prefers_comfyuis_own_figure_over_the_img_row(
    collector_env: SimpleNamespace,
) -> None:
    collector_env.engine_mb = 1024.0
    configs = [{"name": "img", "device": "gpu-rocm", "provider": "comfyui", "type": "image"}]
    budget = await collect_memory_budget(
        _app(
            hardware_stats=_Stats(_shared_sample()),
            slot_manager=_Manager([_slot("img")], configs),
            model_registry=_Registry(size_gib=6),
        )
    )

    assert _gpu(budget).image_engine_gb == 1.0


async def test_collect_lists_a_starting_gpu_slot(collector_env: SimpleNamespace) -> None:
    configs = [{"name": "brain", "device": "gpu-rocm"}]
    budget = await collect_memory_budget(
        _app(
            hardware_stats=_Stats(_shared_sample()),
            slot_manager=_Manager([_slot("brain", state="starting")], configs),
            model_registry=_Registry(size_gib=8),
        )
    )

    (share,) = _gpu(budget).this_box
    assert (share.slot, share.state) == ("brain", "starting")
    assert share.gb == 8.0


async def test_collect_reuses_rows_the_caller_already_built(
    collector_env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def must_not_run(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise AssertionError("build_per_slot ran although rows were passed in")

    monkeypatch.setattr("hal0.slots.capacity.build_per_slot", must_not_run)
    manager = _Manager([], [{"name": "brain", "device": "gpu-rocm"}])
    budget = await collect_memory_budget(
        _app(hardware_stats=_Stats(_shared_sample()), slot_manager=manager),
        per_slot={"brain": _row(2048.0)},
    )

    (share,) = _gpu(budget).this_box
    assert (share.slot, share.gb) == ("brain", 2.0)


async def test_collect_reads_ram_and_the_gpu_sample_off_the_loop(
    collector_env: SimpleNamespace,
) -> None:
    stats = _Stats(_shared_sample())
    await collect_memory_budget(_app(hardware_stats=stats))

    loop_thread = threading.get_ident()
    assert collector_env.ram_thread not in (None, loop_thread)
    assert stats.thread not in (None, loop_thread)


async def test_collect_skips_the_engine_when_there_is_no_gpu_pool(
    collector_env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("hal0.slots.capacity._host_has_capable_gpu", lambda: False)

    budget = await collect_memory_budget(_app(hardware_stats=_Stats(_shared_sample())))

    assert budget.gpu is None
    assert collector_env.engine_calls == 0


async def test_collect_skips_the_engine_when_there_is_no_img_slot(
    collector_env: SimpleNamespace,
) -> None:
    """With no img slot there is nothing to attribute ComfyUI's figure to."""
    configs = [{"name": "brain", "device": "gpu-rocm"}]

    budget = await collect_memory_budget(
        _app(
            hardware_stats=_Stats(_shared_sample()),
            slot_manager=_Manager([_slot("brain")], configs),
            model_registry=_Registry(size_gib=4),
        )
    )

    assert collector_env.engine_calls == 0
    assert _gpu(budget).image_engine_slot is None


async def test_collect_skips_the_engine_on_a_dedicated_card(
    collector_env: SimpleNamespace,
) -> None:
    """ComfyUI's figure is device-wide on a discrete card; the budget never uses it."""
    configs = [{"name": "img", "device": "gpu-rocm", "provider": "comfyui", "type": "image"}]
    collector_env.engine_mb = 99999.0

    budget = await collect_memory_budget(
        _app(
            hardware_stats=_Stats(_dedicated_sample()),
            slot_manager=_Manager([_slot("img")], configs),
            model_registry=_Registry(size_gib=6),
        )
    )

    assert collector_env.engine_calls == 0
    assert _gpu(budget).image_engine_gb == 6.0  # the img row


async def test_collect_takes_the_capability_the_caller_already_resolved(
    collector_env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    def must_not_probe() -> bool:
        raise AssertionError("the GPU capability was probed again")

    monkeypatch.setattr("hal0.slots.capacity._host_has_capable_gpu", must_not_probe)
    app = _app(hardware_stats=_Stats(_shared_sample()))

    capable = await collect_memory_budget(app, gpu_capable=True)
    incapable = await collect_memory_budget(app, gpu_capable=False)

    assert capable.gpu is not None
    assert incapable.gpu is None


async def test_collect_does_not_wait_for_a_hung_engine(
    collector_env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def hung() -> float | None:
        await asyncio.sleep(3)
        return 99999.0

    monkeypatch.setattr("hal0.api.routes.comfyui.engine_used_mb", hung)
    configs = [{"name": "img", "device": "gpu-rocm", "provider": "comfyui", "type": "image"}]
    app = _app(
        hardware_stats=_Stats(_shared_sample()),
        slot_manager=_Manager([_slot("img")], configs),
        model_registry=_Registry(size_gib=6),
    )

    started = time.perf_counter()
    budget = await collect_memory_budget(app)

    assert time.perf_counter() - started < 1.5
    assert _gpu(budget).image_engine_gb == 6.0  # the img row, not the hung read


async def test_collect_survives_a_raising_engine_read(
    collector_env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom() -> float | None:
        raise RuntimeError("boom")

    monkeypatch.setattr("hal0.api.routes.comfyui.engine_used_mb", boom)
    configs = [{"name": "img", "device": "gpu-rocm", "provider": "comfyui", "type": "image"}]
    budget = await collect_memory_budget(
        _app(
            hardware_stats=_Stats(_shared_sample()),
            slot_manager=_Manager([_slot("img")], configs),
            model_registry=_Registry(size_gib=6),
        )
    )

    assert _gpu(budget).image_engine_gb == 6.0
