"""Contract tests for ``preflight_gpu``'s install-time gate (WS-B, #1104).

``preflight_gpu`` runs in two modes. Under ``hal0 doctor`` (the default) it is
advisory-only and must always return 0 so the report never aborts. Under the
Stage-1 installer gate (``HAL0_GPU_GATE=1``) it must classify the platform via
its return code so ``install.sh`` can smart-block the single most common
broken-install shape: a Proxmox LXC with the GPU forwarded but the render-node
gid mis-mapped, which otherwise installs "successfully" then silently runs
every slot CPU-only.

The function reads real ``/dev`` nodes and ``/proc/1/environ``; these tests
drive it through the documented test seams (``HAL0_GPU_DRI_GLOB``,
``HAL0_GPU_CONTAINER_OVERRIDE``, ``HAL0_GPU_RENDER_GID_OVERRIDE``) so the
outcome is independent of the host that runs the suite (which may itself be an
LXC with a real GPU, as the maintainer's box is).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

PREFLIGHT = Path(__file__).resolve().parents[2] / "installer" / "lib" / "preflight.sh"

# Return codes exported by preflight.sh — kept in lock-step with the shell.
RC_OK = 0
RC_BROKEN_GID = 3
RC_NO_DEVICE = 4

# A gid with no matching group on any sane host — forces "maps to NO group".
UNMAPPED_GID = "61999"


def _run_gpu_gate(env_overrides: dict[str, str]) -> int:
    """Source preflight.sh and run ``preflight_gpu``, returning its rc.

    ``set -euo pipefail`` mirrors install.sh so we also prove the function is
    safe to source there. The ``|| rc=$?`` guard captures a non-zero return
    without tripping ``set -e``.
    """
    script = (
        "set -euo pipefail\n"
        f"source {PREFLIGHT!s}\n"
        "rc=0\n"
        "preflight_gpu >/dev/null 2>&1 || rc=$?\n"
        "exit $rc\n"
    )
    env = {**os.environ, **env_overrides}
    proc = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
    return proc.returncode


@pytest.fixture
def render_glob(tmp_path: Path) -> str:
    """A glob that matches a fake render node, so 'device present' is testable."""
    (tmp_path / "renderD128").touch()
    return str(tmp_path / "renderD*")


def test_gate_broken_gid_lxc_hard_stops(render_glob: str) -> None:
    """Device present + unmapped render gid + LXC → BROKEN_GID (hard stop)."""
    rc = _run_gpu_gate(
        {
            "HAL0_GPU_GATE": "1",
            "HAL0_GPU_DRI_GLOB": render_glob,
            "HAL0_GPU_CONTAINER_OVERRIDE": "lxc",
            "HAL0_GPU_RENDER_GID_OVERRIDE": UNMAPPED_GID,
        }
    )
    assert rc == RC_BROKEN_GID


def test_gate_no_device_lxc_opt_in(tmp_path: Path) -> None:
    """No device + LXC → NO_DEVICE, so install.sh can offer a CPU-only opt-in."""
    rc = _run_gpu_gate(
        {
            "HAL0_GPU_GATE": "1",
            "HAL0_GPU_DRI_GLOB": str(tmp_path / "NONE*"),
            "HAL0_GPU_CONTAINER_OVERRIDE": "lxc",
        }
    )
    assert rc == RC_NO_DEVICE


def test_gate_bare_metal_no_gpu_proceeds(tmp_path: Path) -> None:
    """Genuine bare-metal CPU box → proceed (0), no friction."""
    rc = _run_gpu_gate(
        {
            "HAL0_GPU_GATE": "1",
            "HAL0_GPU_DRI_GLOB": str(tmp_path / "NONE*"),
            "HAL0_GPU_CONTAINER_OVERRIDE": "none",
        }
    )
    assert rc == RC_OK


def test_gate_device_good_gid_proceeds(render_glob: str) -> None:
    """Device present + gid maps to a real group → proceed (0)."""
    rc = _run_gpu_gate(
        {
            "HAL0_GPU_GATE": "1",
            "HAL0_GPU_DRI_GLOB": render_glob,
            "HAL0_GPU_CONTAINER_OVERRIDE": "lxc",
            # gid 0 always maps to the 'root' group.
            "HAL0_GPU_RENDER_GID_OVERRIDE": "0",
        }
    )
    assert rc == RC_OK


def test_gate_broken_gid_on_bare_metal_does_not_block(render_glob: str) -> None:
    """Only an LXC miswire blocks — a bare-metal unmapped gid still proceeds."""
    rc = _run_gpu_gate(
        {
            "HAL0_GPU_GATE": "1",
            "HAL0_GPU_DRI_GLOB": render_glob,
            "HAL0_GPU_CONTAINER_OVERRIDE": "none",
            "HAL0_GPU_RENDER_GID_OVERRIDE": UNMAPPED_GID,
        }
    )
    assert rc == RC_OK


def test_doctor_mode_broken_gid_is_soft(render_glob: str) -> None:
    """Without the gate flag (doctor), the same broken LXC stays soft (0)."""
    rc = _run_gpu_gate(
        {
            "HAL0_GPU_DRI_GLOB": render_glob,
            "HAL0_GPU_CONTAINER_OVERRIDE": "lxc",
            "HAL0_GPU_RENDER_GID_OVERRIDE": UNMAPPED_GID,
        }
    )
    assert rc == RC_OK


def test_doctor_mode_no_device_lxc_is_soft(tmp_path: Path) -> None:
    """Without the gate flag, a no-device LXC also stays soft (0)."""
    rc = _run_gpu_gate(
        {
            "HAL0_GPU_DRI_GLOB": str(tmp_path / "NONE*"),
            "HAL0_GPU_CONTAINER_OVERRIDE": "lxc",
        }
    )
    assert rc == RC_OK
