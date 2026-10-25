"""Tests for hal0.hardware.memory_budget — the single owner of the dashboard's
memory figures (ADR-0039).

The model is pure: a GPU sample, per-slot attribution rows, ComfyUI's own
figure and a RAM reading go in, one ``MemoryBudget`` comes out. Fixtures are
in MiB (what the sample and ``build_per_slot`` carry); expectations are in GB
(MiB / 1024), rounded to one decimal.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from hal0.hardware.gpu_view import GPUMemorySample
from hal0.hardware.memory_budget import (
    RENDER_MARGIN_GB,
    GpuBudget,
    MemoryBudget,
    RamBudget,
    SlotShare,
    build_memory_budget,
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
