"""Tests for hal0.comfyui.fit -- the fit verdict of an image model at a size (ADR-0037, ADR-0039).

``need_for`` turns the catalog's measured memory facts into a GPU and a RAM need; ``assess``
compares that need with a ``MemoryBudget``. Both are pure. The budgets here are built the way
the memory-budget tests build theirs: Plan 4a's reference box (a 116 GB shared pool with this
box's four slots on it) and its capped guest (the same pool, 43.4 GB of it held by another
guest, and a 48 GB container limit on RAM).
"""

from __future__ import annotations

import dataclasses
import json
import re
from typing import Any

import pytest

from hal0.comfyui.catalog import CATALOG, ImageModel, weights_gib
from hal0.comfyui.fit import Fit, Need, assess, need_for
from hal0.hardware.gpu_view import GPUMemorySample
from hal0.hardware.memory_budget import MemoryBudget, RamBudget, build_memory_budget

SDXL = CATALOG["sdxl-lightning"]
SD15 = CATALOG["sd-1.5-pruned-emaonly"]
QWEN = CATALOG["qwen-image-lightning"]
QWEN_FULL = CATALOG["qwen-image"]

REFERENCE_RAM = RamBudget(limit_gb=105.5, used_gb=16.1, free_gb=89.4, source="container")
CAPPED_RAM = RamBudget(limit_gb=48.0, used_gb=2.2, free_gb=45.8, source="container")


def _sample(*, gtt_used_mb: float) -> GPUMemorySample:
    """A UMA part with a 116 GB pool (GTT), as in the memory-budget tests."""
    return GPUMemorySample(
        vendor="amd",
        is_uma=True,
        vram_total_mb=512.0,
        gtt_total_mb=118784.0,
        total_mb=118784.0,
        vram_used_mb=100.0,
        gtt_used_mb=gtt_used_mb,
        used_mb=gtt_used_mb,
        gpu_busy=0.1,
        util_is_forced_high=False,
    )


def _row(vram_mb: float) -> dict[str, Any]:
    return {
        "vram_mb": vram_mb,
        "ram_mb": 0.0,
        "mem_mb": vram_mb,
        "state": "ready",
        "model_id": "m",
    }


@pytest.fixture
def reference_box() -> MemoryBudget:
    """This box's four slots on the shared pool: 107.4 GB free for a render, 89.4 GB of RAM."""
    return build_memory_budget(
        sample=_sample(gtt_used_mb=44441.6),
        gpu_capable=True,
        per_slot={
            "utility": _row(31539.2),
            "tts": _row(6348.8),
            "brain": _row(2867.2),
            "img": _row(6758.4),
        },
        gpu_slots=frozenset({"utility", "tts", "brain", "img"}),
        img_slot="img",
        image_engine_mb=None,
        ram=REFERENCE_RAM,
    )


@pytest.fixture
def capped_guest() -> MemoryBudget:
    """Another guest holds 43.4 GB of the pool; this one has no slots and a 48 GB limit."""
    return build_memory_budget(
        sample=_sample(gtt_used_mb=44441.6),
        gpu_capable=True,
        per_slot={},
        gpu_slots=frozenset(),
        img_slot=None,
        image_engine_mb=None,
        ram=CAPPED_RAM,
    )


def _budget(*, render_free_gb: float, ram: RamBudget = REFERENCE_RAM) -> MemoryBudget:
    """A budget with exactly the figures a test wants, taken from the reference box."""
    reference = build_memory_budget(
        sample=_sample(gtt_used_mb=44441.6),
        gpu_capable=True,
        per_slot={},
        gpu_slots=frozenset(),
        img_slot=None,
        image_engine_mb=None,
        ram=ram,
    )
    return dataclasses.replace(reference, render_free_gb=render_free_gb)


def _need(gpu: float = 10.0, ram: float = 10.0) -> Need:
    return Need(gpu_gb=gpu, ram_gb=ram, gpu_estimated=False, ram_estimated=False)


# ── need_for: the GPU need ──────────────────────────────────────────────────


def test_exact_measured_size_is_not_estimated() -> None:
    need = need_for(SDXL, "1024x1024")
    assert need.gpu_gb == 15.9
    assert need.gpu_estimated is False

    larger = need_for(SDXL, "1536x1536")  # measured, though not one of the model's sizes
    assert (larger.gpu_gb, larger.gpu_estimated) == (28.7, False)

    sd15 = need_for(SD15, "512x512")
    assert (sd15.gpu_gb, sd15.gpu_estimated) == (11.6, False)


def test_no_size_means_the_native_size() -> None:
    assert need_for(SDXL, None) == need_for(SDXL, SDXL.sizes[0])
    assert need_for(SD15, None) == need_for(SD15, "512x512")
    assert need_for(SDXL, None).gpu_estimated is False


def test_custom_size_uses_nearest_larger_measured() -> None:
    # 1152x896 is 1,032,192 px, under the 1024x1024 measurement's 1,048,576.
    need = need_for(SDXL, "1152x896")
    assert need.gpu_gb == 15.9
    assert need.gpu_estimated is True

    # Between the two measurements: the larger one, never the smaller.
    between = need_for(SDXL, "1280x1280")  # 1,638,400 px
    assert (between.gpu_gb, between.gpu_estimated) == (28.7, True)

    # Under every measurement: the smallest entry still covers it.
    small = need_for(SDXL, "512x512")
    assert (small.gpu_gb, small.gpu_estimated) == (15.9, True)


def test_same_pixel_count_in_another_shape_is_covered_by_the_measurement() -> None:
    # 2048x512 has exactly the 1,048,576 px of 1024x1024, but is not the size that was measured.
    need = need_for(SDXL, "2048x512")
    assert (need.gpu_gb, need.gpu_estimated) == (15.9, True)


def test_size_above_table_scales_by_area() -> None:
    need = need_for(SDXL, "2048x2048")
    # 28.7 GB at 1536x1536, and 2048x2048 has 4194304 / 2359296 times the pixels.
    assert need.gpu_gb == pytest.approx(28.7 * 4194304 / 2359296, abs=0.05)
    assert need.gpu_gb == 51.0
    assert need.gpu_estimated is True


def test_size_is_read_as_numbers_not_text() -> None:
    assert need_for(SDXL, "01024x1024") == need_for(SDXL, "1024x1024")


def test_model_without_a_table_needs_its_weights() -> None:
    need = need_for(QWEN_FULL, None)
    assert need.gpu_gb == weights_gib(QWEN_FULL) == 47.1
    assert need.gpu_estimated is True
    # No table: every size reads the same weight total, still estimated.
    assert need_for(QWEN_FULL, "1664x928") == need


def test_model_with_a_table_but_no_native_size_needs_its_weights() -> None:
    odd = dataclasses.replace(SDXL, sizes=())
    need = need_for(odd, None)
    assert need.gpu_gb == weights_gib(odd)
    assert need.gpu_estimated is True
    # An explicit size still reads the table.
    assert need_for(odd, "1024x1024").gpu_gb == 15.9


def test_a_need_is_never_zero() -> None:
    for model in CATALOG.values():
        need = need_for(model, None)
        assert need.gpu_gb > 0, model.id
        assert need.ram_gb > 0, model.id


def test_every_measured_size_key_is_a_width_by_height() -> None:
    """``need_for`` reads the table's keys as sizes; a malformed key would fail every row."""
    for model in CATALOG.values():
        for key in model.gpu_peak_gb:
            assert re.fullmatch(r"[0-9]+x[0-9]+", key), (model.id, key)


# ── need_for: the RAM need ──────────────────────────────────────────────────


def test_unmeasured_ram_need_is_the_weights_and_estimated() -> None:
    need = need_for(SDXL, None)
    assert need.ram_gb == weights_gib(SDXL) == 7.1
    assert need.ram_estimated is True


def test_measured_ram_need_is_a_lower_bound_never_below_the_weights() -> None:
    # Qwen-Image Lightning: measured 45.5 GB at the OOM kill, weights 47.9 GB.
    need = need_for(QWEN, None)
    assert need.ram_gb == 47.9
    assert need.ram_estimated is False


def test_measured_ram_need_above_the_weights_wins() -> None:
    big = dataclasses.replace(QWEN, load_ram_gb=60.0)
    need = need_for(big, None)
    assert need.ram_gb == 60.0
    assert need.ram_estimated is False


def test_ram_need_does_not_depend_on_the_size() -> None:
    assert need_for(QWEN, "1664x928").ram_gb == need_for(QWEN, None).ram_gb


# ── assess: the verdict ─────────────────────────────────────────────────────


def test_qwen_wont_fit_on_capped_guest_by_ram(capped_guest: MemoryBudget) -> None:
    assert capped_guest.render_free_gb == 70.6  # the GPU side has room for 47.9

    fit = assess(need_for(QWEN, None), capped_guest)

    assert fit is not None
    assert fit.verdict == "wont_fit"
    assert fit.reason == "needs ~47.9 GB of RAM to load, 45.8 GB free (48.0 GB container limit)"
    assert fit.need == Need(gpu_gb=47.9, ram_gb=47.9, gpu_estimated=True, ram_estimated=False)
    assert fit.render_free_gb == 70.6
    assert fit.ram_free_gb == 45.8


def test_qwen_fits_on_reference_box(reference_box: MemoryBudget) -> None:
    fit = assess(need_for(QWEN, None), reference_box)

    assert fit is not None
    assert fit.verdict == "fits"
    assert fit.reason is None


def test_sdxl_fits_on_reference_box(reference_box: MemoryBudget) -> None:
    assert reference_box.render_free_gb == 107.4

    fit = assess(need_for(SDXL, None), reference_box)

    assert fit == Fit(
        verdict="fits",
        reason=None,
        need=Need(gpu_gb=15.9, ram_gb=7.1, gpu_estimated=False, ram_estimated=True),
        render_free_gb=107.4,
        ram_free_gb=89.4,
    )


def test_sdxl_fits_on_capped_guest(capped_guest: MemoryBudget) -> None:
    fit = assess(need_for(SDXL, None), capped_guest)
    assert fit is not None
    assert fit.verdict == "fits"


@pytest.mark.parametrize(
    ("render_free_gb", "verdict"),
    [
        (15.8, "wont_fit"),  # the need is above what is free
        (15.9, "tight"),  # it fits, with nothing to spare
        (17.6, "tight"),  # 0.9 * 17.6 = 15.84, below the need
        (17.7, "fits"),  # 0.9 * 17.7 = 15.93, above the need
        (107.4, "fits"),
    ],
)
def test_tight_above_ninety_percent(render_free_gb: float, verdict: str) -> None:
    """Tight means the need exceeds 0.9 x what is free."""
    fit = assess(need_for(SDXL, None), _budget(render_free_gb=render_free_gb))

    assert fit is not None
    assert fit.verdict == verdict


@pytest.mark.parametrize(
    ("ram_free_gb", "verdict"),
    [(9.9, "wont_fit"), (10.0, "tight"), (11.1, "tight"), (11.2, "fits")],
)
def test_ram_is_judged_by_the_same_rule(ram_free_gb: float, verdict: str) -> None:
    ram = RamBudget(limit_gb=64.0, used_gb=0.0, free_gb=ram_free_gb, source="container")

    fit = assess(_need(gpu=1.0, ram=10.0), _budget(render_free_gb=100.0, ram=ram))

    assert fit is not None
    assert fit.verdict == verdict
    assert (fit.reason is None) == (verdict == "fits")


@pytest.mark.parametrize(
    ("need_gb", "verdict"),
    [(9.0, "fits"), (9.1, "tight"), (10.0, "tight"), (10.1, "wont_fit")],
)
def test_ninety_percent_itself_still_fits(need_gb: float, verdict: str) -> None:
    """With 10.0 GB free, the line between fits and tight is 9.0 GB, and 9.0 is not above it."""
    fit = assess(_need(gpu=need_gb, ram=1.0), _budget(render_free_gb=10.0))

    assert fit is not None
    assert fit.verdict == verdict


def test_gpu_reason_names_the_need_and_the_room_for_a_render() -> None:
    fit = assess(need_for(SDXL, "1536x1536"), _budget(render_free_gb=20.0))

    assert fit is not None
    assert fit.verdict == "wont_fit"
    assert fit.reason == "needs 28.7 GB of GPU memory, 20.0 GB free for a render"


def test_tight_carries_the_same_sentence_as_a_refusal() -> None:
    fit = assess(need_for(SDXL, None), _budget(render_free_gb=17.0))

    assert fit is not None
    assert fit.verdict == "tight"
    assert fit.reason == "needs 15.9 GB of GPU memory, 17.0 GB free for a render"


def test_ram_reason_labels_a_system_limit() -> None:
    ram = RamBudget(limit_gb=64.0, used_gb=50.0, free_gb=14.0, source="system")

    fit = assess(need_for(QWEN, None), _budget(render_free_gb=100.0, ram=ram))

    assert fit is not None
    assert fit.reason == "needs ~47.9 GB of RAM to load, 14.0 GB free (64.0 GB system)"


def test_the_worst_dimension_decides_and_the_gpu_is_named_first() -> None:
    ram = RamBudget(limit_gb=64.0, used_gb=60.0, free_gb=4.0, source="container")

    # Tight on the GPU, refused on RAM: the refusal wins.
    fit = assess(_need(gpu=9.5, ram=10.0), _budget(render_free_gb=10.0, ram=ram))
    assert fit is not None
    assert fit.verdict == "wont_fit"
    assert fit.reason is not None and "RAM" in fit.reason

    # Refused on both: the GPU is reported.
    fit = assess(_need(gpu=12.0, ram=10.0), _budget(render_free_gb=10.0, ram=ram))
    assert fit is not None
    assert fit.verdict == "wont_fit"
    assert fit.reason is not None and "GPU" in fit.reason


def test_an_estimated_need_is_judged_like_a_measured_one() -> None:
    """Weight sums and measured values are lower bounds, so a refusal built on them stands."""
    need = Need(gpu_gb=47.9, ram_gb=1.0, gpu_estimated=True, ram_estimated=True)

    fit = assess(need, _budget(render_free_gb=30.0))

    assert fit is not None
    assert fit.verdict == "wont_fit"
    assert fit.need.gpu_estimated is True


# ── assess: no budget ───────────────────────────────────────────────────────


def test_fit_is_null_without_budget() -> None:
    need = need_for(SDXL, None)

    # No GPU lane on the box.
    no_gpu = build_memory_budget(
        sample=None,
        gpu_capable=False,
        per_slot={},
        gpu_slots=frozenset(),
        img_slot=None,
        image_engine_mb=None,
        ram=REFERENCE_RAM,
    )
    assert no_gpu.gpu is None
    assert assess(need, no_gpu) is None

    # A pool that was read, with no headroom figure.
    pool = _budget(render_free_gb=100.0)
    assert assess(need, dataclasses.replace(pool, render_free_gb=None)) is None
    assert assess(need, dataclasses.replace(pool, gpu=None)) is None


def test_unreadable_meminfo_is_unknown_not_wont_fit() -> None:
    """A RAM limit of 0 is "could not read", and 0 GB free must not refuse a render."""
    unreadable = RamBudget(limit_gb=0.0, used_gb=0.0, free_gb=0.0, source="system")

    assert assess(need_for(SDXL, None), _budget(render_free_gb=100.0, ram=unreadable)) is None


# ── the value objects ───────────────────────────────────────────────────────


def test_as_dict_is_json_native(capped_guest: MemoryBudget) -> None:
    fit = assess(need_for(QWEN, None), capped_guest)
    assert fit is not None

    assert fit.as_dict() == {
        "verdict": "wont_fit",
        "reason": "needs ~47.9 GB of RAM to load, 45.8 GB free (48.0 GB container limit)",
        "need": {"gpu_gb": 47.9, "ram_gb": 47.9, "gpu_estimated": True, "ram_estimated": False},
        "render_free_gb": 70.6,
        "ram_free_gb": 45.8,
    }
    assert json.loads(json.dumps(fit.as_dict())) == fit.as_dict()


def test_fit_and_need_are_immutable(reference_box: MemoryBudget) -> None:
    fit = assess(need_for(SDXL, None), reference_box)
    assert fit is not None

    # Assign a real field: a missing one fails differently on a slotted dataclass.
    with pytest.raises(dataclasses.FrozenInstanceError):
        fit.verdict = "wont_fit"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        fit.need.gpu_gb = 1.0  # type: ignore[misc]


def test_need_for_leaves_the_catalog_alone() -> None:
    before: dict[str, Any] = {
        m.id: (dict(m.gpu_peak_gb), m.load_ram_gb, m.sizes) for m in CATALOG.values()
    }
    for model in CATALOG.values():
        assert isinstance(model, ImageModel)
        need_for(model, None)
        need_for(model, "2048x2048")
    assert before == {m.id: (dict(m.gpu_peak_gb), m.load_ram_gb, m.sizes) for m in CATALOG.values()}
