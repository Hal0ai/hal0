from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from hal0.install.wizard.facts import (
    Disk,
    FactsError,
    load_facts,
    local_disks,
    satisfied,
    total_ram_gb,
)

SAMPLE = {
    "version": 1,
    "distro": {"id": "ubuntu", "release": "26.04", "package_manager": "apt-get"},
    "container": "none",
    "podman": {"present": True, "runs": True},
    "gpu": {"outcome": "usable", "reason": None, "vendor": "amd", "pool_gb": 96.0},
    "npu": {"device": True, "memlock_ok": True, "flm_build": True},
    "memory_python": True,
    "install": {"state": "fresh", "version": None},
}


def write(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "facts.json"
    path.write_text(json.dumps(data))
    return path


def fake_statvfs(sizes: dict[str, tuple[int, int]]):  # type: ignore[no-untyped-def]
    def statvfs(mount: str) -> os.statvfs_result:
        total, free = sizes[mount]
        return SimpleNamespace(f_frsize=2**30, f_blocks=total, f_bavail=free)  # type: ignore[return-value]

    return statvfs


def test_full_sample_parses(tmp_path: Path) -> None:
    f = load_facts(write(tmp_path, SAMPLE), disks=[], ram_gb=64.0)
    assert (f.gpu_outcome, f.gpu_pool_gb, f.npu_device, f.install_state) == (
        "usable",
        96.0,
        True,
        "fresh",
    )
    assert f.ram_gb == 64.0
    assert f.container == "none"


def test_no_facts_file_means_unknown_not_guessed() -> None:
    f = load_facts(None, disks=[], ram_gb=None)
    assert (f.gpu_outcome, f.gpu_pool_gb, f.npu_device, f.podman_runs, f.install_state) == (
        "usable",
        None,
        None,
        None,
        "fresh",
    )


def test_missing_keys_mean_unknown(tmp_path: Path) -> None:
    f = load_facts(write(tmp_path, {"version": 1}), disks=[], ram_gb=None)
    assert f.container is None
    assert f.npu_flm_build is None


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"version": 2}, "version 2"),
        ({"gpu": {"outcome": "maybe"}}, "gpu.outcome"),
        ({"install": {"state": "sideways"}}, "install.state"),
        ({"gpu": "yes"}, "gpu"),
        ({"npu": [1]}, "npu"),
        ({"podman": 3}, "podman"),
        ({"install": "fresh"}, "install"),
        ({"memory_python": "yes"}, "memory_python"),
        ({"container": 5}, "container"),
        ({"gpu": {"pool_gb": "big"}}, "gpu.pool_gb"),
        ({"npu": {"device": 1}}, "npu.device"),
    ],
)
def test_bad_facts_are_refused(tmp_path: Path, patch: dict[str, object], message: str) -> None:
    with pytest.raises(FactsError, match=message):
        load_facts(write(tmp_path, {**SAMPLE, **patch}), disks=[], ram_gb=None)


def test_null_sections_are_unknown(tmp_path: Path) -> None:
    data = {**SAMPLE, "npu": None, "gpu": None, "podman": None, "install": None}
    f = load_facts(write(tmp_path, data), disks=[], ram_gb=None)
    assert f.npu_device is None
    assert f.gpu_outcome == "usable"
    assert f.install_state == "fresh"


def test_free_strings_are_not_validated(tmp_path: Path) -> None:
    data = {**SAMPLE, "container": "lxc-new", "gpu": {"reason": "novel", "vendor": "acme"}}
    f = load_facts(write(tmp_path, data), disks=[], ram_gb=None)
    assert (f.container, f.gpu_reason, f.gpu_vendor) == ("lxc-new", "novel", "acme")


def test_missing_file_is_a_facts_error(tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"
    with pytest.raises(FactsError, match="not found") as info:
        load_facts(missing, disks=[], ram_gb=None)
    assert str(missing) in str(info.value)


def test_not_json_is_a_facts_error(tmp_path: Path) -> None:
    path = tmp_path / "facts.json"
    path.write_text("{oops")
    with pytest.raises(FactsError, match="not JSON"):
        load_facts(path, disks=[], ram_gb=None)


def test_deeply_nested_json_is_a_facts_error(tmp_path: Path) -> None:
    path = tmp_path / "facts.json"
    path.write_text("[" * 200000)
    with pytest.raises(FactsError, match="not JSON"):
        load_facts(path, disks=[], ram_gb=None)


def test_not_an_object_is_a_facts_error(tmp_path: Path) -> None:
    with pytest.raises(FactsError, match="not a JSON object"):
        load_facts(write(tmp_path, [1, 2]), disks=[], ram_gb=None)


def test_unreadable_file_is_a_facts_error(tmp_path: Path) -> None:
    with pytest.raises(FactsError, match="cannot be read"):
        load_facts(tmp_path, disks=[], ram_gb=None)


def test_system_reads_when_not_given() -> None:
    f = load_facts(None)
    assert isinstance(f.disks, tuple)
    assert f.ram_gb is None or f.ram_gb > 0


def test_local_disks_keeps_real_filesystems_once_each() -> None:
    mounts = (
        "/dev/sda1 / ext4 rw 0 0\n"
        "proc /proc proc rw 0 0\n"
        "/dev/sdb1 /data xfs rw 0 0\n"
        "/dev/sdb1 /data/alias xfs rw 0 0\n"
        "tmpfs /run tmpfs rw 0 0\n"
    )
    disks = local_disks(
        mounts,
        fake_statvfs({"/": (100, 40), "/data": (2000, 1500), "/data/alias": (2000, 1500)}),
    )
    assert [(d.mount, d.free_gb) for d in disks] == [("/data", 1500.0), ("/", 40.0)]
    assert disks[0] == Disk("/data", 1500.0, 2000.0)


def test_local_disks_skips_a_mount_that_cannot_be_read() -> None:
    def statvfs(mount: str) -> os.statvfs_result:
        raise OSError(mount)

    assert local_disks("/dev/sda1 / ext4 rw 0 0\n", statvfs) == ()


def test_total_ram_reads_memtotal() -> None:
    assert total_ram_gb("MemTotal:       65536000 kB\nMemFree: 1 kB\n") == pytest.approx(62.5)
    assert total_ram_gb("garbage") is None


@pytest.mark.parametrize("need", ["podman", "gpu", "npu_device", "flm_build", "memory_python"])
def test_satisfied_on_the_sample(tmp_path: Path, need: str) -> None:
    assert satisfied(need, load_facts(write(tmp_path, SAMPLE), disks=[], ram_gb=None)) is True


def test_satisfied_is_none_when_the_fact_is_unknown() -> None:
    assert satisfied("podman", load_facts(None, disks=[], ram_gb=None)) is None


@pytest.mark.parametrize(
    ("podman", "present", "runs", "met"),
    [
        ({"present": False}, False, None, True),
        ({"present": False, "runs": False}, False, False, True),
        ({"present": True, "runs": True}, True, True, True),
        ({"present": True, "runs": False}, True, False, False),
        ({"runs": False}, None, False, False),
        ({"runs": True}, None, True, True),
        ({}, None, None, None),
    ],
)
def test_podman_facts_and_the_podman_need(
    tmp_path: Path,
    podman: dict[str, bool],
    present: bool | None,
    runs: bool | None,
    met: bool | None,
) -> None:
    f = load_facts(write(tmp_path, {"version": 1, "podman": podman}), disks=[], ram_gb=None)
    assert (f.podman_present, f.podman_runs) == (present, runs)
    assert satisfied("podman", f) is met


def test_podman_present_must_be_a_flag(tmp_path: Path) -> None:
    with pytest.raises(FactsError, match=r"podman\.present"):
        load_facts(write(tmp_path, {"podman": {"present": "yes"}}), disks=[], ram_gb=None)
