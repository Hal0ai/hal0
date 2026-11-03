"""``hal0_lxc_kind`` recognises a rootless podman box, and the box remedies
never send the operator to Proxmox.

The box root cannot change a device node's group (the node's owner is not
mapped into the box), so a broken ``/dev/kfd`` group can only be fixed on the
host that created the box. ``hal0_in_lxc`` must stay
LXC-only so the LXC remedies are not emitted for a box.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PREFLIGHT = _REPO_ROOT / "installer" / "lib" / "preflight.sh"
_INSTALL_SH = _REPO_ROOT / "installer" / "install.sh"


def _bash(snippet: str, env_extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    script = f"source {_PREFLIGHT!s} >/dev/null 2>&1\n{snippet}\n"
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": "/tmp", **env_extra}
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=env, check=False
    )


def test_containerenv_marker_is_a_box(tmp_path: Path) -> None:
    (tmp_path / ".containerenv").touch()
    r = _bash("hal0_lxc_kind", {"HAL0_CONTAINER_RUN_DIR": str(tmp_path)})
    assert r.stdout.strip() == "box", r.stderr


def test_systemd_container_podman_is_a_box(tmp_path: Path) -> None:
    (tmp_path / "systemd").mkdir()
    (tmp_path / "systemd" / "container").write_text("podman\n")
    r = _bash("hal0_lxc_kind", {"HAL0_CONTAINER_RUN_DIR": str(tmp_path)})
    assert r.stdout.strip() == "box", r.stderr


def test_lxc_marker_is_not_a_box(tmp_path: Path) -> None:
    (tmp_path / "systemd").mkdir()
    (tmp_path / "systemd" / "container").write_text("lxc\n")
    r = _bash("hal0_lxc_kind", {"HAL0_CONTAINER_RUN_DIR": str(tmp_path)})
    assert r.stdout.strip() in {"lxc-privileged", "lxc-unprivileged"}, r.stderr


def test_no_marker_is_not_a_box(tmp_path: Path) -> None:
    r = _bash("hal0_lxc_kind", {"HAL0_CONTAINER_RUN_DIR": str(tmp_path)})
    assert r.stdout.strip() != "box", r.stderr


def test_in_lxc_is_false_for_a_box(tmp_path: Path) -> None:
    (tmp_path / ".containerenv").touch()
    assert _bash("hal0_in_lxc", {"HAL0_CONTAINER_RUN_DIR": str(tmp_path)}).returncode == 1
    assert _bash("hal0_in_lxc", {"HAL0_LXC_KIND_OVERRIDE": "box"}).returncode == 1


def _kfd_gate(tmp_path: Path, kind: str) -> subprocess.CompletedProcess[str]:
    (tmp_path / "renderD128").touch()
    kfd = tmp_path / "kfd"
    kfd.touch()
    script = f"set -euo pipefail\nsource {_PREFLIGHT!s}\nrc=0\npreflight_gpu || rc=$?\nexit $rc\n"
    env = {
        **os.environ,
        "HAL0_GPU_GATE": "1",
        "HAL0_GPU_AMD_OVERRIDE": "1",
        "HAL0_GPU_DRI_GLOB": str(tmp_path / "renderD*"),
        "HAL0_GPU_RENDER_GID_OVERRIDE": "0",
        "HAL0_GPU_RENDER_GROUP_OVERRIDE": "root",
        "HAL0_GPU_KFD_PATH": str(kfd),
        "HAL0_GPU_KFD_GID_OVERRIDE": "0",
        "HAL0_LXC_KIND_OVERRIDE": kind,
    }
    return subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)


def test_kfd_remedy_under_box_points_at_the_host(tmp_path: Path) -> None:
    r = _kfd_gate(tmp_path, "box")
    out = r.stdout + r.stderr
    assert r.returncode == 6, out  # HAL0_GPU_RC_KFD_GID still gates
    assert "dev1:" not in out
    assert "PROXMOX" not in out.upper()
    assert "pct " not in out
    assert "HOST" in out and "re-create the box" in out


def test_kfd_remedy_under_lxc_is_unchanged(tmp_path: Path) -> None:
    out = (r := _kfd_gate(tmp_path, "lxc-unprivileged")).stdout + r.stderr
    assert "dev1:" in out


def test_install_sh_probes_the_npu_as_hal0_in_a_box() -> None:
    src = _INSTALL_SH.read_text(encoding="utf-8")
    assert "runuser -u hal0 -- flm validate" in src
    assert '== "box"' in src
