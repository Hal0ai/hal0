"""``hal0_lxc_kind`` tells a Proxmox/LXD/Incus LXC container from any other
container, and every reader of the kind gives a non-Proxmox container neutral
advice (#4010).

systemd writes ``/run/systemd/container`` for Docker, systemd-nspawn and OCI
runtimes as well as for LXC, so the file merely existing proved nothing about
which container this is. The classifier now reads its CONTENT: ``lxc`` is the
LXC family (``lxc-privileged`` / ``lxc-unprivileged`` by the uid map), anything
else is the new kind ``container``.

Everything runs real bash under ``subprocess`` with the run directory, PID 1's
environment file and the uid map pointed at ``tmp_path``; nothing reads the real
``/proc/1/environ``.
"""

from __future__ import annotations

import os
import shlex
import stat
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PREFLIGHT = _REPO_ROOT / "installer" / "lib" / "preflight.sh"
_INSTALL_SH = _REPO_ROOT / "installer" / "install.sh"

_TIMEOUT = 60

_IDENTITY_MAP = "         0          0 4294967295\n"
_SHIFTED_MAP = "         0     100000      65536\n"

# What the neutral advice must never name (Proxmox-only vocabulary).
_PROXMOX_WORDS = ("pct ", "proxmox", "dev0:", "dev1:", "dev2:", "<ctid>")


def _classify_env(
    tmp_path: Path,
    *,
    marker: str | None,
    pid1: str = "",
    uid_map: str = _SHIFTED_MAP,
) -> dict[str, str]:
    """Fake every input of ``hal0_lxc_kind`` under ``tmp_path``."""
    run_dir = tmp_path / "run"
    (run_dir / "systemd").mkdir(parents=True)
    if marker is not None:
        (run_dir / "systemd" / "container").write_text(marker)
    environ = tmp_path / "pid1-environ"
    environ.write_bytes(pid1.encode())
    umap = tmp_path / "uid_map"
    umap.write_text(uid_map)
    return {
        "HAL0_CONTAINER_RUN_DIR": str(run_dir),
        "HAL0_BOX_RELEASE_FILE": str(tmp_path / "no-box-release"),
        "HAL0_PID1_ENVIRON_FILE": str(environ),
        "HAL0_UID_MAP_FILE": str(umap),
    }


def _bash(script: str, env_extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(Path.home()), **env_extra}
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=_TIMEOUT,
    )


def _kind(env: dict[str, str]) -> str:
    script = f"source {shlex.quote(str(_PREFLIGHT))} >/dev/null 2>&1\nhal0_lxc_kind\n"
    result = _bash(script, env)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


# -- the classifier -----------------------------------------------------------


@pytest.mark.parametrize("marker", ["docker\n", "systemd-nspawn\n", "oci\n", "docker"])
def test_other_container_is_container_not_lxc(tmp_path: Path, marker: str) -> None:
    assert _kind(_classify_env(tmp_path, marker=marker)) == "container"


def test_lxc_with_identity_uid_map_is_privileged(tmp_path: Path) -> None:
    env = _classify_env(tmp_path, marker="lxc\n", uid_map=_IDENTITY_MAP)
    assert _kind(env) == "lxc-privileged"


def test_lxc_with_shifted_uid_map_is_unprivileged(tmp_path: Path) -> None:
    env = _classify_env(tmp_path, marker="lxc\n", uid_map=_SHIFTED_MAP)
    assert _kind(env) == "lxc-unprivileged"


def test_podman_under_the_box_conditions_is_still_a_box(tmp_path: Path) -> None:
    env = _classify_env(tmp_path, marker="podman\n")
    release = tmp_path / "hal0-box-release"
    release.write_text("HAL0_BOX=1\n")
    env["HAL0_BOX_RELEASE_FILE"] = str(release)
    assert _kind(env) == "box"


def test_podman_without_the_box_image_is_a_plain_container(tmp_path: Path) -> None:
    assert _kind(_classify_env(tmp_path, marker="podman\n")) == "container"


def test_no_marker_and_no_pid1_signal_is_none(tmp_path: Path) -> None:
    assert _kind(_classify_env(tmp_path, marker=None)) == "none"


def test_empty_marker_with_a_pid1_lxc_signal_is_lxc(tmp_path: Path) -> None:
    env = _classify_env(tmp_path, marker="", pid1="PATH=/bin\0container=lxc\0")
    assert _kind(env) == "lxc-unprivileged"


def test_absent_marker_with_a_pid1_lxc_signal_is_lxc(tmp_path: Path) -> None:
    env = _classify_env(tmp_path, marker=None, pid1="container=lxc\0", uid_map=_IDENTITY_MAP)
    assert _kind(env) == "lxc-privileged"


def test_empty_marker_without_a_pid1_lxc_signal_is_container(tmp_path: Path) -> None:
    env = _classify_env(tmp_path, marker="\n", pid1="PATH=/bin\0container=docker\0")
    assert _kind(env) == "container"


def test_unreadable_marker_is_container(tmp_path: Path) -> None:
    # A marker file with no read permission (as root it reads as empty, which
    # classifies the same way).
    env = _classify_env(tmp_path, marker="")
    marker = Path(env["HAL0_CONTAINER_RUN_DIR"]) / "systemd" / "container"
    marker.chmod(0o000)
    try:
        assert _kind(env) == "container"
    finally:
        marker.chmod(stat.S_IRUSR | stat.S_IWUSR)


def test_marker_content_beats_a_pid1_lxc_signal(tmp_path: Path) -> None:
    env = _classify_env(tmp_path, marker="docker\n", pid1="container=lxc\0")
    assert _kind(env) == "container"


def test_override_accepts_container(tmp_path: Path) -> None:
    script = f"source {shlex.quote(str(_PREFLIGHT))} >/dev/null 2>&1\nhal0_lxc_kind\n"
    assert _bash(script, {"HAL0_LXC_KIND_OVERRIDE": "container"}).stdout.strip() == "container"


def test_in_lxc_predicate_is_false_for_container() -> None:
    script = f"source {shlex.quote(str(_PREFLIGHT))} >/dev/null 2>&1\nhal0_in_lxc\n"
    assert _bash(script, {"HAL0_LXC_KIND_OVERRIDE": "container"}).returncode == 1


# -- the GPU gate (preflight_gpu) ----------------------------------------------


def _gpu(tmp_path: Path, kind: str, extra: dict[str, str]) -> tuple[int, str]:
    script = (
        "set -euo pipefail\n"
        f"source {shlex.quote(str(_PREFLIGHT))}\n"
        "rc=0\npreflight_gpu || rc=$?\nexit $rc\n"
    )
    env = {
        **os.environ,
        "HAL0_GPU_GATE": "1",
        "HAL0_GPU_AMD_OVERRIDE": "0",
        "HAL0_GPU_VULKAN_LANE_OVERRIDE": "0",
        "HAL0_NPU_ACCEL_NODE": str(tmp_path / "no-accel"),
        "HAL0_LXC_KIND_OVERRIDE": kind,
        **extra,
    }
    proc = subprocess.run(
        ["bash", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
        check=False,
    )
    return proc.returncode, (proc.stdout + proc.stderr)


def _assert_neutral(output: str) -> None:
    low = output.lower()
    for word in _PROXMOX_WORDS:
        assert word not in low, f"{word!r} in:\n{output}"


def test_gpu_no_device_container_is_stopped_like_lxc_with_neutral_advice(tmp_path: Path) -> None:
    extra = {"HAL0_GPU_DRI_GLOB": str(tmp_path / "NONE*")}
    rc_lxc, _ = _gpu(tmp_path, "lxc-unprivileged", extra)
    rc, out = _gpu(tmp_path, "container", extra)
    assert rc == rc_lxc == 4, out
    assert "passed into this container from the machine that runs it" in out
    _assert_neutral(out)


def test_gpu_no_device_container_advice_is_soft_outside_the_gate(tmp_path: Path) -> None:
    rc, out = _gpu(
        tmp_path, "container", {"HAL0_GPU_DRI_GLOB": str(tmp_path / "NONE*"), "HAL0_GPU_GATE": "0"}
    )
    assert rc == 0, out
    _assert_neutral(out)


def test_gpu_bare_metal_is_unchanged(tmp_path: Path) -> None:
    rc, out = _gpu(tmp_path, "none", {"HAL0_GPU_DRI_GLOB": str(tmp_path / "NONE*")})
    assert rc == 0, out
    assert "no GPU driver bound" in out


@pytest.mark.parametrize(
    ("gid", "want_group"),
    [("61999", "render"), ("0", "daemon")],
    ids=["gid-maps-to-no-group", "gid-maps-to-the-wrong-group"],
)
def test_gpu_gid_problem_container_is_stopped_like_lxc_with_neutral_advice(
    tmp_path: Path, gid: str, want_group: str
) -> None:
    (tmp_path / "renderD128").touch()
    extra = {
        "HAL0_GPU_DRI_GLOB": str(tmp_path / "renderD*"),
        "HAL0_GPU_RENDER_GID_OVERRIDE": gid,
        "HAL0_GPU_RENDER_GROUP_OVERRIDE": want_group,
    }
    rc_lxc, _ = _gpu(tmp_path, "lxc-unprivileged", extra)
    rc, out = _gpu(tmp_path, "container", extra)
    assert rc == rc_lxc == 3, out
    assert "Fix where this container is started" in out
    _assert_neutral(out)


def test_gpu_missing_kfd_container_is_stopped_like_lxc_with_neutral_advice(tmp_path: Path) -> None:
    (tmp_path / "renderD128").touch()
    extra = {
        "HAL0_GPU_DRI_GLOB": str(tmp_path / "renderD*"),
        "HAL0_GPU_RENDER_GID_OVERRIDE": "0",
        "HAL0_GPU_RENDER_GROUP_OVERRIDE": "root",
        "HAL0_GPU_AMD_OVERRIDE": "1",
        "HAL0_GPU_KFD_PATH": str(tmp_path / "no-kfd"),
    }
    rc_lxc, _ = _gpu(tmp_path, "lxc-unprivileged", extra)
    rc, out = _gpu(tmp_path, "container", extra)
    assert rc == rc_lxc == 5, out
    assert "Pass /dev/kfd into this container from the machine that runs it" in out
    _assert_neutral(out)


def test_gpu_vulkan_lane_container_hint_is_neutral(tmp_path: Path) -> None:
    (tmp_path / "renderD128").touch()
    extra = {
        "HAL0_GPU_DRI_GLOB": str(tmp_path / "renderD*"),
        "HAL0_GPU_RENDER_GID_OVERRIDE": "0",
        "HAL0_GPU_RENDER_GROUP_OVERRIDE": "root",
        "HAL0_GPU_AMD_OVERRIDE": "1",
        "HAL0_GPU_VULKAN_LANE_OVERRIDE": "1",
        "HAL0_GPU_KFD_PATH": str(tmp_path / "no-kfd"),
    }
    rc, out = _gpu(tmp_path, "container", extra)
    assert rc == 0, out
    assert "To ALSO enable the ROCm lane, pass /dev/kfd into this container" in out
    _assert_neutral(out)


def test_gpu_kfd_group_container_is_stopped_like_lxc_with_neutral_advice(tmp_path: Path) -> None:
    (tmp_path / "renderD128").touch()
    kfd = tmp_path / "kfd"
    kfd.touch()
    kfd.chmod(0o660)
    extra = {
        "HAL0_GPU_DRI_GLOB": str(tmp_path / "renderD*"),
        "HAL0_GPU_RENDER_GID_OVERRIDE": "0",
        "HAL0_GPU_RENDER_GROUP_OVERRIDE": "root",
        "HAL0_GPU_KFD_PATH": str(kfd),
        "HAL0_GPU_KFD_GID_OVERRIDE": "0",
    }
    rc_lxc, out_lxc = _gpu(tmp_path, "lxc-unprivileged", extra)
    rc, out = _gpu(tmp_path, "container", extra)
    assert rc == rc_lxc, out
    if rc == 6:
        assert "If chgrp is refused, pass" in out
        assert "dev1:" in out_lxc
        _assert_neutral(out)
    else:  # running as root: both gids are 0, the shape needs a non-root owner
        pytest.skip("the render node and /dev/kfd share a gid under this user")


# -- the container-runtime gate ------------------------------------------------


def _fake_failing_podman(tmp_path: Path) -> None:
    shim = tmp_path / "podman"
    shim.write_text(
        '#!/usr/bin/env bash\nif [[ "$1" != "run" ]]; then exit 0; fi\n'
        "echo 'Error: crun: could not start the thing' >&2\nexit 125\n",
        encoding="utf-8",
    )
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _runtime_gate(tmp_path: Path, kind: str) -> tuple[int, str]:
    _fake_failing_podman(tmp_path)
    script = (
        "set -uo pipefail\n"
        f"source {shlex.quote(str(_PREFLIGHT))}\n"
        "rc=0\n_container_runtime_gate podman || rc=$?\nexit $rc\n"
    )
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ.get('PATH', '')}",
        "REPO_ROOT": str(_REPO_ROOT),
        "HAL0_APPARMOR_CONF": str(tmp_path / "containers.conf"),
        "HAL0_APPARMOR_D": str(tmp_path / "apparmor.d"),
        "HAL0_APPARMOR_NS_LEVEL_FILE": str(tmp_path / "ns_level"),
        "HAL0_LXC_KIND_OVERRIDE": kind,
    }
    (tmp_path / "ns_level").write_text("0\n")
    proc = subprocess.run(
        ["bash", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        timeout=_TIMEOUT,
        check=False,
    )
    return proc.returncode, proc.stdout + proc.stderr


def test_runtime_gate_container_fails_with_neutral_advice(tmp_path: Path) -> None:
    rc, out = _runtime_gate(tmp_path, "container")
    assert rc != 0, out
    assert "fails inside this container" in out
    assert "needs nesting and extra privileges allowed for this container" in out
    _assert_neutral(out)


def test_runtime_gate_lxc_advice_is_unchanged(tmp_path: Path) -> None:
    rc, out = _runtime_gate(tmp_path, "lxc-unprivileged")
    assert rc != 0, out
    assert "pct stop <CTID> && pct start <CTID>" in out
    rc, out = _runtime_gate(tmp_path, "lxc-privileged")
    assert rc != 0, out
    assert "features: nesting=1,keyctl=1,fuse=1,mknod=1" in out


# -- install.sh's use of the gate result ----------------------------------------


def _install_gate_region() -> str:
    text = _INSTALL_SH.read_text(encoding="utf-8")
    start = text.index('if [[ "${DEV_MODE}" -eq 0 ]]; then\n    gpu_rc=0\n')
    end = text.index("# ── Operator input: model store + HuggingFace token", start)
    return text[start:end]


def _install_gate(tmp_path: Path, kind: str, gpu_rc: int, extra: dict[str, str]) -> tuple[int, str]:
    region = tmp_path / "region.sh"
    region.write_text(_install_gate_region(), encoding="utf-8")
    script = (
        "set -uo pipefail\n"
        f"source {shlex.quote(str(_PREFLIGHT))} >/dev/null 2>&1\n"
        'err() { printf "ERR: %s\\n" "$*" >&2; }\n'
        'warn() { printf "WARN: %s\\n" "$*" >&2; }\n'
        'info() { printf "INFO: %s\\n" "$*" >&2; }\n'
        "_confirm_cpu_only() { return 1; }\n"
        "_cpu_only_image_gate() { return 0; }\n"
        "_cpu_only_remedy_lines() { :; }\n"
        f"preflight_gpu() {{ return {gpu_rc}; }}\n"
        "DEV_MODE=0\n"
        f"source {shlex.quote(str(region))}\n"
        "echo PROCEEDED\n"
    )
    env = {
        **os.environ,
        "HAL0_LXC_KIND_OVERRIDE": kind,
        "HAL0_GPU_KFD_PATH": str(tmp_path / "no-kfd"),
        **extra,
    }
    env.pop("HAL0_ALLOW_CPU_ONLY", None)
    env.update(extra)
    proc = subprocess.run(
        ["bash", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        timeout=_TIMEOUT,
        check=False,
    )
    return proc.returncode, proc.stdout + proc.stderr


def test_install_no_device_container_stops_with_neutral_sentence(tmp_path: Path) -> None:
    rc, out = _install_gate(tmp_path, "container", 4, {})
    assert rc == 1, out
    assert "PROCEEDED" not in out
    assert (
        "No GPU devices inside this container. Pass them in from the machine that runs "
        "this container (remedy above), then re-run install.sh."
    ) in out
    _assert_neutral(out)


def test_install_no_device_container_honours_the_cpu_only_consent(tmp_path: Path) -> None:
    rc, out = _install_gate(tmp_path, "container", 4, {"HAL0_ALLOW_CPU_ONLY": "1"})
    assert rc == 0, out
    assert "PROCEEDED" in out
    assert "proceeding CPU-only (HAL0_ALLOW_CPU_ONLY=1)" in out


def test_install_no_device_lxc_sentence_is_unchanged(tmp_path: Path) -> None:
    rc, out = _install_gate(tmp_path, "lxc-unprivileged", 4, {})
    assert rc == 1, out
    assert (
        "No GPU devices inside this container. Forward them from the Proxmox host "
        "(remedy above), then re-run install.sh."
    ) in out


def test_install_broken_gid_container_stops_with_neutral_sentence(tmp_path: Path) -> None:
    rc, out = _install_gate(tmp_path, "container", 3, {})
    assert rc == 1, out
    assert "Apply the device group fix shown above where this container is started" in out
    _assert_neutral(out)


def test_install_broken_gid_lxc_sentence_is_unchanged(tmp_path: Path) -> None:
    rc, out = _install_gate(tmp_path, "lxc-privileged", 3, {})
    assert rc == 1, out
    assert "Apply the dev0/gid fix shown above on the Proxmox host, then re-run install.sh." in out


def test_install_kfd_group_container_advice_is_neutral_and_does_not_block(tmp_path: Path) -> None:
    rc, out = _install_gate(tmp_path, "container", 6, {})
    assert rc == 0, out
    assert "PROCEEDED" in out
    assert "Container: the node's ownership comes from the machine that runs this container" in out
    _assert_neutral(out)


def test_install_kfd_group_lxc_unprivileged_advice_is_unchanged(tmp_path: Path) -> None:
    rc, out = _install_gate(tmp_path, "lxc-unprivileged", 6, {})
    assert rc == 0, out
    assert "then: pct stop <CTID> && pct start <CTID>. See hal0 issue #1953." in out
