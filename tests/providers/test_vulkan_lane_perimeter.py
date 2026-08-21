"""#1948 review B1/B2 — the paths to a Vulkan runner that do NOT pass the gate.

The slot-load image gate (``require_kfd_for_gpu_slot``) covers exactly one of
the ways a llama.cpp Vulkan runner gets started. Three others exist:

* **bench** — ``hal0 bench`` / ``hal0-tune`` run ``llama-bench`` under podman
  directly. They never touch the guard, and ``llama-bench`` measures tok/s
  without reading the tokens, so a sweep on a broken image publishes a
  throughput number for non-language.
* **install-time derivation** — ``derive_device`` / ``_backend_for`` /
  ``_detect_default_hardware`` decide what device a seeded or freshly-created
  slot gets. Handing them a device the guard will refuse turns "slow but
  working" into "no loadable slot at all".
* **the installer preflight** — covered in
  ``tests/installer/test_preflight_gpu_gate.py``.

All three are gated on the SAME question the slot-load guard asks — can the
default runner image serve this lane — so the whole feature composes in either
order with #1959's repin, with no window where the answers disagree.
"""

from __future__ import annotations

import os

import pytest

from hal0.providers._gpu import (
    default_image_serves_vulkan_lane,
    image_serves_vulkan_lane,
    render_node_present,
)

ADE07BA_REF = "ghcr.io/hal0ai/hal0-rocmfpx:ade07ba"


def _pin(monkeypatch, ref: str) -> None:
    """Point every default-image resolution at ``ref``.

    Uses ``resolve_runner_image``'s documented ``HAL0_TOOLBOX_IMAGE_<KEY>``
    seam rather than reaching into the registry — :class:`hal0.runners.Runner`
    is a frozen dataclass, and going through the real resolver means these
    tests exercise the same precedence chain a box does. Both llama-server GPU
    runner keys are pinned because ``rocmfpx`` and ``vulkanfpx`` resolve to the
    same image today and either can back a GPU lane.

    ``hal0.bench.harness`` imported ``DEFAULT_ROCMFPX_IMAGE`` by value at
    module load, so its ``LaneSpec`` table is pinned separately.
    """
    import hal0.bench.harness as harness_mod

    monkeypatch.setenv("HAL0_TOOLBOX_IMAGE_ROCMFPX", ref)
    monkeypatch.setenv("HAL0_TOOLBOX_IMAGE_VULKANFPX", ref)
    monkeypatch.setattr(harness_mod, "DEFAULT_ROCMFPX_IMAGE", ref)


@pytest.fixture
def broken_pin(monkeypatch):
    """main between #1973 and #1959: the default runner cannot serve Vulkan."""
    _pin(monkeypatch, ADE07BA_REF)
    assert default_image_serves_vulkan_lane() is False
    return ADE07BA_REF


@pytest.fixture
def fixed_pin(monkeypatch):
    """main after #1959: the default runner is Vulkan-validated."""
    from hal0.config.schema import VULKAN_FIXED_IMAGE

    _pin(monkeypatch, VULKAN_FIXED_IMAGE)
    assert default_image_serves_vulkan_lane() is True
    return VULKAN_FIXED_IMAGE


# ── N3: a render node must be a character device ────────────────────────── #


class TestRenderNodeIsACharacterDevice:
    """``resolve_gpu_device_paths`` forwards only character devices, so a
    regular file named ``renderD128`` (a stale bind target, a leftover) would
    pass preflight and then not be forwarded into the container at all."""

    def test_a_regular_file_named_like_a_render_node_does_not_count(self, tmp_path) -> None:
        dri = tmp_path / "dri"
        dri.mkdir()
        (dri / "renderD128").write_text("")
        assert render_node_present(str(dri)) is False

    def test_a_directory_named_like_a_render_node_does_not_count(self, tmp_path) -> None:
        dri = tmp_path / "dri"
        dri.mkdir()
        (dri / "renderD128").mkdir()
        assert render_node_present(str(dri)) is False

    @pytest.mark.skipif(not os.path.exists("/dev/null"), reason="needs a char device")
    def test_a_real_character_device_counts(self, tmp_path) -> None:
        """Symlink to /dev/null — a genuine character device, which is what a
        forwarded render node is."""
        dri = tmp_path / "dri"
        dri.mkdir()
        (dri / "renderD128").symlink_to("/dev/null")
        assert render_node_present(str(dri)) is True


# ── B1: the bench harness ───────────────────────────────────────────────── #


class TestBenchLaneIsImageGated:
    def test_vulkan_radv_is_unsupported_on_a_broken_pin(self, broken_pin) -> None:
        """The exact window the agreed land order opens: main carries #1973
        but not yet #1959. A GPU sweep must not silently publish throughput
        for a backend that emits non-language."""
        from hal0.bench.harness import lane_is_supported

        assert lane_is_supported("vulkan_radv") is False
        assert lane_is_supported("rocm") is True
        assert lane_is_supported("cpu") is True

    def test_vulkan_radv_is_supported_on_the_fixed_pin(self, fixed_pin) -> None:
        from hal0.bench.harness import lane_is_supported

        assert lane_is_supported("vulkan_radv") is True

    def test_default_lanes_drops_the_vulkan_lane_on_a_broken_pin(self, broken_pin) -> None:
        from hal0.bench.devices import TIER_AMD, TIER_NVIDIA
        from hal0.bench.harness import default_lanes

        assert default_lanes(TIER_AMD) == ["rocm"]
        assert default_lanes(TIER_NVIDIA) == ["rocm"]

    def test_default_lanes_includes_it_on_the_fixed_pin(self, fixed_pin) -> None:
        from hal0.bench.devices import TIER_AMD
        from hal0.bench.harness import default_lanes

        assert default_lanes(TIER_AMD) == ["rocm", "vulkan_radv"]

    def test_no_default_lane_is_ever_unsupported(self, broken_pin) -> None:
        """The invariant that makes the gate meaningful, checked in the state
        where it is easiest to violate."""
        from hal0.bench.devices import TIER_AMD, TIER_CPU, TIER_NVIDIA
        from hal0.bench.harness import default_lanes
        from hal0.bench.harness import lane_is_supported as supported

        for tier in (TIER_CPU, TIER_AMD, TIER_NVIDIA):
            for lane in default_lanes(tier):
                assert supported(lane), f"{tier} defaults to unsupported {lane}"

    def test_the_lane_spec_still_resolves_so_old_records_parse(self, broken_pin) -> None:
        """Unsupported is not deleted: historical records name this lane and
        must still resolve to a spec."""
        from hal0.bench.harness import lane_specs

        assert "vulkan_radv" in lane_specs()

    def test_the_bench_lane_benches_the_image_slots_actually_run(self, broken_pin) -> None:
        """Deliberately NOT pinned to ``VULKAN_FIXED_IMAGE``.

        Pinning the lane spec to the fixed image would make the sweep "work"
        on a broken-pin box by benchmarking an image no slot on that box
        launches — a number about nothing. The lane keeps tracking the default
        runner and is refused instead.
        """
        import hal0.bench.harness as harness_mod

        specs = harness_mod.lane_specs()
        assert specs["vulkan_radv"].image == harness_mod.DEFAULT_ROCMFPX_IMAGE
        assert specs["rocm"].image == harness_mod.DEFAULT_ROCMFPX_IMAGE
        assert specs["vulkan_radv"].image == ADE07BA_REF


class TestBenchPlannerSkipsAnUnsupportedLane:
    """Warning was enough when the lane was permanently retired and nothing
    shipped naming it. ``lane-matrix.toml`` now names it, so a warning alone
    would still produce the garbage records."""

    def test_the_planner_skips_rather_than_plans(self, broken_pin, capsys) -> None:
        from hal0.bench import planner as planner_mod

        planned = planner_mod._lane_is_plannable("vulkan_radv", suite_id="lane-matrix")
        assert planned is False
        err = capsys.readouterr().err
        assert "vulkan_radv" in err
        assert "1888" in err

    def test_a_supported_lane_is_plannable_and_silent(self, fixed_pin, capsys) -> None:
        from hal0.bench import planner as planner_mod

        assert planner_mod._lane_is_plannable("vulkan_radv", suite_id="lane-matrix") is True
        assert planner_mod._lane_is_plannable("rocm", suite_id="lane-matrix") is True
        assert capsys.readouterr().err == ""


# ── B2/N2: the three derivation ladders ─────────────────────────────────── #


def _amd_hw(*, compute: bool, vulkan: bool = True):
    from hal0.config.schema import GPUInfo, HardwareInfo, NPUInfo

    return HardwareInfo(
        cpu_model="AMD Ryzen AI Max+ 395",
        cpu_cores=16,
        cpu_threads=32,
        ram_mb=131072,
        unified_memory_mb=98304,
        platform="strix-halo",
        gpus=[
            GPUInfo(
                vendor="amd",
                name="Radeon 8060S",
                vram_mb=512,
                compute_capable=compute,
                vulkan_capable=vulkan,
            )
        ],
        npu=NPUInfo(present=False),
    )


class TestDerivationLaddersConsultTheImage:
    """A kfd-less AMD box must never be handed a device the slot-load guard
    will refuse. Before this, main+#1973-without-#1959 derived ``gpu-vulkan``
    for every LLM slot on such a box and then refused every one of them —
    a regression from "works slowly on CPU" to "no loadable slot at all"."""

    @pytest.fixture(autouse=True)
    def _no_kfd(self, monkeypatch):
        monkeypatch.setattr("hal0.install.profile_derive.kfd_present", lambda *a, **k: False)
        monkeypatch.setattr("hal0.hardware.recommend.kfd_present", lambda *a, **k: False)

    def test_profile_derive_falls_back_to_cpu_on_a_broken_pin(self, broken_pin) -> None:
        from hal0.install.profile_derive import derive_device

        assert derive_device("chat", _amd_hw(compute=False), npu_opt_in=False) == "cpu"

    def test_profile_derive_picks_vulkan_on_the_fixed_pin(self, fixed_pin) -> None:
        from hal0.install.profile_derive import derive_device

        assert derive_device("chat", _amd_hw(compute=False), npu_opt_in=False) == "gpu-vulkan"

    def test_recommend_falls_back_to_cpu_on_a_broken_pin(self, broken_pin) -> None:
        from hal0.hardware.recommend import recommend_primary_slot

        assert recommend_primary_slot(_amd_hw(compute=False))["device"] == "cpu"

    def test_recommend_picks_vulkan_on_the_fixed_pin(self, fixed_pin) -> None:
        from hal0.hardware.recommend import recommend_primary_slot

        assert recommend_primary_slot(_amd_hw(compute=False))["device"] == "gpu-vulkan"

    def test_rocm_is_unaffected_by_the_pin(self, broken_pin) -> None:
        """The image gate is Vulkan's. A box with ROCm compute derives ROCm
        whatever the Vulkan story is."""
        from hal0.hardware.recommend import recommend_primary_slot
        from hal0.install.profile_derive import derive_device

        hw = _amd_hw(compute=True)
        assert derive_device("chat", hw, npu_opt_in=False) == "gpu-rocm"
        assert recommend_primary_slot(hw)["device"] == "gpu-rocm"


class TestCliLadderMatchesTheOtherTwo:
    """N2: three ladders, one convention. ``_detect_default_hardware`` never
    consulted ``vulkan_capable`` at all, so an AMD box with neither compute
    nor a usable Vulkan device got ``gpu-vulkan`` written by a bare
    ``hal0 slot create`` — guaranteed refused at load."""

    @staticmethod
    def _probe(monkeypatch, tmp_path, *, compute: bool, vulkan: bool) -> None:
        import json

        from hal0.config import paths as _paths

        probe = tmp_path / "hardware.json"
        probe.write_text(
            json.dumps(
                {
                    "gpus": [
                        {
                            "vendor": "amd",
                            "name": "Radeon 8060S",
                            "vram_mb": 512,
                            "compute_capable": compute,
                            "vulkan_capable": vulkan,
                        }
                    ],
                    "unified_memory_mb": 98304,
                }
            )
        )
        monkeypatch.setattr(_paths, "hardware_json", lambda: probe)

    def test_broken_pin_gives_cpu_not_vulkan(self, monkeypatch, tmp_path, broken_pin) -> None:
        from hal0.cli.slot_commands import _detect_default_hardware

        self._probe(monkeypatch, tmp_path, compute=False, vulkan=True)
        assert _detect_default_hardware() == "cpu"

    def test_fixed_pin_gives_vulkan(self, monkeypatch, tmp_path, fixed_pin) -> None:
        from hal0.cli.slot_commands import _detect_default_hardware

        self._probe(monkeypatch, tmp_path, compute=False, vulkan=True)
        assert _detect_default_hardware() == "vulkan"

    def test_no_vulkan_device_gives_cpu_even_on_the_fixed_pin(
        self, monkeypatch, tmp_path, fixed_pin
    ) -> None:
        """The N2 gap: ``vulkan_capable`` was never read here."""
        from hal0.cli.slot_commands import _detect_default_hardware

        self._probe(monkeypatch, tmp_path, compute=False, vulkan=False)
        assert _detect_default_hardware() == "cpu"

    def test_compute_capable_still_gives_rocm(self, monkeypatch, tmp_path, broken_pin) -> None:
        from hal0.cli.slot_commands import _detect_default_hardware

        self._probe(monkeypatch, tmp_path, compute=True, vulkan=True)
        assert _detect_default_hardware() == "rocm"


class TestTheThreeLaddersAgree:
    """One convention, checked directly: for every (compute, vulkan, pin)
    combination the three ladders must reach the same verdict about whether
    this box has a usable Vulkan LLM lane."""

    @pytest.mark.parametrize("compute", [True, False])
    @pytest.mark.parametrize("vulkan", [True, False])
    @pytest.mark.parametrize("pin_is_fixed", [True, False])
    def test_all_three_agree(self, monkeypatch, tmp_path, compute, vulkan, pin_is_fixed) -> None:
        import json

        from hal0.config import paths as _paths
        from hal0.config.schema import VULKAN_FIXED_IMAGE
        from hal0.hardware.recommend import recommend_primary_slot
        from hal0.install.profile_derive import derive_device

        _pin(monkeypatch, VULKAN_FIXED_IMAGE if pin_is_fixed else ADE07BA_REF)
        monkeypatch.setattr("hal0.install.profile_derive.kfd_present", lambda *a, **k: False)
        monkeypatch.setattr("hal0.hardware.recommend.kfd_present", lambda *a, **k: False)

        probe = tmp_path / "hardware.json"
        probe.write_text(
            json.dumps(
                {
                    "gpus": [
                        {
                            "vendor": "amd",
                            "name": "Radeon 8060S",
                            "vram_mb": 512,
                            "compute_capable": compute,
                            "vulkan_capable": vulkan,
                        }
                    ],
                    "unified_memory_mb": 98304,
                }
            )
        )
        monkeypatch.setattr(_paths, "hardware_json", lambda: probe)

        hw = _amd_hw(compute=compute, vulkan=vulkan)
        expected = "gpu-rocm" if compute else ("gpu-vulkan" if (vulkan and pin_is_fixed) else "cpu")
        cli_to_device = {"rocm": "gpu-rocm", "vulkan": "gpu-vulkan", "cpu": "cpu"}

        from hal0.cli.slot_commands import _detect_default_hardware

        assert derive_device("chat", hw, npu_opt_in=False) == expected
        assert recommend_primary_slot(hw)["device"] == expected
        assert cli_to_device[_detect_default_hardware()] == expected


class TestDefaultImageServesVulkanLane:
    def test_it_answers_the_same_question_as_the_slot_load_gate(self, monkeypatch) -> None:
        """One predicate, so the perimeter and the gate cannot disagree."""
        from hal0.config.schema import VULKAN_FIXED_IMAGE, resolve_default_image

        for ref in (ADE07BA_REF, VULKAN_FIXED_IMAGE):
            monkeypatch.setenv("HAL0_TOOLBOX_IMAGE_ROCMFPX", ref)
            monkeypatch.setenv("HAL0_TOOLBOX_IMAGE_VULKANFPX", ref)
            assert default_image_serves_vulkan_lane() is image_serves_vulkan_lane(
                resolve_default_image("vulkan", "gpu")
            )
