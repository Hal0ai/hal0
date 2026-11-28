"""installer/lib/facts.sh gathers detection-only facts and writes facts.json.

The library is sourced under real bash with the detection functions stubbed and
the sysfs and state directories pointed at temp trees (HAL0_FACTS_SYS_ROOT,
HAL0_FACTS_VAR_DIR, HAL0_FACTS_FHS_ROOT). The result is parsed with json.loads.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

FACTS_SH = Path(__file__).resolve().parents[2] / "installer" / "lib" / "facts.sh"

_DEFAULT_STUBS = """
hal0_lxc_kind() { echo none; }
distro_id() { echo ubuntu; }
pkg_mgr() { echo apt-get; }
_os_release_field() { case "$1" in VERSION_ID) echo 26.04 ;; ID) echo ubuntu ;; *) return 1 ;; esac; }
preflight_hindsight_python() { return 0; }
ulimit() { echo unlimited; }
command() {
    if [[ "$1" == "-v" && "$2" == "podman" ]]; then return "${STUB_PODMAN_PRESENT:-0}"; fi
    builtin command "$@"
}
podman() { [[ "$1" == "--version" ]] && return "${STUB_PODMAN_INFO_RC:-0}"; echo "podman $*" >> "$STUB_FAIL_LOG"; return 1; }
"""


def _make_sys(root: Path, *, vram: int | None, gtt: int | None, amdgpu: bool = True) -> None:
    if amdgpu:
        (root / "sys/module/amdgpu").mkdir(parents=True)
    dev = root / "sys/class/drm/card0/device"
    dev.mkdir(parents=True)
    if vram is not None:
        (dev / "mem_info_vram_total").write_text(f"{vram}\n")
    if gtt is not None:
        (dev / "mem_info_gtt_total").write_text(f"{gtt}\n")


def _run(
    tmp_path: Path,
    gpu_rc: int = 0,
    *,
    stubs: str = "",
    env: dict[str, str] | None = None,
    sys_root: Path | None = None,
    path: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    out = tmp_path / "run" / "facts.json"
    var_dir = tmp_path / "var"
    var_dir.mkdir(exist_ok=True)
    fhs = tmp_path / "fhs"
    fhs.mkdir(exist_ok=True)
    npu_node = tmp_path / "accel0"
    npu_node.write_text("")
    npu_sys = tmp_path / "sysaccel"
    (npu_sys / "device").mkdir(parents=True, exist_ok=True)
    script = f"""
set -u
{_DEFAULT_STUBS}
{stubs}
source "{FACTS_SH}"
hal0_facts_gather {gpu_rc}
hal0_facts_write "{out}"
echo "write_rc=$?"
"""
    full_env = {
        "PATH": path or os.environ["PATH"],
        "HAL0_FACTS_SYS_ROOT": str(sys_root if sys_root else tmp_path / "nosys"),
        "HAL0_FACTS_VAR_DIR": str(var_dir),
        "HAL0_FACTS_FHS_ROOT": str(fhs),
        "HAL0_NPU_ACCEL_NODE": str(npu_node),
        "HAL0_NPU_SYS_ACCEL": str(npu_sys),
        "STUB_FAIL_LOG": str(tmp_path / "fail.log"),
    }
    full_env.update(env or {})
    proc = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=full_env, check=False
    )
    return proc, out


def _facts(tmp_path: Path, gpu_rc: int = 0, **kw) -> dict:
    proc, out = _run(tmp_path, gpu_rc, **kw)
    assert "write_rc=0" in proc.stdout, proc.stdout + proc.stderr
    assert proc.stderr == "", proc.stderr
    return json.loads(out.read_text())


GIB = 1024**3


def test_full_amd_box(tmp_path: Path) -> None:
    sysroot = tmp_path / "sysfs"
    _make_sys(sysroot, vram=512 * 1024**2, gtt=96 * GIB)
    assert _facts(tmp_path, 0, sys_root=sysroot) == {
        "version": 1,
        "distro": {"id": "ubuntu", "release": "26.04", "package_manager": "apt-get"},
        "container": "none",
        "podman": {"present": True, "runs": True},
        "gpu": {"outcome": "usable", "reason": None, "vendor": "amd", "pool_gb": 96.0},
        "npu": {"device": True, "memlock_ok": True, "flm_build": True},
        "install": {"state": "fresh", "version": None},
        "memory_python": True,
    }


@pytest.mark.parametrize(
    ("rc", "outcome", "reason"),
    [
        (0, "usable", None),
        (3, "stop", "render_gid_unmapped"),
        (4, "cpu_only_needs_consent", "no_gpu_device"),
        (5, "cpu_only_needs_consent", "no_kfd"),
        (6, "usable", "kfd_group_mismatch"),
    ],
)
def test_gpu_return_codes_map(tmp_path: Path, rc: int, outcome: str, reason: str | None) -> None:
    gpu = _facts(tmp_path, rc)["gpu"]
    assert gpu["outcome"] == outcome
    assert gpu["reason"] == reason


def test_pool_is_larger_of_vram_and_gtt(tmp_path: Path) -> None:
    sysroot = tmp_path / "sysfs"
    _make_sys(sysroot, vram=24 * GIB, gtt=8 * GIB + GIB // 2)
    assert _facts(tmp_path, sys_root=sysroot)["gpu"]["pool_gb"] == 24.0
    sysroot2 = tmp_path / "sysfs2"
    _make_sys(sysroot2, vram=None, gtt=8 * GIB + GIB // 2)
    assert _facts(tmp_path, sys_root=sysroot2)["gpu"]["pool_gb"] == 8.5


def test_no_drm_tree_gives_null_pool_and_vendor(tmp_path: Path) -> None:
    gpu = _facts(tmp_path, 4)["gpu"]
    assert gpu["vendor"] is None
    assert gpu["pool_gb"] is None


def test_unreadable_pool_with_amd_is_null(tmp_path: Path) -> None:
    sysroot = tmp_path / "sysfs"
    _make_sys(sysroot, vram=None, gtt=None)
    gpu = _facts(tmp_path, sys_root=sysroot)["gpu"]
    assert gpu["vendor"] == "amd"
    assert gpu["pool_gb"] is None


def test_unknown_container_kind_maps_to_other(tmp_path: Path) -> None:
    assert _facts(tmp_path, stubs="hal0_lxc_kind() { echo docker; }")["container"] == "other"
    assert (
        _facts(tmp_path, stubs="hal0_lxc_kind() { echo lxc-unprivileged; }")["container"]
        == "lxc-unprivileged"
    )


def test_missing_os_release(tmp_path: Path) -> None:
    facts = _facts(tmp_path, stubs="distro_id() { return 1; }\n_os_release_field() { return 1; }")
    assert facts["distro"]["id"] == "unknown"
    assert facts["distro"]["release"] is None
    assert facts["npu"]["flm_build"] is False


def test_install_state_three_ways(tmp_path: Path) -> None:
    assert _facts(tmp_path)["install"] == {"state": "fresh", "version": None}

    fhs = tmp_path / "fhs"
    target = fhs / "hal0-1.8.1"
    target.mkdir(parents=True)
    (target / "VERSION").write_text("1.8.1\n")
    (fhs / "current").symlink_to(target)
    assert _facts(tmp_path)["install"] == {"state": "upgrade", "version": "1.8.1"}

    (tmp_path / "var").mkdir(exist_ok=True)
    (tmp_path / "var" / ".install-in-progress").write_text("")
    assert _facts(tmp_path)["install"]["state"] == "unfinished"


def test_json_string_strips_control_characters(tmp_path: Path) -> None:
    script = f"""
source "{FACTS_SH}"
hal0_json_string $'26.\\n04 "x"\\\\y\\t\\x01'
"""
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    assert "\n" not in proc.stdout.rstrip("\n")
    assert json.loads(proc.stdout) == '26.04 "x"\\y'


def test_json_string_drops_non_ascii_bytes_in_the_file(tmp_path: Path) -> None:
    stubs = (
        "_os_release_field() { case \"$1\" in VERSION_ID) printf 'a\\xffb\\xe2\\n\"q\\\\' ;; "
        "*) return 1 ;; esac; }"
    )
    proc, out = _run(tmp_path, stubs=stubs)
    assert "write_rc=0" in proc.stdout, proc.stdout + proc.stderr
    raw = out.read_bytes()
    assert b"\xff" not in raw and b"\xe2" not in raw
    doc = json.loads(raw.decode("utf-8"))
    assert "\xff" not in doc["distro"]["release"]
    assert doc["distro"]["release"] == 'ab"q\\'


def test_writer_needs_no_python(tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for tool in ("bash", "mktemp", "chmod", "mv", "tr", "mkdir", "dirname", "rm", "cat", "head", "sort"):
        found = shutil.which(tool)
        assert found
        (bindir / tool).symlink_to(found)
    proc, out = _run(tmp_path, path=str(bindir))
    assert "write_rc=0" in proc.stdout, proc.stdout + proc.stderr
    assert proc.stderr == ""
    assert json.loads(out.read_text()) == {
        "version": 1,
        "distro": {"id": "ubuntu", "release": "26.04", "package_manager": "apt-get"},
        "container": "none",
        "podman": {"present": True, "runs": True},
        "gpu": {"outcome": "usable", "reason": None, "vendor": None, "pool_gb": None},
        "npu": {"device": True, "memlock_ok": True, "flm_build": True},
        "install": {"state": "fresh", "version": None},
        "memory_python": True,
    }


def test_write_is_atomic_and_0644(tmp_path: Path) -> None:
    out = tmp_path / "run" / "facts.json"
    out.parent.mkdir()
    out.write_text("old")
    proc, _ = _run(tmp_path)
    assert "write_rc=0" in proc.stdout
    assert stat.S_IMODE(out.stat().st_mode) == 0o644
    assert json.loads(out.read_text())["version"] == 1
    assert sorted(p.name for p in out.parent.iterdir()) == ["facts.json"]


def test_gather_installs_nothing(tmp_path: Path) -> None:
    bindir = tmp_path / "failbin"
    bindir.mkdir()
    log = tmp_path / "fail.log"
    for tool in ("apt-get", "dnf", "chgrp", "usermod", "groupadd"):
        stub = bindir / tool
        stub.write_text(f'#!/bin/sh\necho "{tool} $*" >> "{log}"\nexit 1\n')
        stub.chmod(0o755)
    podman = bindir / "podman"
    podman.write_text(
        f'#!/bin/sh\n[ "$1" = --version ] && exit 0\necho "podman $*" >> "{log}"\nexit 1\n'
    )
    podman.chmod(0o755)
    stubs = "unset -f podman command"
    proc, out = _run(
        tmp_path, stubs=stubs, path=f"{bindir}:{os.environ['PATH']}"
    )
    assert "write_rc=0" in proc.stdout, proc.stdout + proc.stderr
    assert not log.exists(), log.read_text()
    assert json.loads(out.read_text())["podman"] == {"present": True, "runs": True}


def test_memlock_not_unlimited_is_false(tmp_path: Path) -> None:
    assert _facts(tmp_path, stubs="ulimit() { echo 64; }")["npu"]["memlock_ok"] is False


def test_missing_npu_sysfs_entry_is_stale_device(tmp_path: Path) -> None:
    facts = _facts(tmp_path, env={"HAL0_NPU_SYS_ACCEL": str(tmp_path / "gone")})
    assert facts["npu"]["device"] is False


def test_fhs_root_defaults_to_prefix(tmp_path: Path) -> None:
    prefix = tmp_path / "prefix"
    target = prefix / "hal0-2.0.0"
    target.mkdir(parents=True)
    (target / "VERSION").write_text("2.0.0\n")
    (prefix / "current").symlink_to(target)
    facts = _facts(tmp_path, env={"HAL0_FACTS_FHS_ROOT": "", "HAL0_PREFIX": str(prefix)})
    assert facts["install"] == {"state": "upgrade", "version": "2.0.0"}


def test_write_without_path_fails_under_set_u(tmp_path: Path) -> None:
    script = f'set -u\nsource "{FACTS_SH}"\nhal0_facts_write\necho "rc=$?"'
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    assert "rc=1" in proc.stdout
    assert "no path" in proc.stderr


def test_write_refuses_symlinked_directory(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "run"
    link.symlink_to(real)
    script = f'source "{FACTS_SH}"\nhal0_facts_write "{link}/facts.json"\necho "rc=$?"'
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    assert "rc=1" in proc.stdout
    assert not (real / "facts.json").exists()


def test_write_keeps_the_mode_of_an_existing_directory(tmp_path: Path) -> None:
    d = tmp_path / "run"
    d.mkdir()
    d.chmod(0o1777)
    proc, out = _run(tmp_path)
    assert "write_rc=0" in proc.stdout
    assert stat.S_IMODE(d.stat().st_mode) == 0o1777
    assert stat.S_IMODE(out.stat().st_mode) == 0o644


def test_write_creates_a_new_directory_0755(tmp_path: Path) -> None:
    out = tmp_path / "fresh" / "facts.json"
    script = f'source "{FACTS_SH}"\nhal0_facts_write "{out}"\necho "rc=$?"'
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    assert "rc=0" in proc.stdout
    assert stat.S_IMODE(out.parent.stat().st_mode) == 0o755
