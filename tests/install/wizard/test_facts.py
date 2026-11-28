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


def _stat(total: int, free: int, fsid: int = 0) -> os.statvfs_result:
    return SimpleNamespace(  # type: ignore[return-value]
        f_frsize=1, f_blocks=total, f_bavail=free, f_fsid=fsid
    )


def _probe(table: dict[str, os.statvfs_result]):  # type: ignore[no-untyped-def]
    """A statvfs that knows only the mounts in the table and fails the test on any other."""

    def statvfs(mount: str) -> os.statvfs_result:
        assert mount in table, f"statvfs was called on {mount}"
        return table[mount]

    return statvfs


_POOL = (812 * 2**30, 700 * 2**30)


def test_a_container_host_lists_only_the_root_and_one_row_for_the_pool() -> None:
    pool_mounts = [f"/pool/share{n}" for n in range(6)] + ["/pool"]
    mounts = "".join(
        [
            "rpool/root / zfs rw,relatime 0 0\n",
            "lxcfs /proc/stat fuse.lxcfs rw,nosuid 0 0\n",
            "hugetlbfs /dev/hugepages hugetlbfs rw,relatime 0 0\n",
            "sunrpc /run/rpc_pipefs rpc_pipefs rw,relatime 0 0\n",
            "nas:/export /mnt/nas nfs4 rw,relatime,vers=4.2 0 0\n",
            *(f"tank/d{n} {m} zfs rw,xattr 0 0\n" for n, m in enumerate(pool_mounts)),
            "/dev/sdc1 /mnt/readonly ext4 ro,relatime 0 0\n",
            "/dev/sdd1 /mnt/tiny ext4 rw,relatime 0 0\n",
        ]
    )
    table = {
        "/": _stat(100 * 2**30, 40 * 2**30),
        **{m: _stat(*_POOL) for m in pool_mounts},
        "/mnt/tiny": _stat(500 * 2**20, 400 * 2**20),
    }
    disks = local_disks(mounts, _probe(table))
    assert [d.mount for d in disks] == ["/pool", "/"]
    assert disks[0].free_gb == 700.0


@pytest.mark.parametrize(
    "line",
    [
        "x /data/fuse fuse rw 0 0",
        "x /data/sshfs fuse.sshfs rw 0 0",
        "x /data/huge hugetlbfs rw 0 0",
        "x /data/ns nsfs rw 0 0",
        "x /data/misc binfmt_misc rw 0 0",
        "x /data/efi efivarfs rw 0 0",
        "x /data/se selinuxfs rw 0 0",
        "x /data/nfsd nfsd rw 0 0",
        "x /data/rpc sunrpc rw 0 0",
        "x /data/pipes rpc_pipefs rw 0 0",
        *(
            f"srv:/x /data/{kind} {kind} rw 0 0"
            for kind in (
                "nfs",
                "nfs4",
                "cifs",
                "smb3",
                "smbfs",
                "ceph",
                "glusterfs",
                "9p",
                "afs",
                "lustre",
                "davfs",
                "ncpfs",
                "ocfs2",
                "gfs2",
            )
        ),
        *(
            f"/dev/sdb1 {where} ext4 rw 0 0"
            for where in ("/proc", "/sys/x", "/dev/y", "/run/z", "/boot", "/boot/efi", "/snap/a")
        ),
        "/dev/sdb1 /data/ro ext4 ro 0 0",
        "/dev/sdb1 /data/noopts ext4",
    ],
)
def test_a_mount_that_is_not_a_local_writable_disk_is_never_probed(line: str) -> None:
    assert local_disks(line + "\n", _probe({})) == ()


def test_the_root_is_kept_whatever_its_type() -> None:
    disks = local_disks("overlay / overlay ro 0 0\n", _probe({"/": _stat(2**20, 2**19)}))
    assert [d.mount for d in disks] == ["/"]


def test_a_mount_under_a_system_folder_name_prefix_is_still_a_disk() -> None:
    table = {"/running": _stat(*_POOL), "/bootstrap": _stat(2 * 2**30, 2**30)}
    mounts = "/dev/sdb1 /running ext4 rw 0 0\n/dev/sdc1 /bootstrap ext4 rw 0 0\n"
    assert [d.mount for d in local_disks(mounts, _probe(table))] == ["/running", "/bootstrap"]


def test_two_mounts_of_one_filesystem_id_are_one_row_under_the_shortest_mount() -> None:
    table = {
        "/data/deep": _stat(10 * 2**30, 5 * 2**30, fsid=7),
        "/data": _stat(10 * 2**30, 6 * 2**30, fsid=7),
        "/other": _stat(10 * 2**30, 4 * 2**30, fsid=8),
    }
    mounts = "a /data/deep ext4 rw 0 0\nb /data ext4 rw 0 0\nc /other ext4 rw 0 0\n"
    assert [d.mount for d in local_disks(mounts, _probe(table))] == ["/data", "/other"]


def test_a_filesystem_under_one_gib_is_left_out() -> None:
    table = {"/small": _stat(2**30 - 1, 2**29), "/big": _stat(2**30, 2**29)}
    mounts = "a /small ext4 rw 0 0\nb /big ext4 rw 0 0\n"
    assert [d.mount for d in local_disks(mounts, _probe(table))] == ["/big"]


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_number_that_is_not_finite_is_refused(tmp_path: Path, bad: float) -> None:
    path = tmp_path / "facts.json"
    path.write_text(json.dumps({"version": 1, "gpu": {"pool_gb": bad}}))
    with pytest.raises(FactsError, match=r"gpu\.pool_gb"):
        load_facts(path, disks=[], ram_gb=None)


def test_a_huge_number_that_reads_as_infinity_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "facts.json"
    path.write_text('{"version": 1, "gpu": {"pool_gb": 1e999}}')
    with pytest.raises(FactsError, match=r"gpu\.pool_gb"):
        load_facts(path, disks=[], ram_gb=None)
