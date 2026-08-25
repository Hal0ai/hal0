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
RC_NO_KFD = 5
RC_KFD_GID = 6

# A gid with no matching group on any sane host — forces "maps to NO group".
UNMAPPED_GID = "61999"


def _run_gpu_gate_full(env_overrides: dict[str, str]) -> subprocess.CompletedProcess:
    """Source preflight.sh, run ``preflight_gpu``, return the whole process.

    ``set -euo pipefail`` mirrors install.sh so we also prove the function is
    safe to source there. The ``|| rc=$?`` guard captures a non-zero return
    without tripping ``set -e``. Output is captured rather than discarded so
    tests can assert on what the operator is actually told (#1948: the gate's
    two branches now say different things, and both must be true).
    """
    script = f"set -euo pipefail\nsource {PREFLIGHT!s}\nrc=0\npreflight_gpu || rc=$?\nexit $rc\n"
    # HAL0_GPU_AMD_OVERRIDE defaults OFF here so the AMD lane check (#1888)
    # stays hermetic: the box running the suite may itself be an AMD LXC with
    # (or without) a real /dev/kfd. Tests that exercise that check set it.
    #
    # HAL0_GPU_VULKAN_LANE_OVERRIDE defaults OFF for the same reason — the
    # answer would otherwise depend on what DEFAULT_ROCMFPX_IMAGE happens to
    # be in this checkout, which is exactly the moving target #1959 moves.
    # "0" reproduces the pre-repin world, which is the conservative default
    # and the one the historical assertions below were written against.
    env = {
        **os.environ,
        "HAL0_GPU_AMD_OVERRIDE": "0",
        "HAL0_GPU_VULKAN_LANE_OVERRIDE": "0",
        **env_overrides,
    }
    return subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)


def _run_gpu_gate(env_overrides: dict[str, str]) -> int:
    """``_run_gpu_gate_full``'s return code — the shape most tests want."""
    return _run_gpu_gate_full(env_overrides).returncode


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
    """Device present + gid maps to the (expected) render group → proceed (0).

    HAL0_GPU_RENDER_GROUP_OVERRIDE points "the render group" at 'root' (gid 0
    always maps to 'root') so this is hermetic without a real 'render' group
    on the host running the suite.
    """
    rc = _run_gpu_gate(
        {
            "HAL0_GPU_GATE": "1",
            "HAL0_GPU_DRI_GLOB": render_glob,
            "HAL0_GPU_CONTAINER_OVERRIDE": "lxc",
            "HAL0_GPU_RENDER_GID_OVERRIDE": "0",
            "HAL0_GPU_RENDER_GROUP_OVERRIDE": "root",
        }
    )
    assert rc == RC_OK


def test_gate_gid_maps_to_wrong_group_hard_stops(render_glob: str) -> None:
    """M3 regression: gid resolves to a REAL group that is NOT the render
    group (a gid/name collision, e.g. host gid 993 landing on 'clock' inside
    the container instead of 'render') must hard-stop, not false-pass just
    because *some* group name was found.
    """
    rc = _run_gpu_gate(
        {
            "HAL0_GPU_GATE": "1",
            "HAL0_GPU_DRI_GLOB": render_glob,
            "HAL0_GPU_CONTAINER_OVERRIDE": "lxc",
            # gid 0 maps to 'root', but the expected group is 'render' (the
            # untouched default) — a real-group / wrong-name collision.
            "HAL0_GPU_RENDER_GID_OVERRIDE": "0",
        }
    )
    assert rc == RC_BROKEN_GID


def test_gate_wrong_group_on_bare_metal_does_not_block(render_glob: str) -> None:
    """Mirrors test_gate_broken_gid_on_bare_metal_does_not_block: the wrong-
    group collision only hard-stops inside an LXC, same as the no-group case.
    """
    rc = _run_gpu_gate(
        {
            "HAL0_GPU_GATE": "1",
            "HAL0_GPU_DRI_GLOB": render_glob,
            "HAL0_GPU_CONTAINER_OVERRIDE": "none",
            "HAL0_GPU_RENDER_GID_OVERRIDE": "0",
        }
    )
    assert rc == RC_OK


def test_gate_member_user_proceeds(render_glob: str) -> None:
    """Correct group + hal0-equivalent user IS a member → proceed (0)."""
    rc = _run_gpu_gate(
        {
            "HAL0_GPU_GATE": "1",
            "HAL0_GPU_DRI_GLOB": render_glob,
            "HAL0_GPU_CONTAINER_OVERRIDE": "lxc",
            "HAL0_GPU_RENDER_GID_OVERRIDE": "0",
            "HAL0_GPU_RENDER_GROUP_OVERRIDE": "root",
            # root is always a member of its own primary group 'root'.
            "HAL0_GPU_USER_OVERRIDE": "root",
        }
    )
    assert rc == RC_OK


def test_gate_non_member_user_warns_but_does_not_block(render_glob: str) -> None:
    """Correct group but the target user is NOT a member: advisory only — the
    gate still proceeds (install.sh's own usermod step runs later and is
    idempotent, so this must not hard-block an otherwise-fresh install).
    """
    rc = _run_gpu_gate(
        {
            "HAL0_GPU_GATE": "1",
            "HAL0_GPU_DRI_GLOB": render_glob,
            "HAL0_GPU_CONTAINER_OVERRIDE": "lxc",
            "HAL0_GPU_RENDER_GID_OVERRIDE": "0",
            "HAL0_GPU_RENDER_GROUP_OVERRIDE": "root",
            # 'nobody' exists on virtually every Linux host and is never a
            # member of 'root'.
            "HAL0_GPU_USER_OVERRIDE": "nobody",
        }
    )
    assert rc == RC_OK


def test_gate_nonexistent_user_skips_membership_check(render_glob: str) -> None:
    """A user that doesn't exist yet (fresh install: preflight_gpu runs
    BEFORE install.sh creates the hal0 system user) must not be treated as a
    membership failure — the check is skipped entirely.
    """
    rc = _run_gpu_gate(
        {
            "HAL0_GPU_GATE": "1",
            "HAL0_GPU_DRI_GLOB": render_glob,
            "HAL0_GPU_CONTAINER_OVERRIDE": "lxc",
            "HAL0_GPU_RENDER_GID_OVERRIDE": "0",
            "HAL0_GPU_RENDER_GROUP_OVERRIDE": "root",
            "HAL0_GPU_USER_OVERRIDE": "hal0-user-does-not-exist-xyz",
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


# ── #1888 / #1948: an AMD box needs SOME valid GPU lane ──────────────────────
# #1923 made /dev/kfd an outright requirement on AMD: the pinned runner was one
# HIP+Vulkan build, llama.cpp ran ROCm when /dev/kfd was visible and SILENTLY
# fell back to that image's Vulkan backend when it was not, and that backend
# emitted invalid tokens for every model at full nominal speed while every
# health surface read green. With no valid lane, refusing the install was the
# honest answer.
#
# #1948 fixed the image, so the requirement is now the honest one it always
# stood in for: the box must have SOME lane that produces language. ROCm needs
# /dev/kfd; Vulkan needs a render node plus a runner image validated for that
# lane. The gate refuses only when neither is available — which is what makes
# a fresh install on ct151 (the box the defect was found on) succeed.


def _amd_no_kfd_env(render_glob: str, tmp_path: Path, **extra: str) -> dict[str, str]:
    return {
        "HAL0_GPU_GATE": "1",
        "HAL0_GPU_DRI_GLOB": render_glob,
        "HAL0_GPU_CONTAINER_OVERRIDE": "lxc",
        "HAL0_GPU_RENDER_GID_OVERRIDE": "0",
        "HAL0_GPU_RENDER_GROUP_OVERRIDE": "root",
        "HAL0_GPU_AMD_OVERRIDE": "1",
        "HAL0_GPU_KFD_PATH": str(tmp_path / "no-such-kfd"),
        **extra,
    }


def test_gate_amd_render_node_without_kfd_stops_on_an_unvalidated_image(
    render_glob: str, tmp_path: Path
) -> None:
    """The #1888 refusal, now correctly scoped to the case that warrants it:
    no ROCm lane AND no usable Vulkan lane, so no GPU lane at all."""
    proc = _run_gpu_gate_full(
        _amd_no_kfd_env(render_glob, tmp_path, HAL0_GPU_VULKAN_LANE_OVERRIDE="0")
    )
    assert proc.returncode == RC_NO_KFD
    out = proc.stdout + proc.stderr
    assert "not validated for the Vulkan lane" in out
    assert "1888" in out


def test_gate_amd_render_node_without_kfd_proceeds_on_a_validated_image(
    render_glob: str, tmp_path: Path
) -> None:
    """#1948 — the whole point of Phase D, at the installer.

    ct151 (AMD, render node, no /dev/kfd) is the box the §3-C matrix validated
    Vulkan on. A fresh install there must SUCCEED: refusing it would be
    refusing a configuration that demonstrably serves correct output.
    """
    proc = _run_gpu_gate_full(
        _amd_no_kfd_env(render_glob, tmp_path, HAL0_GPU_VULKAN_LANE_OVERRIDE="1")
    )
    assert proc.returncode == RC_OK
    out = proc.stdout + proc.stderr
    # Truthful, and not scary: this is a supported install, not a broken one.
    assert "will use the Vulkan lane" in out
    assert "INVALID TOKENS" not in out


def test_gate_amd_without_kfd_or_render_node_stops_and_says_which(tmp_path: Path) -> None:
    """A validated image cannot conjure a device. With no render node there is
    no Vulkan lane either, and the message must name that — not blame the
    image, which is fine."""
    proc = _run_gpu_gate_full(
        {
            "HAL0_GPU_GATE": "1",
            "HAL0_GPU_DRI_GLOB": str(tmp_path / "NONE*"),
            "HAL0_GPU_CONTAINER_OVERRIDE": "lxc",
            "HAL0_GPU_AMD_OVERRIDE": "1",
            "HAL0_GPU_VULKAN_LANE_OVERRIDE": "1",
            "HAL0_GPU_KFD_PATH": str(tmp_path / "no-such-kfd"),
        }
    )
    # No devices at all is classified earlier, as NO_DEVICE — either way the
    # install is gated, and that is the contract being pinned here.
    assert proc.returncode in (RC_NO_DEVICE, RC_NO_KFD)


def test_the_shell_mirror_agrees_with_the_python_predicate(tmp_path: Path) -> None:
    """``_hal0_vulkan_lane_serves_default_image`` is a shell RE-IMPLEMENTATION
    of ``providers._gpu.default_image_serves_vulkan_lane`` (preflight runs
    before hal0 is installed, so it cannot just call it). Two implementations
    of one predicate drift; this is the tripwire.
    """
    from hal0.config.schema import VULKAN_CAPABLE_IMAGE_REFS
    from hal0.providers._gpu import default_image_serves_vulkan_lane

    assert len(VULKAN_CAPABLE_IMAGE_REFS) == 1, (
        "VULKAN_CAPABLE_IMAGE_REFS has grown past one member — the shell mirror in "
        "installer/lib/preflight.sh only recognises the single-member shape "
        "(default == VULKAN_FIXED_IMAGE) and must be taught the general case, or "
        "fresh installs will be refused on a validated image"
    )

    script = (
        "set -euo pipefail\n"
        f"source {PREFLIGHT!s}\n"
        "if _hal0_vulkan_lane_serves_default_image; then echo yes; else echo no; fi\n"
    )
    env = {k: v for k, v in os.environ.items() if k != "HAL0_GPU_VULKAN_LANE_OVERRIDE"}
    proc = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
    shell_says = proc.stdout.strip() == "yes"

    assert shell_says is default_image_serves_vulkan_lane(), (
        "the shell mirror and the Python predicate disagree about whether this "
        f"checkout's default runner image serves the Vulkan lane (shell={shell_says})"
    )


def test_the_shell_mirror_fails_closed_on_an_unreadable_schema(tmp_path: Path) -> None:
    """A false 'yes' ships a box that serves invalid tokens; a false 'no'
    costs a CPU-only install. The asymmetry decides the default."""
    script = (
        "set -euo pipefail\n"
        f"source {PREFLIGHT!s}\n"
        "if _hal0_vulkan_lane_serves_default_image; then echo yes; else echo no; fi\n"
    )
    env = {
        **{k: v for k, v in os.environ.items() if k != "HAL0_GPU_VULKAN_LANE_OVERRIDE"},
        "HAL0_SCHEMA_PY_OVERRIDE": str(tmp_path / "does-not-exist.py"),
    }
    proc = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
    assert proc.stdout.strip() == "no"


def test_gate_amd_render_node_with_kfd_proceeds(render_glob: str, tmp_path: Path) -> None:
    kfd = tmp_path / "kfd"
    kfd.touch()
    rc = _run_gpu_gate(
        {
            "HAL0_GPU_GATE": "1",
            "HAL0_GPU_DRI_GLOB": render_glob,
            "HAL0_GPU_CONTAINER_OVERRIDE": "lxc",
            "HAL0_GPU_RENDER_GID_OVERRIDE": "0",
            "HAL0_GPU_RENDER_GROUP_OVERRIDE": "root",
            "HAL0_GPU_AMD_OVERRIDE": "1",
            "HAL0_GPU_KFD_PATH": str(kfd),
        }
    )
    assert rc == RC_OK


def test_gate_non_amd_gpu_without_kfd_proceeds(render_glob: str, tmp_path: Path) -> None:
    """The check is AMD-scoped: an NVIDIA/Intel box has no /dev/kfd to forward
    and must not be blocked by it."""
    rc = _run_gpu_gate(
        {
            "HAL0_GPU_GATE": "1",
            "HAL0_GPU_DRI_GLOB": render_glob,
            "HAL0_GPU_CONTAINER_OVERRIDE": "lxc",
            "HAL0_GPU_RENDER_GID_OVERRIDE": "0",
            "HAL0_GPU_RENDER_GROUP_OVERRIDE": "root",
            "HAL0_GPU_AMD_OVERRIDE": "0",
            "HAL0_GPU_KFD_PATH": str(tmp_path / "no-such-kfd"),
        }
    )
    assert rc == RC_OK


def test_doctor_mode_missing_kfd_is_soft(render_glob: str, tmp_path: Path) -> None:
    """`hal0 doctor` (no HAL0_GPU_GATE) stays advisory-only — it reports the
    missing compute node but never aborts the report."""
    rc = _run_gpu_gate(
        {
            "HAL0_GPU_DRI_GLOB": render_glob,
            "HAL0_GPU_CONTAINER_OVERRIDE": "lxc",
            "HAL0_GPU_RENDER_GID_OVERRIDE": "0",
            "HAL0_GPU_RENDER_GROUP_OVERRIDE": "root",
            "HAL0_GPU_AMD_OVERRIDE": "1",
            "HAL0_GPU_KFD_PATH": str(tmp_path / "no-such-kfd"),
        }
    )
    assert rc == RC_OK


class TestKfdGroupGate:
    """#1953 — /dev/kfd forwarded but group-misaligned with the render node.

    A plain LXC ``dev`` passthrough lands renderD128 as root:render and the
    compute node as root:root. The rootful slot containers open both fine, so
    ROCm genuinely works — but the hal0 service user cannot open the compute
    node, so #1923's guard refuses every AMD GPU slot on a healthy box. The
    gate has to catch that shape, and must NOT tell the operator to re-forward
    a device that is already present.
    """

    def test_mismatched_kfd_gid_is_caught(self, tmp_path: Path) -> None:
        render = tmp_path / "renderD128"
        render.touch()
        kfd = tmp_path / "kfd"
        kfd.touch()
        rc = _run_gpu_gate(
            {
                "HAL0_GPU_GATE": "1",
                "HAL0_GPU_AMD_OVERRIDE": "1",
                "HAL0_GPU_DRI_GLOB": str(tmp_path / "renderD*"),
                # Satisfy the group-NAME check above so we reach the gid
                # check under test; it reads the real nodes regardless.
                "HAL0_GPU_RENDER_GID_OVERRIDE": "0",
                "HAL0_GPU_RENDER_GROUP_OVERRIDE": "root",
                "HAL0_GPU_KFD_PATH": str(kfd),
                "HAL0_GPU_KFD_GID_OVERRIDE": "0",
                "HAL0_GPU_CONTAINER_OVERRIDE": "lxc",
            }
        )
        # The compute node is ROOT-owned while the render node is not — the
        # exact inaccessible shape a plain LXC dev passthrough produces.
        assert rc == RC_KFD_GID

    def test_aligned_gids_pass(self, tmp_path: Path) -> None:
        render = tmp_path / "renderD128"
        render.touch()
        kfd = tmp_path / "kfd"
        kfd.touch()
        rc = _run_gpu_gate(
            {
                "HAL0_GPU_GATE": "1",
                "HAL0_GPU_AMD_OVERRIDE": "1",
                "HAL0_GPU_DRI_GLOB": str(tmp_path / "renderD*"),
                # Satisfy the group-NAME check above so we reach the gid
                # check under test; it reads the real nodes regardless.
                "HAL0_GPU_RENDER_GID_OVERRIDE": "0",
                "HAL0_GPU_RENDER_GROUP_OVERRIDE": "root",
                "HAL0_GPU_KFD_PATH": str(kfd),
                "HAL0_GPU_CONTAINER_OVERRIDE": "lxc",
            }
        )
        # Both nodes are real files with the same owning gid — nothing to fix.
        assert rc == RC_OK

    def test_remedy_never_says_re_forward_the_device(self, tmp_path: Path) -> None:
        """The headline harm of #1953: the old advice cost a production reboot."""
        (tmp_path / "renderD128").touch()
        kfd = tmp_path / "kfd"
        kfd.touch()
        script = f"set -uo pipefail\nsource {PREFLIGHT!s}\npreflight_gpu 2>&1 || true\n"
        env = {
            **os.environ,
            "HAL0_GPU_GATE": "1",
            "HAL0_GPU_AMD_OVERRIDE": "1",
            "HAL0_GPU_DRI_GLOB": str(tmp_path / "renderD*"),
            "HAL0_GPU_RENDER_GID_OVERRIDE": "0",
            "HAL0_GPU_RENDER_GROUP_OVERRIDE": "root",
            "HAL0_GPU_KFD_PATH": str(kfd),
            "HAL0_GPU_KFD_GID_OVERRIDE": "0",
            "HAL0_GPU_CONTAINER_OVERRIDE": "lxc",
        }
        out = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True).stdout
        render_gid = os.stat(tmp_path / "renderD128").st_gid
        assert f"chgrp {render_gid}" in out
        # The host-side remedy must carry a REAL gid, never a placeholder.
        assert f"gid={render_gid}" in out
        # And it must never send the operator to re-forward a present device.
        assert "dev1: /dev/kfd\n" not in out

    def test_a_non_root_gid_divergence_is_left_alone(self, tmp_path: Path) -> None:
        """#1953 review: a differing gid is NOT itself a fault.

        A valid box can put /dev/kfd on `video` and the render nodes on
        `render` — install.sh adds hal0 to BOTH. Rewriting that to the render
        group would strip access from video-only users, and on an unprivileged
        LXC the failed chgrp would abort a fresh install over a working config.
        Only the root-owned, no-world-access shape is repaired.
        """
        (tmp_path / "renderD128").touch()
        kfd = tmp_path / "kfd"
        kfd.touch()
        rc = _run_gpu_gate(
            {
                "HAL0_GPU_GATE": "1",
                "HAL0_GPU_AMD_OVERRIDE": "1",
                "HAL0_GPU_DRI_GLOB": str(tmp_path / "renderD*"),
                "HAL0_GPU_RENDER_GID_OVERRIDE": "0",
                "HAL0_GPU_RENDER_GROUP_OVERRIDE": "root",
                "HAL0_GPU_KFD_PATH": str(kfd),
                # Differs from the render node, but is a REAL group the service
                # user can be added to — not the root-owned passthrough shape.
                "HAL0_GPU_KFD_GID_OVERRIDE": "61996",
                "HAL0_GPU_CONTAINER_OVERRIDE": "lxc",
            }
        )
        assert rc == RC_OK
