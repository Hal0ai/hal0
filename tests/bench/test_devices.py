"""Tests for benchmark GPU device-node resolution (issue #1303).

The harness used to hardcode ``--device=/dev/dri/amdgpu``, so every queued
ROCm/Vulkan cell died with ``stat /dev/dri/amdgpu: no such file or directory``
on any host whose DRM nodes carry conventional kernel names (a Proxmox LXC
exposing ``card1`` + ``renderD128``), even while the production slots on the
same box ran fine.

None of these tests need real GPU hardware: ``/dev/dri`` and ``/dev/kfd`` are
relocated into ``tmp_path`` and the "device nodes" are symlinks to
``/dev/null`` — a real character device — which is the same trick
``tests/providers/test_gpu.py`` uses to exercise the ``S_ISCHR`` filter
without root/mknod.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hal0.bench import devices as bench_devices
from hal0.bench.devices import (
    TIER_AMD,
    TIER_CPU,
    TIER_NVIDIA,
    BenchDeviceError,
    resolve_bench_devices,
)

CONFIG_SH = Path(__file__).resolve().parents[2] / "installer" / "bench" / "config.sh"
REPO_SRC = Path(__file__).resolve().parents[2] / "src"


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _no_host_hardware_json(monkeypatch, tmp_path):
    """Never read the developer box's real /etc/hal0/hardware.json."""
    monkeypatch.setenv("HAL0_HOME", str(tmp_path / "hal0-home"))


def _lxc_dri(tmp_path: Path) -> tuple[str, str]:
    """The #1303 reproduction shape: /dev/kfd + card1 + renderD128 only.

    Notably NO ``card0`` and NO ``amdgpu`` node — exactly the LXC layout from
    the issue report.
    """
    dri = tmp_path / "dri"
    dri.mkdir()
    (dri / "card1").symlink_to("/dev/null")
    (dri / "renderD128").symlink_to("/dev/null")
    (dri / "by-path").mkdir()  # subdir — must never be passed to podman
    kfd = tmp_path / "kfd"
    kfd.symlink_to("/dev/null")
    return str(kfd), str(dri)


def _write_hardware_json(tmp_path: Path, payload: dict) -> None:
    etc = tmp_path / "hal0-home" / "etc" / "hal0"
    etc.mkdir(parents=True, exist_ok=True)
    (etc / "hardware.json").write_text(json.dumps(payload))


@pytest.fixture
def _stub_group_ids(monkeypatch):
    """Pin the render/video GIDs so assertions don't depend on /etc/group."""
    monkeypatch.setattr(
        "hal0.providers._gpu.resolve_gpu_group_ids",
        lambda node_paths=None: [993, 44],
    )


# ── probed / discovered nodes are used ───────────────────────────────────────


class TestDiscovery:
    def test_uses_the_real_card_and_render_nodes(self, tmp_path, _stub_group_ids) -> None:
        """Regression fixture: an LXC exposing only card1 + renderD128."""
        kfd, dri = _lxc_dri(tmp_path)

        spec = resolve_bench_devices({}, kfd_path=kfd, dri_dir=dri)

        assert spec.tier == TIER_AMD
        assert spec.source == "discovery"
        assert spec.devices == (kfd, f"{dri}/card1", f"{dri}/renderD128")
        assert spec.card_node == f"{dri}/card1"
        assert spec.render_node == f"{dri}/renderD128"

    def test_podman_argv_is_exactly_the_discovered_nodes(self, tmp_path, _stub_group_ids) -> None:
        """The acceptance criterion: the generated argv carries the DISCOVERED
        nodes and nothing else — in particular no ``/dev/dri/amdgpu``."""
        kfd, dri = _lxc_dri(tmp_path)

        flags = resolve_bench_devices({}, kfd_path=kfd, dri_dir=dri).podman_flags()

        assert flags == [
            f"--device={kfd}",
            f"--device={dri}/card1",
            f"--device={dri}/renderD128",
            "--group-add=993",
            "--group-add=44",
        ]

    def test_non_device_entries_are_dropped(self, tmp_path, _stub_group_ids) -> None:
        """Subdirectories and regular files under /dev/dri are never passed."""
        kfd, dri = _lxc_dri(tmp_path)
        (Path(dri) / "README").write_text("not a device")

        spec = resolve_bench_devices({}, kfd_path=kfd, dri_dir=dri)

        assert all(not d.endswith(("by-path", "README")) for d in spec.devices)

    def test_group_ids_come_from_the_resolved_nodes(self, tmp_path, monkeypatch) -> None:
        """The shared slot resolver is handed the nodes we resolved, so bench
        and slot containers derive their GIDs from the same source."""
        kfd, dri = _lxc_dri(tmp_path)
        seen: list[list[str]] = []

        def _fake(node_paths=None):
            seen.append(list(node_paths or []))
            return [1234]

        monkeypatch.setattr("hal0.providers._gpu.resolve_gpu_group_ids", _fake)

        spec = resolve_bench_devices({}, kfd_path=kfd, dri_dir=dri)

        assert spec.group_ids == (1234,)
        assert seen == [[kfd, f"{dri}/card1", f"{dri}/renderD128"]]


# ── CPU tier ─────────────────────────────────────────────────────────────────


class TestCpuTier:
    def test_no_gpu_nodes_resolves_to_cpu_without_error(self, tmp_path) -> None:
        """A CPU-tier run must not demand a DRI device — no raise, no flags."""
        dri = tmp_path / "dri"
        dri.mkdir()  # present but empty
        kfd = tmp_path / "kfd"  # absent

        spec = resolve_bench_devices({}, kfd_path=str(kfd), dri_dir=str(dri))

        assert spec.tier == TIER_CPU
        assert spec.devices == ()
        assert spec.group_ids == ()
        assert spec.podman_flags() == []

    def test_missing_dri_directory_resolves_to_cpu(self, tmp_path) -> None:
        """No /dev/dri at all (a plain CPU box / CI runner) is still fine."""
        spec = resolve_bench_devices(
            {}, kfd_path=str(tmp_path / "kfd"), dri_dir=str(tmp_path / "nope")
        )

        assert spec.tier == TIER_CPU
        assert spec.podman_flags() == []

    def test_cpu_tier_never_falls_back_to_the_legacy_bare_dirs(self, tmp_path) -> None:
        """``resolve_gpu_device_paths`` degrades to ``["/dev/kfd", "/dev/dri"]``
        on a no-GPU box; those are directories, not device nodes, and must not
        reach podman."""
        spec = resolve_bench_devices(
            {}, kfd_path=str(tmp_path / "kfd"), dri_dir=str(tmp_path / "nope")
        )

        assert "/dev/dri" not in spec.devices
        assert "/dev/kfd" not in spec.devices

    def test_explicit_cpu_pin_wins_over_present_hardware(self, tmp_path) -> None:
        kfd, dri = _lxc_dri(tmp_path)

        spec = resolve_bench_devices({"HAL0_BENCH_TIER": "cpu"}, kfd_path=kfd, dri_dir=dri)

        assert spec.tier == TIER_CPU
        assert spec.podman_flags() == []

    def test_amd_pin_without_nodes_is_an_actionable_error(self, tmp_path) -> None:
        with pytest.raises(BenchDeviceError) as exc:
            resolve_bench_devices(
                {"HAL0_BENCH_TIER": "amd"},
                kfd_path=str(tmp_path / "kfd"),
                dri_dir=str(tmp_path / "nope"),
            )

        assert "no GPU device nodes were found" in str(exc.value)
        assert "checked" in exc.value.details


# ── explicit overrides ───────────────────────────────────────────────────────


class TestOverrides:
    def test_per_node_env_overrides_beat_discovery(self, tmp_path, _stub_group_ids) -> None:
        kfd, dri = _lxc_dri(tmp_path)
        (Path(dri) / "card9").symlink_to("/dev/null")

        spec = resolve_bench_devices(
            {"HAL0_BENCH_CARD_DEVICE": f"{dri}/card9", "HAL0_BENCH_KFD_DEVICE": kfd},
            kfd_path=kfd,
            dri_dir=dri,
        )

        assert spec.source == "env"
        assert spec.devices == (kfd, f"{dri}/card9")
        assert f"{dri}/card1" not in spec.devices

    def test_device_list_override(self, tmp_path, _stub_group_ids) -> None:
        kfd, dri = _lxc_dri(tmp_path)

        spec = resolve_bench_devices(
            {"HAL0_BENCH_GPU_DEVICES": f"{kfd},{dri}/renderD128"},
            kfd_path=kfd,
            dri_dir=dri,
        )

        assert spec.devices == (kfd, f"{dri}/renderD128")

    def test_group_id_override(self, tmp_path) -> None:
        kfd, dri = _lxc_dri(tmp_path)

        spec = resolve_bench_devices(
            {"HAL0_BENCH_KFD_DEVICE": kfd, "HAL0_BENCH_GPU_GROUPS": "110,44,110"},
            kfd_path=kfd,
            dri_dir=dri,
        )

        assert spec.group_ids == (110, 44)

    def test_missing_override_path_fails_with_the_paths_checked(self, tmp_path) -> None:
        kfd, dri = _lxc_dri(tmp_path)

        with pytest.raises(BenchDeviceError) as exc:
            resolve_bench_devices(
                {"HAL0_BENCH_CARD_DEVICE": f"{dri}/amdgpu"}, kfd_path=kfd, dri_dir=dri
            )

        msg = str(exc.value)
        assert "does not exist" in msg
        assert f"{dri}/amdgpu" in msg

    def test_non_character_device_override_is_rejected(self, tmp_path) -> None:
        kfd, dri = _lxc_dri(tmp_path)
        plain = Path(dri) / "notadevice"
        plain.write_text("")

        with pytest.raises(BenchDeviceError) as exc:
            resolve_bench_devices(
                {"HAL0_BENCH_RENDER_DEVICE": str(plain)}, kfd_path=kfd, dri_dir=dri
            )

        assert "not a character device" in str(exc.value)

    def test_path_outside_the_allowed_node_shapes_is_rejected(self, tmp_path) -> None:
        with pytest.raises(BenchDeviceError) as exc:
            resolve_bench_devices({"HAL0_BENCH_CARD_DEVICE": "/etc/shadow"})

        assert "not an allowed GPU device node" in str(exc.value)

    def test_unknown_tier_pin_is_rejected(self) -> None:
        with pytest.raises(BenchDeviceError):
            resolve_bench_devices({"HAL0_BENCH_TIER": "tpu"})

    def test_env_seams_relocate_the_discovery_roots(self, tmp_path, _stub_group_ids) -> None:
        kfd, dri = _lxc_dri(tmp_path)

        spec = resolve_bench_devices({"HAL0_BENCH_KFD_PATH": kfd, "HAL0_BENCH_DRI_DIR": dri})

        assert spec.devices == (kfd, f"{dri}/card1", f"{dri}/renderD128")


# ── NVIDIA tier ──────────────────────────────────────────────────────────────


class TestNvidiaTier:
    def test_probed_nvidia_vendor_selects_cdi(self, tmp_path) -> None:
        _write_hardware_json(tmp_path, {"gpus": [{"vendor": "nvidia", "name": "NVIDIA RTX 4090"}]})

        spec = resolve_bench_devices(
            {}, kfd_path=str(tmp_path / "kfd"), dri_dir=str(tmp_path / "nope")
        )

        assert spec.tier == TIER_NVIDIA
        assert spec.devices == ("nvidia.com/gpu=all",)
        assert spec.gpu_label == "NVIDIA RTX 4090"

    def test_cdi_takes_no_group_add(self, tmp_path) -> None:
        """CDI injects nodes + permissions itself; --group-add would be wrong."""
        _write_hardware_json(tmp_path, {"gpus": [{"vendor": "nvidia", "name": "L40S"}]})

        flags = resolve_bench_devices(
            {}, kfd_path=str(tmp_path / "kfd"), dri_dir=str(tmp_path / "nope")
        ).podman_flags()

        assert flags == ["--device=nvidia.com/gpu=all"]


# ── probe snapshot ───────────────────────────────────────────────────────────


class TestProbeSnapshot:
    def test_gpu_label_comes_from_the_probe(self, tmp_path, _stub_group_ids) -> None:
        """So a run is never mislabelled with another tier's GPU string."""
        kfd, dri = _lxc_dri(tmp_path)
        _write_hardware_json(
            tmp_path, {"gpus": [{"vendor": "amd", "name": "AMD Radeon 8060S Graphics"}]}
        )

        spec = resolve_bench_devices({}, kfd_path=kfd, dri_dir=dri)

        assert spec.gpu_label == "AMD Radeon 8060S Graphics"

    def test_unreadable_snapshot_degrades_quietly(self, tmp_path, _stub_group_ids) -> None:
        kfd, dri = _lxc_dri(tmp_path)
        etc = tmp_path / "hal0-home" / "etc" / "hal0"
        etc.mkdir(parents=True, exist_ok=True)
        (etc / "hardware.json").write_text("{not json")

        spec = resolve_bench_devices({}, kfd_path=kfd, dri_dir=dri)

        assert spec.tier == TIER_AMD
        assert spec.gpu_label == ""


# ── rendered output ──────────────────────────────────────────────────────────


class TestRender:
    def test_env_block_is_parseable_key_values(self, tmp_path, _stub_group_ids) -> None:
        kfd, dri = _lxc_dri(tmp_path)
        spec = resolve_bench_devices({}, kfd_path=kfd, dri_dir=dri)

        block = bench_devices.render(spec, "env")
        parsed = [line.split("=", 1) for line in block.splitlines()]

        assert ["BENCH_TIER", TIER_AMD] in parsed
        assert ["BENCH_CARD_NODE", f"{dri}/card1"] in parsed
        assert [f"--device={kfd}"] == [
            v for k, v in parsed if k == "BENCH_RUN_FLAG" and v.endswith("kfd")
        ]
        assert "amdgpu" not in block

    def test_env_block_is_empty_of_flags_on_cpu(self, tmp_path) -> None:
        spec = resolve_bench_devices(
            {}, kfd_path=str(tmp_path / "kfd"), dri_dir=str(tmp_path / "nope")
        )

        block = bench_devices.render(spec, "env")

        assert "BENCH_TIER=cpu" in block
        assert "BENCH_RUN_FLAG=" not in block

    def test_json_render_round_trips(self, tmp_path, _stub_group_ids) -> None:
        kfd, dri = _lxc_dri(tmp_path)
        spec = resolve_bench_devices({}, kfd_path=kfd, dri_dir=dri)

        payload = json.loads(bench_devices.render(spec, "json"))

        assert payload["tier"] == TIER_AMD
        assert payload["podman_flags"] == spec.podman_flags()
